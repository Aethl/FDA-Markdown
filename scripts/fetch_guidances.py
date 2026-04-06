"""
Fetch FDA guidance documents from the FDA guidance search JSON endpoint.

FDA publishes a complete JSON index of all guidance documents at:
    https://www.fda.gov/files/api/datatables/static/search-for-guidance.json

This script downloads that index, filters by center/topic/status, and
downloads the associated PDFs.

Usage:
    python scripts/fetch_guidances.py --centers CDRH CBER
    python scripts/fetch_guidances.py --topics "biocompatibility" "device"
    python scripts/fetch_guidances.py --status Final --incremental
    python scripts/fetch_guidances.py                 # all guidances
"""

import argparse
import html
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests

FDA_GUIDANCE_JSON_URL = "https://www.fda.gov/files/api/datatables/static/search-for-guidance.json"
FDA_BASE_URL = "https://www.fda.gov"

STATE_FILE = Path(__file__).parent.parent / ".pipeline_state.json"
RAW_DIR = Path(__file__).parent.parent / ".raw" / "guidances"
OUTPUT_DIR = Path(__file__).parent.parent / "guidances"

# Map FDA center long names to short codes for directory structure
CENTER_SHORT = {
    "Center for Devices and Radiological Health": "CDRH",
    "Center for Drug Evaluation and Research": "CDER",
    "Center for Biologics Evaluation and Research": "CBER",
    "Center for Tobacco Products": "CTP",
    "Center for Veterinary Medicine": "CVM",
    "Human Foods Program": "HFP",
    "Office of Regulatory Affairs": "ORA",
    "Office of Inspections and Investigations": "OII",
    "Office of the Commissioner": "OC",
}


def strip_html(text: str) -> str:
    """Remove HTML tags and decode entities."""
    return re.sub(r"<[^>]+>", "", html.unescape(text)).strip()


def extract_href(html_str: str) -> str | None:
    """Extract the first href URL from an HTML string."""
    match = re.search(r'href="([^"]+)"', html.unescape(html_str))
    return match.group(1) if match else None


def fetch_guidance_index() -> list[dict]:
    """Download and parse the FDA guidance JSON index."""
    print(f"Fetching guidance index from {FDA_GUIDANCE_JSON_URL}...")
    headers = {
        "User-Agent": "FDA-Markdown/1.0 (https://github.com/Aethl/FDA-Markdown; regulatory data pipeline)",
        "Accept": "application/json",
    }
    resp = requests.get(FDA_GUIDANCE_JSON_URL, headers=headers, timeout=60)
    resp.raise_for_status()
    raw_records = resp.json()
    print(f"  Total guidance records: {len(raw_records)}")

    parsed = []
    for record in raw_records:
        title = strip_html(record.get("title", ""))
        guidance_url = extract_href(record.get("title", ""))
        pdf_url = extract_href(record.get("field_associated_media_2", ""))
        docket_number = strip_html(record.get("field_docket_number", ""))
        center_long = record.get("field_center", "")
        center = CENTER_SHORT.get(center_long, center_long)
        status_raw = record.get("field_final_guidance_1", "").strip()
        status = status_raw.lower() if status_raw else ""
        topics_raw = record.get("topics-product", "")
        topics = [t.strip() for t in topics_raw.split(",") if t.strip()] if topics_raw else []
        issue_date = record.get("field_issue_datetime", "")
        comm_type = record.get("field_communication_type", "")
        regulated_product = record.get("field_regulated_product_field", "")

        # Parse changed timestamp
        changed_html = record.get("changed", "")
        changed_match = re.search(r'datetime="([^"]+)"', html.unescape(changed_html))
        changed = changed_match.group(1) if changed_match else ""

        # Normalize issue date to YYYY-MM-DD
        issue_date_normalized = ""
        if issue_date:
            try:
                issue_date_normalized = datetime.strptime(issue_date, "%m/%d/%Y").strftime("%Y-%m-%d")
            except ValueError:
                issue_date_normalized = issue_date

        # Build a slug for the filename from the title
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:80].strip("-")

        parsed.append({
            "title": title,
            "slug": slug,
            "guidance_url": f"{FDA_BASE_URL}{guidance_url}" if guidance_url and guidance_url.startswith("/") else guidance_url,
            "pdf_url": f"{FDA_BASE_URL}{pdf_url}" if pdf_url and pdf_url.startswith("/") else pdf_url,
            "docket_number": docket_number,
            "center": center,
            "status": status,
            "topics": topics,
            "issue_date": issue_date_normalized,
            "communication_type": comm_type,
            "regulated_product": strip_html(regulated_product),
            "changed": changed,
        })

    return parsed


