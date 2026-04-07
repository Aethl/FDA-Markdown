"""
Main pipeline orchestrator — runs the full fetch → extract → structure → commit cycle.

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
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / ".raw"
SCRIPTS = ROOT / "scripts"


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


def pipeline_510k(incremental: bool = True, product_codes: str = ""):
    """Run the 510(k) pipeline: fetch → extract → structure."""
    if not product_codes:
        print("  Skipping 510(k) pipeline — no product codes specified")
        return

    print("\n" + "="*60)
    print("  510(k) PIPELINE")
    print("="*60)

    # Step 1: Fetch from openFDA
    fetch_cmd = [sys.executable, str(SCRIPTS / "fetch_510k.py"),
                 "--product-codes", product_codes]
    if incremental:
        fetch_cmd.append("--incremental")
    run_cmd(fetch_cmd, "Fetching 510(k) records from openFDA")

    # Step 2: Extract text from downloaded PDFs
    for code_dir in sorted((RAW_DIR / "510k").glob("*")):
        if not code_dir.is_dir():
            continue
        pdfs = list(code_dir.glob("*.pdf"))
        new_pdfs = [p for p in pdfs if not (code_dir / f"{p.stem}.txt").exists()]
        if new_pdfs:
            run_cmd(
                [sys.executable, str(SCRIPTS / "extract_pdf.py"),
                 "--batch", str(code_dir)],
                f"Extracting PDFs in {code_dir.name}"
            )

    # Step 3: Structure with LLM, committing every BATCH_SIZE docs
    BATCH_SIZE = 25
    structured_count = 0

    for code_dir in sorted((RAW_DIR / "510k").glob("*")):
        if not code_dir.is_dir():
            continue
        product_code = code_dir.name
        output_dir = ROOT / "510k-summaries" / product_code
        output_dir.mkdir(parents=True, exist_ok=True)

        txts = list(code_dir.glob("*.txt"))
        new_txts = [t for t in txts if not (output_dir / f"{t.stem}.md").exists()]

        for txt_path in sorted(new_txts):
            meta_path = code_dir / f"{txt_path.stem}_meta.json"
            meta_arg = ["--meta", str(meta_path)] if meta_path.exists() else []

            run_cmd(
                [sys.executable, str(SCRIPTS / "structure_md.py"),
                 "--input", str(txt_path),
                 "--doc-type", "510k",
                 "--output-dir", str(output_dir)] + meta_arg,
                f"Structuring {txt_path.stem} ({product_code})"
            )
            structured_count += 1

            if structured_count % BATCH_SIZE == 0:
                git_commit_progress(
                    f"Add {structured_count} 510(k) summaries (batch checkpoint)"
                )

    if structured_count > 0:
        git_commit_progress(
            f"Add 510(k) summaries (total: {structured_count} new this run)"
        )


def pipeline_guidances(incremental: bool = True, centers: str | None = None,
                       limit: int | None = None):
    """Run the guidance document pipeline."""
    print("\n" + "="*60)
    print("  GUIDANCE DOCUMENT PIPELINE")
    print("="*60)

    fetch_script = SCRIPTS / "fetch_guidances.py"
    if not fetch_script.exists():
        print("  fetch_guidances.py not found — skipping")
        return

    # Step 1: Fetch guidance index + PDFs
    fetch_cmd = [sys.executable, str(fetch_script)]
    if incremental:
        fetch_cmd.append("--incremental")
    if centers:
        fetch_cmd.extend(["--centers"] + centers.split())
    if limit:
        fetch_cmd.extend(["--limit", str(limit)])
    run_cmd(fetch_cmd, "Fetching guidance documents from FDA")

    # Step 2: Extract and structure
    guidance_raw = RAW_DIR / "guidances"
    if not guidance_raw.exists():
        print("  No raw guidance PDFs found — skipping extract/structure")
        return

    # Step 2: Extract text from all downloaded PDFs
    for center_dir in sorted(guidance_raw.glob("*")):
        if not center_dir.is_dir():
            continue

        pdfs = list(center_dir.glob("*.pdf"))
        new_pdfs = [p for p in pdfs if not (center_dir / f"{p.stem}.txt").exists()]
        if new_pdfs:
            run_cmd(
                [sys.executable, str(SCRIPTS / "extract_pdf.py"),
                 "--batch", str(center_dir)],
                f"Extracting guidance PDFs for {center_dir.name}"
            )

    # Step 3: Structure with LLM, committing every BATCH_SIZE docs
    BATCH_SIZE = 25
    structured_count = 0

    for center_dir in sorted(guidance_raw.glob("*")):
        if not center_dir.is_dir():
            continue

        output_dir = ROOT / "guidances" / center_dir.name
        output_dir.mkdir(parents=True, exist_ok=True)

        txts = list(center_dir.glob("*.txt"))
        new_txts = [t for t in txts if not (output_dir / f"{t.stem}.md").exists()]

        for txt_path in sorted(new_txts):
            meta_path = center_dir / f"{txt_path.stem}_meta.json"
            meta_arg = ["--meta", str(meta_path)] if meta_path.exists() else []

            run_cmd(
                [sys.executable, str(SCRIPTS / "structure_md.py"),
                 "--input", str(txt_path),
                 "--doc-type", "guidance",
                 "--output-dir", str(output_dir)] + meta_arg,
                f"Structuring {txt_path.stem} ({center_dir.name})"
            )
            structured_count += 1

            if structured_count % BATCH_SIZE == 0:
                git_commit_progress(
                    f"Add {structured_count} guidance docs (batch checkpoint)"
                )

    # Final commit for any remaining docs
    if structured_count > 0:
        git_commit_progress(
            f"Add guidance docs (total: {structured_count} new this run)"
        )


def pipeline_pma(incremental: bool = True, product_codes: str = ""):
    """Run the PMA summary pipeline."""
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

    fetch_cmd = [sys.executable, str(fetch_script),
                 "--product-codes", product_codes]
    if incremental:
        fetch_cmd.append("--incremental")
    run_cmd(fetch_cmd, "Fetching PMA records from openFDA")


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
