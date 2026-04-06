"""
Main pipeline orchestrator — runs the full fetch → extract → structure → commit cycle.

This is what GitHub Actions calls daily.

Usage:
    python scripts/run_pipeline.py                    # Full run, all doc types
    python scripts/run_pipeline.py --only 510k        # Only 510(k)s
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
        # Only extract PDFs that don't already have a .txt
        new_pdfs = [p for p in pdfs if not (code_dir / f"{p.stem}.txt").exists()]
        if new_pdfs:
            run_cmd(
                [sys.executable, str(SCRIPTS / "extract_pdf.py"),
                 "--batch", str(code_dir)],
                f"Extracting PDFs in {code_dir.name}"
            )

    # Step 3: Structure with LLM
    for code_dir in sorted((RAW_DIR / "510k").glob("*")):
        if not code_dir.is_dir():
            continue
        product_code = code_dir.name
        output_dir = ROOT / "510k-summaries" / product_code
        output_dir.mkdir(parents=True, exist_ok=True)

        txts = list(code_dir.glob("*.txt"))
        # Only structure .txt files that don't already have a .md output
        new_txts = [t for t in txts if not (output_dir / f"{t.stem}.md").exists()]
        if new_txts:
            run_cmd(
                [sys.executable, str(SCRIPTS / "structure_md.py"),
                 "--batch", str(code_dir),
                 "--doc-type", "510k",
                 "--output-dir", str(output_dir)],
                f"Structuring 510(k) summaries for {product_code}"
            )


def pipeline_guidances(incremental: bool = True):
    """Run the guidance document pipeline."""
    print("\n" + "="*60)
    print("  GUIDANCE DOCUMENT PIPELINE")
    print("="*60)

    # Fetch guidances
    fetch_script = SCRIPTS / "fetch_guidances.py"
    if fetch_script.exists():
        fetch_cmd = [sys.executable, str(fetch_script)]
        if incremental:
            fetch_cmd.append("--incremental")
        run_cmd(fetch_cmd, "Fetching guidance documents from FDA")

        # Extract and structure
        guidance_raw = RAW_DIR / "guidances"
        if guidance_raw.exists():
            for center_dir in sorted(guidance_raw.glob("*")):
                if not center_dir.is_dir():
                    continue
                output_dir = ROOT / "guidances" / center_dir.name
                output_dir.mkdir(parents=True, exist_ok=True)

                run_cmd(
                    [sys.executable, str(SCRIPTS / "extract_pdf.py"),
                     "--batch", str(center_dir)],
                    f"Extracting guidance PDFs for {center_dir.name}"
                )
                run_cmd(
                    [sys.executable, str(SCRIPTS / "structure_md.py"),
                     "--batch", str(center_dir),
                     "--doc-type", "guidance",
                     "--output-dir", str(output_dir)],
                    f"Structuring guidances for {center_dir.name}"
                )
    else:
        print("  fetch_guidances.py not yet implemented — skipping")


def pipeline_pma(incremental: bool = True):
    """Run the PMA summary pipeline."""
    print("\n" + "="*60)
    print("  PMA SUMMARY PIPELINE")
    print("="*60)

    fetch_script = SCRIPTS / "fetch_pma.py"
    if fetch_script.exists():
        fetch_cmd = [sys.executable, str(fetch_script)]
        if incremental:
            fetch_cmd.append("--incremental")
        run_cmd(fetch_cmd, "Fetching PMA records from openFDA")
    else:
        print("  fetch_pma.py not yet implemented — skipping")


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
        # Try to parse frontmatter
        if content.startswith("---"):
            try:
                import yaml
                _, fm, _ = content.split("---", 2)
                meta = yaml.safe_load(fm)
                index["documents"]["510k_summaries"].append({
                    "file": str(md_file.relative_to(ROOT)),
                    "document_number": meta.get("document_number", ""),
                    "title": meta.get("title", ""),
                    "product_code": meta.get("product_code", ""),
                    "decision_date": meta.get("decision_date", ""),
                    "applicant": meta.get("applicant", ""),
                })
            except Exception:
                index["documents"]["510k_summaries"].append({
                    "file": str(md_file.relative_to(ROOT)),
                })

    # Index guidances
    for md_file in sorted((ROOT / "guidances").rglob("*.md")):
        index["documents"]["guidances"].append({
            "file": str(md_file.relative_to(ROOT)),
        })

    # Index PMA summaries
    for md_file in sorted((ROOT / "pma-summaries").rglob("*.md")):
        index["documents"]["pma_summaries"].append({
            "file": str(md_file.relative_to(ROOT)),
        })

    index_path = ROOT / "index.json"
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2, default=str)

    total = sum(len(v) for v in index["documents"].values())
    print(f"  Index rebuilt: {total} documents")


def main():
    parser = argparse.ArgumentParser(description="FDA Open Regulatory Pipeline")
    parser.add_argument("--only", type=str, default=None,
                        choices=["510k", "guidance", "pma"],
                        help="Run only one pipeline")
    parser.add_argument("--full-rebuild", action="store_true",
                        help="Ignore state, reprocess everything")
    parser.add_argument("--product-codes", type=str, required=True,
                        help="Comma-separated FDA product codes for 510(k) pipeline")
    parser.add_argument("--skip-index", action="store_true",
                        help="Skip index rebuild")
    args = parser.parse_args()

    incremental = not args.full_rebuild

    print(f"FDA Open Regulatory Pipeline — {datetime.utcnow().isoformat()}")
    print(f"Mode: {'incremental' if incremental else 'full rebuild'}")

    if args.only is None or args.only == "510k":
        pipeline_510k(incremental=incremental, product_codes=args.product_codes)

    if args.only is None or args.only == "guidance":
        pipeline_guidances(incremental=incremental)

    if args.only is None or args.only == "pma":
        pipeline_pma(incremental=incremental)

    if not args.skip_index:
        update_index()

    print("\nPipeline complete.")


if __name__ == "__main__":
    main()
