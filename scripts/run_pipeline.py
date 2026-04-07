"""
Main pipeline orchestrator — runs the full fetch → extract → structure → commit cycle.

Each document is processed end-to-end (download PDF → extract text → structure with
LLM) before moving to the next. This means interrupted runs only redo work for the
single document that was in progress, not the entire corpus.

This is what GitHub Actions calls daily.

Usage:
    python scripts/run_pipeline.py --only guidance --guidance-limit 50
    python scripts/run_pipeline.py --only 510k --product-codes QMT,FRO
    python scripts/run_pipeline.py                    # Full run (requires --product-codes)
    python scripts/run_pipeline.py --full-rebuild      # Ignore state, reprocess everything
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / ".raw"
SCRIPTS = ROOT / "scripts"

# Import processing functions directly to avoid subprocess overhead per document
sys.path.insert(0, str(SCRIPTS))

BATCH_SIZE = 25

# Lazy-loaded processing functions (avoids import errors when deps aren't installed locally)
_extract_text = None
_process_single = None


def get_extract_text():
    global _extract_text
    if _extract_text is None:
        from extract_pdf import extract_text
        _extract_text = extract_text
    return _extract_text


def get_process_single():
    global _process_single
    if _process_single is None:
        from structure_md import process_single
        _process_single = process_single
    return _process_single


def git_commit_progress(message: str):
    """Commit any new/changed files in the repo so partial progress is saved."""
    print(f"\n  [GIT] Committing progress: {message}", flush=True)
    # Stage output directories and index
    subprocess.run(
        ["git", "add", "guidances/", "510k-summaries/", "pma-summaries/", "index.json", "docs/"],
        cwd=str(ROOT)
    )
    # Check if there's anything to commit
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(ROOT)
    )
    if result.returncode != 0:
        # There are staged changes
        commit_result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(ROOT)
        )
        if commit_result.returncode == 0:
            push_result = subprocess.run(
                ["git", "push"],
                cwd=str(ROOT)
            )
            if push_result.returncode == 0:
                print(f"  [GIT] Committed and pushed.", flush=True)
            else:
                print(f"  [GIT] Push failed (code {push_result.returncode})", flush=True)
        else:
            print(f"  [GIT] Commit failed (code {commit_result.returncode})", flush=True)
    else:
        print(f"  [GIT] Nothing new to commit.", flush=True)


def run_cmd(cmd: list[str], description: str) -> bool:
    """Run a subprocess command with logging."""
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"  CMD: {' '.join(cmd)}")
    print(f"{'='*60}")

    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=False)
    if result.returncode != 0:
        print(f"  WARNING: Command exited with code {result.returncode}")
        return False
    return True


def process_doc_end_to_end(
    doc_id: str,
    raw_dir: Path,
    output_dir: Path,
    doc_type: str,
    download_fn,
    meta_path: Path | None = None,
) -> bool:
    """
    Process a single document end-to-end: download PDF → extract text → structure.

    Returns True if a new .md file was produced.
    """
    output_md = output_dir / f"{doc_id}.md"
    if output_md.exists():
        return False  # already done

    pdf_path = raw_dir / f"{doc_id}.pdf"
    txt_path = raw_dir / f"{doc_id}.txt"

    # Step 1: Download PDF if needed
    if not pdf_path.exists() and not txt_path.exists():
        print(f"\n  [{doc_type.upper()}] Downloading: {doc_id}")
        if download_fn:
            download_fn(doc_id, pdf_path)
            time.sleep(0.5)  # Be polite to FDA servers

    # Step 2: Extract text if needed
    if not txt_path.exists() and pdf_path.exists():
        text = get_extract_text()(pdf_path)
        if text:
            txt_path.write_text(text, encoding="utf-8")
        else:
            print(f"    Could not extract text from {doc_id}")
            return False

    # Step 3: Structure with LLM
    if not txt_path.exists():
        print(f"    No text available for {doc_id}, skipping")
        return False

    output_dir.mkdir(parents=True, exist_ok=True)
    result = get_process_single()(txt_path, doc_type, meta_path, output_dir)
    return result is not None


# ── 510(k) Pipeline ─────────────────────────────────────────────────────────

def pipeline_510k(incremental: bool = True, product_codes: str = ""):
    """Run the 510(k) pipeline: fetch metadata → process each doc end-to-end."""
    if not product_codes:
        print("  Skipping 510(k) pipeline — no product codes specified")
        return

    print("\n" + "="*60)
    print("  510(k) PIPELINE")
    print("="*60)

    # Step 1: Fetch metadata only (no PDF downloads)
    fetch_cmd = [sys.executable, str(SCRIPTS / "fetch_510k.py"),
                 "--product-codes", product_codes, "--skip-pdf"]
    if incremental:
        fetch_cmd.append("--incremental")
    run_cmd(fetch_cmd, "Fetching 510(k) metadata from openFDA")

    # Lazy-import download function
    from fetch_510k import download_summary_pdf

    # Step 2: Process each document end-to-end
    structured_count = 0
    raw_510k = RAW_DIR / "510k"
    if not raw_510k.exists():
        return

    for code_dir in sorted(raw_510k.glob("*")):
        if not code_dir.is_dir():
            continue
        product_code = code_dir.name
        output_dir = ROOT / "510k-summaries" / product_code

        # Find all docs that have metadata but no .md output yet
        for meta_file in sorted(code_dir.glob("*_meta.json")):
            doc_id = meta_file.stem.replace("_meta", "")
            if (output_dir / f"{doc_id}.md").exists():
                continue

            success = process_doc_end_to_end(
                doc_id=doc_id,
                raw_dir=code_dir,
                output_dir=output_dir,
                doc_type="510k",
                download_fn=download_summary_pdf,
                meta_path=meta_file,
            )
            if success:
                structured_count += 1
                if structured_count % BATCH_SIZE == 0:
                    git_commit_progress(
                        f"Add {structured_count} 510(k) summaries (batch checkpoint)"
                    )

    if structured_count > 0:
        git_commit_progress(
            f"Add 510(k) summaries (total: {structured_count} new this run)"
        )


# ── Guidance Pipeline ────────────────────────────────────────────────────────

def pipeline_guidances(incremental: bool = True, centers: str | None = None,
                       limit: int | None = None):
    """Run the guidance pipeline: fetch metadata + PDFs → process each doc end-to-end."""
    print("\n" + "="*60)
    print("  GUIDANCE DOCUMENT PIPELINE")
    print("="*60)

    fetch_script = SCRIPTS / "fetch_guidances.py"
    if not fetch_script.exists():
        print("  fetch_guidances.py not found — skipping")
        return

    # Step 1: Fetch guidance index + download PDFs
    # Guidance PDFs come from the FDA website (not openFDA), and the fetch script
    # handles skip-already-done logic. We still download PDFs here because the
    # guidance fetch script filters and selects which PDFs to grab based on
    # center/status/limit args. The per-doc loop below handles extract + structure.
    fetch_cmd = [sys.executable, str(fetch_script)]
    if incremental:
        fetch_cmd.append("--incremental")
    if centers:
        fetch_cmd.extend(["--centers"] + centers.split())
    if limit:
        fetch_cmd.extend(["--limit", str(limit)])
    run_cmd(fetch_cmd, "Fetching guidance documents from FDA")

    # Step 2: Process each document end-to-end (extract + structure)
    guidance_raw = RAW_DIR / "guidances"
    if not guidance_raw.exists():
        print("  No raw guidance data found — skipping")
        return

    structured_count = 0

    for center_dir in sorted(guidance_raw.glob("*")):
        if not center_dir.is_dir():
            continue
        center = center_dir.name
        output_dir = ROOT / "guidances" / center

        for meta_file in sorted(center_dir.glob("*_meta.json")):
            slug = meta_file.stem.replace("_meta", "")
            if (output_dir / f"{slug}.md").exists():
                continue

            pdf_path = center_dir / f"{slug}.pdf"
            txt_path = center_dir / f"{slug}.txt"

            # Extract text if needed (PDF was already downloaded by fetch step)
            if not txt_path.exists() and pdf_path.exists():
                text = get_extract_text()(pdf_path)
                if text:
                    txt_path.write_text(text, encoding="utf-8")
                else:
                    print(f"    Could not extract text from {slug}")
                    continue

            if not txt_path.exists():
                continue

            # Structure with LLM
            output_dir.mkdir(parents=True, exist_ok=True)
            result = get_process_single()(txt_path, "guidance", meta_file, output_dir)
            if result:
                structured_count += 1
                if structured_count % BATCH_SIZE == 0:
                    git_commit_progress(
                        f"Add {structured_count} guidance docs (batch checkpoint)"
                    )

    if structured_count > 0:
        git_commit_progress(
            f"Add guidance docs (total: {structured_count} new this run)"
        )


# ── PMA Pipeline ─────────────────────────────────────────────────────────────

def pipeline_pma(incremental: bool = True, product_codes: str = ""):
    """Run the PMA pipeline: fetch metadata → process each doc end-to-end."""
    if not product_codes:
        print("  Skipping PMA pipeline — no product codes specified")
        return

    print("\n" + "="*60)
    print("  PMA SUMMARY PIPELINE")
    print("="*60)

    fetch_script = SCRIPTS / "fetch_pma.py"
    if not fetch_script.exists():
        print("  fetch_pma.py not found — skipping")
        return

    # Step 1: Fetch metadata only (no PDF downloads)
    fetch_cmd = [sys.executable, str(fetch_script),
                 "--product-codes", product_codes, "--skip-pdf"]
    if incremental:
        fetch_cmd.append("--incremental")
    run_cmd(fetch_cmd, "Fetching PMA metadata from openFDA")

    # Lazy-import download function
    from fetch_pma import download_ssed_pdf

    # Step 2: Process each document end-to-end
    structured_count = 0
    raw_pma = RAW_DIR / "pma"
    if not raw_pma.exists():
        return

    for code_dir in sorted(raw_pma.glob("*")):
        if not code_dir.is_dir():
            continue
        product_code = code_dir.name
        output_dir = ROOT / "pma-summaries" / product_code

        for meta_file in sorted(code_dir.glob("*_meta.json")):
            doc_id = meta_file.stem.replace("_meta", "")
            if (output_dir / f"{doc_id}.md").exists():
                continue

            success = process_doc_end_to_end(
                doc_id=doc_id,
                raw_dir=code_dir,
                output_dir=output_dir,
                doc_type="pma",
                download_fn=download_ssed_pdf,
                meta_path=meta_file,
            )
            if success:
                structured_count += 1
                if structured_count % BATCH_SIZE == 0:
                    git_commit_progress(
                        f"Add {structured_count} PMA summaries (batch checkpoint)"
                    )

    if structured_count > 0:
        git_commit_progress(
            f"Add PMA summaries (total: {structured_count} new this run)"
        )


# ── Index Builder ────────────────────────────────────────────────────────────

def update_index():
    """Rebuild the master index.json."""
    print("\n" + "="*60)
    print("  REBUILDING INDEX")
    print("="*60)

    index = {
        "last_updated": datetime.utcnow().isoformat(),
        "documents": {
            "510k_summaries": [],
            "pma_summaries": [],
            "guidances": [],
        }
    }

    # Index 510(k) summaries
    for md_file in sorted((ROOT / "510k-summaries").rglob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        if content.startswith("---"):
            try:
                import yaml
                _, fm, _ = content.split("---", 2)
                meta = yaml.safe_load(fm)
                index["documents"]["510k_summaries"].append({
                    "file": str(md_file.relative_to(ROOT)).replace("\\", "/"),
                    "document_number": meta.get("document_number", ""),
                    "title": meta.get("title", ""),
                    "product_code": meta.get("product_code", ""),
                    "decision_date": meta.get("decision_date", ""),
                    "decision": meta.get("decision", ""),
                    "applicant": meta.get("applicant", ""),
                    "predicate_devices": meta.get("predicate_devices", []),
                    "source_url": meta.get("source_url", ""),
                })
            except Exception:
                index["documents"]["510k_summaries"].append({
                    "file": str(md_file.relative_to(ROOT)).replace("\\", "/"),
                })

    # Index guidances
    for md_file in sorted((ROOT / "guidances").rglob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        entry = {"file": str(md_file.relative_to(ROOT)).replace("\\", "/")}
        if content.startswith("---"):
            try:
                import yaml
                _, fm, _ = content.split("---", 2)
                meta = yaml.safe_load(fm)
                entry.update({
                    "title": meta.get("title", ""),
                    "document_number": meta.get("document_number", ""),
                    "fda_center": meta.get("fda_center", ""),
                    "status": meta.get("status", ""),
                    "date_issued": meta.get("date_issued", ""),
                    "topics": meta.get("topics", []),
                    "docket_number": meta.get("docket_number", ""),
                    "regulated_product": meta.get("regulated_product", ""),
                    "source_url": meta.get("source_url", ""),
                })
            except Exception:
                pass
        index["documents"]["guidances"].append(entry)

    # Index PMA summaries
    for md_file in sorted((ROOT / "pma-summaries").rglob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        entry = {"file": str(md_file.relative_to(ROOT)).replace("\\", "/")}
        if content.startswith("---"):
            try:
                import yaml
                _, fm, _ = content.split("---", 2)
                meta = yaml.safe_load(fm)
                entry.update({
                    "document_number": meta.get("document_number", ""),
                    "title": meta.get("title", ""),
                    "applicant": meta.get("applicant", ""),
                    "product_code": meta.get("product_code", ""),
                    "decision_date": meta.get("decision_date", ""),
                    "decision": meta.get("decision", ""),
                    "supplement_number": meta.get("supplement_number", ""),
                    "source_url": meta.get("source_url", ""),
                })
            except Exception:
                pass
        index["documents"]["pma_summaries"].append(entry)

    index_path = ROOT / "index.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2, default=str)

    # Copy to docs/ for GitHub Pages
    docs_index = ROOT / "docs" / "index.json"
    if docs_index.parent.exists():
        import shutil
        shutil.copy2(index_path, docs_index)

    total = sum(len(v) for v in index["documents"].values())
    print(f"  Index rebuilt: {total} documents")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FDA Open Regulatory Pipeline")
    parser.add_argument("--only", type=str, default=None,
                        choices=["510k", "guidance", "pma"],
                        help="Run only one pipeline")
    parser.add_argument("--full-rebuild", action="store_true",
                        help="Ignore state, reprocess everything")
    parser.add_argument("--product-codes", type=str, default="",
                        help="Comma-separated FDA product codes (required for 510k/pma)")
    parser.add_argument("--guidance-centers", type=str, default=None,
                        help="Space-separated FDA center codes for guidance filtering")
    parser.add_argument("--guidance-limit", type=int, default=None,
                        help="Max number of guidances to process")
    parser.add_argument("--skip-index", action="store_true",
                        help="Skip index rebuild")
    args = parser.parse_args()

    incremental = not args.full_rebuild

    print(f"FDA Open Regulatory Pipeline — {datetime.utcnow().isoformat()}")
    print(f"Mode: {'incremental' if incremental else 'full rebuild'}")

    if args.only is None or args.only == "510k":
        pipeline_510k(incremental=incremental, product_codes=args.product_codes)

    if args.only is None or args.only == "guidance":
        pipeline_guidances(incremental=incremental, centers=args.guidance_centers,
                           limit=args.guidance_limit)

    if args.only is None or args.only == "pma":
        pipeline_pma(incremental=incremental, product_codes=args.product_codes)

    if not args.skip_index:
        update_index()

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