def filter_guidances(records: list[dict],
                     centers: list[str] | None = None,
                     topics: list[str] | None = None,
                     status: str | None = None) -> list[dict]:
    """Filter guidance records by center, topic keywords, and/or status."""
    filtered = records

    if centers:
        centers_upper = [c.upper() for c in centers]
        filtered = [r for r in filtered if r["center"].upper() in centers_upper]

    if status:
        filtered = [r for r in filtered if r["status"] == status.lower()]

    if topics:
        topics_lower = [t.lower() for t in topics]
        def matches_topic(record):
            searchable = " ".join(record["topics"]).lower() + " " + record["title"].lower()
            return any(t in searchable for t in topics_lower)
        filtered = [r for r in filtered if matches_topic(r)]

    return filtered


def download_guidance_pdf(url: str, output_path: Path) -> bool:
    """Download a guidance PDF from FDA servers."""
    headers = {
        "User-Agent": "FDA-Markdown/1.0 (https://github.com/Aethl/FDA-Markdown; regulatory data pipeline)",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=60, allow_redirects=True)
        if resp.status_code == 200:
            content_type = resp.headers.get("content-type", "")
            if "pdf" in content_type or url.endswith("/download"):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(resp.content)
                print(f"  Downloaded: {output_path.name} ({len(resp.content) // 1024} KB)")
                return True
    except Exception as e:
        print(f"  Download error for {url}: {e}")
    return False


def build_metadata(record: dict) -> dict:
    """Build metadata dict for a guidance record."""
    return {
        "type": "guidance",
        "title": record["title"],
        "status": record["status"],
        "date_issued": record["issue_date"],
        "fda_center": record["center"],
        "docket_number": record["docket_number"],
        "communication_type": record["communication_type"],
        "topics": record["topics"],
        "regulated_product": record["regulated_product"],
        "source_url": record["guidance_url"] or "",
        "pdf_url": record["pdf_url"] or "",
        "last_updated": datetime.utcnow().strftime("%Y-%m-%d"),
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Fetch FDA guidance documents")
    parser.add_argument("--centers", nargs="+", default=None,
                        help="FDA center short codes to filter (e.g., CDRH CBER CDER)")
    parser.add_argument("--topics", nargs="+", default=None,
                        help="Topic keywords to filter by")
    parser.add_argument("--status", type=str, default=None, choices=["draft", "final", "Draft", "Final"],
                        help="Filter by guidance status")
    parser.add_argument("--incremental", action="store_true",
                        help="Only download PDFs not already fetched")
    parser.add_argument("--skip-pdf", action="store_true",
                        help="Save metadata only, skip PDF downloads")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max number of guidances to process")
    args = parser.parse_args()

    state = load_state()
    processed = set(state.get("processed_guidances", []))

    # Fetch the full index
    all_records = fetch_guidance_index()

    # Apply filters
    records = filter_guidances(all_records, centers=args.centers,
                               topics=args.topics, status=args.status)
    print(f"After filtering: {len(records)} guidance records")

    # Skip any records that already have a .md output file (from a previous run)
    already_done = set()
    for center_dir in OUTPUT_DIR.glob("*"):
        if center_dir.is_dir():
            for md_file in center_dir.glob("*.md"):
                already_done.add(md_file.stem)
    if already_done:
        before = len(records)
        records = [r for r in records if r["slug"] not in already_done]
        print(f"Skipped {before - len(records)} already-structured docs, {len(records)} remaining")

    # Incremental: also skip based on state file (for within-session tracking)
    if args.incremental:
        records = [r for r in records if r["slug"] not in processed]
        print(f"New records (incremental): {len(records)}")

    if args.limit:
        records = records[:args.limit]
        print(f"Limited to: {len(records)} records")

    # Process each record
    downloaded = 0
    for record in records:
        center = record["center"] or "OTHER"
        slug = record["slug"]
        out_dir = RAW_DIR / center

        # Save metadata
        out_dir.mkdir(parents=True, exist_ok=True)
        meta_path = out_dir / f"{slug}_meta.json"
        metadata = build_metadata(record)
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)

        # Download PDF
        if not args.skip_pdf and record["pdf_url"]:
            pdf_path = out_dir / f"{slug}.pdf"
            if not pdf_path.exists():
                if download_guidance_pdf(record["pdf_url"], pdf_path):
                    downloaded += 1
                time.sleep(0.5)  # Be polite to FDA servers

        processed.add(slug)

    # Update state
    state["last_guidance_run"] = datetime.utcnow().strftime("%Y-%m-%d")
    state["processed_guidances"] = list(processed)
    save_state(state)

    print(f"\nGuidance fetch complete.")
    print(f"  Processed: {len(records)} records")
    print(f"  PDFs downloaded: {downloaded}")
    print(f"  Total tracked: {len(processed)}")


if __name__ == "__main__":
    main()
