"""
Fetch 510(k) clearance data and summary PDFs from openFDA and FDA servers.

Usage:
    python scripts/fetch_510k.py --product-codes OZP,NIQ --since 2024-01-01
    python scripts/fetch_510k.py --incremental  # uses last run date from state file
"""

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests
from ratelimit import limits, sleep_and_retry

OPENFDA_510K_URL = "https://api.fda.gov/device/510k.json"
FDA_SUMMARY_BASE = "https://www.accessdata.fda.gov/cdrh_docs/pdf{}/{}.pdf"
STATE_FILE = Path(__file__).parent.parent / ".pipeline_state.json"
OUTPUT_DIR = Path(__file__).parent.parent / "510k-summaries"
RAW_DIR = Path(__file__).parent.parent / ".raw" / "510k"

# openFDA allows 240 requests per minute without an API key,
# but let's be conservative
@sleep_and_retry
@limits(calls=120, period=60)
def query_openfda(params: dict) -> dict:
    """Query the openFDA API with rate limiting."""
    resp = requests.get(OPENFDA_510K_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_clearances(product_codes: list[str], since: str | None = None,
                     limit: int = 100) -> list[dict]:
    """
    Fetch 510(k) clearance records from openFDA for given product codes.
    
    Args:
        product_codes: List of FDA product codes (e.g., ["OZP", "NIQ"])
        since: ISO date string; only fetch clearances after this date
        limit: Max results per query (openFDA caps at 1000)
    
    Returns:
        List of clearance record dicts
    """
    all_results = []

    for code in product_codes:
        search_parts = [f'product_code:"{code}"']
        if since:
            search_parts.append(f'decision_date:[{since.replace("-", "")} TO *]')

        search = " AND ".join(search_parts)
        skip = 0

        while True:
            params = {
                "search": search,
                "limit": min(limit, 100),
                "skip": skip,
            }

            try:
                data = query_openfda(params)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    # No results for this query
                    break
                raise

            results = data.get("results", [])
            if not results:
                break

            all_results.extend(results)
            skip += len(results)

            total = data.get("meta", {}).get("results", {}).get("total", 0)
            if skip >= total:
                break

            print(f"  [{code}] Fetched {skip}/{total} records...")

    print(f"Total 510(k) records fetched: {len(all_results)}")
    return all_results


def download_summary_pdf(k_number: str, output_path: Path) -> bool:
    """
    Attempt to download a 510(k) summary PDF from FDA servers.
    
    FDA stores summaries at predictable URLs based on the K-number.
    The path pattern varies by year range.
    """
    k_number = k_number.upper().strip()
    
    # Extract the numeric portion to determine the PDF path segment
    num_match = re.match(r'K(\d+)', k_number)
    if not num_match:
        print(f"  Warning: Could not parse K-number: {k_number}")
        return False

    num = num_match.group(1)
    # FDA organizes by century: K9xxxxx -> pdf9, K0xxxxx -> pdf, K1xxxxx -> pdf14, etc.
    year_prefix = num[:2]
    
    # Determine the PDF path segment
    year_int = int(year_prefix)
    if year_int >= 90:
        pdf_segment = f"pdf{year_prefix}"
    elif year_int == 0:
        pdf_segment = "pdf"
    else:
        pdf_segment = f"pdf{year_prefix}"

    # Try multiple URL patterns (FDA isn't perfectly consistent)
    url_patterns = [
        f"https://www.accessdata.fda.gov/cdrh_docs/{pdf_segment}/{k_number}.pdf",
        f"https://www.accessdata.fda.gov/cdrh_docs/{pdf_segment}/{k_number.lower()}.pdf",
    ]

    for url in url_patterns:
        try:
            resp = requests.get(url, timeout=30, allow_redirects=True)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/pdf"):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(resp.content)
                print(f"  Downloaded: {k_number}")
                return True
        except requests.exceptions.RequestException:
            continue

    print(f"  No summary PDF found for {k_number}")
    return False


def build_metadata(record: dict) -> dict:
    """Extract structured metadata from an openFDA 510(k) record."""
    openfda = record.get("openfda", {})

    return {
        "type": "510k_summary",
        "document_number": record.get("k_number", ""),
        "title": record.get("device_name", ""),
        "applicant": record.get("applicant_100", ""),
        "contact": record.get("contact", ""),
        "date_received": record.get("date_received", ""),
        "decision_date": record.get("decision_date", ""),
        "decision": record.get("decision_code", ""),
        "decision_description": record.get("decision_description", ""),
        "product_code": record.get("product_code", ""),
        "classification_panel": record.get("advisory_committee_description", ""),
        "regulation_number": openfda.get("regulation_number", [""])[0] if openfda.get("regulation_number") else "",
        "classification": record.get("clearance_type", ""),
        "review_advisory_committee": record.get("review_advisory_committee", ""),
        "statement_or_summary": record.get("statement_or_summary", ""),
        "third_party_flag": record.get("third_party_flag", ""),
        "expedited_review_flag": record.get("expedited_review_flag", ""),
        "source_url": f"https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={record.get('k_number', '')}",
        "last_updated": datetime.utcnow().strftime("%Y-%m-%d"),
    }


def save_metadata(metadata: dict, product_code: str):
    """Save clearance metadata as JSON for later LLM structuring."""
    k_number = metadata["document_number"]
    out_dir = RAW_DIR / product_code.upper()
    out_dir.mkdir(parents=True, exist_ok=True)

    meta_path = out_dir / f"{k_number}_meta.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)


def load_state() -> dict:
    """Load pipeline state (last run date, processed K-numbers, etc.)."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_510k_run": None, "processed_510k": []}


def save_state(state: dict):
    """Persist pipeline state."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Fetch 510(k) data from openFDA")
    parser.add_argument("--product-codes", type=str, required=True,
                        help="Comma-separated FDA product codes")
    parser.add_argument("--since", type=str, default=None,
                        help="Fetch clearances after this date (YYYY-MM-DD)")
    parser.add_argument("--incremental", action="store_true",
                        help="Use last run date from state file")
    parser.add_argument("--skip-pdf", action="store_true",
                        help="Skip PDF downloads (metadata only)")
    args = parser.parse_args()

    codes = [c.strip().upper() for c in args.product_codes.split(",")]
    state = load_state()

    since = args.since
    if args.incremental and state.get("last_510k_run"):
        since = state["last_510k_run"]
        print(f"Incremental mode: fetching since {since}")

    print(f"Fetching 510(k) records for product codes: {codes}")
    records = fetch_clearances(codes, since=since)

    already_processed = set(state.get("processed_510k", []))
    new_records = [r for r in records if r.get("k_number") not in already_processed]
    print(f"New records to process: {len(new_records)}")

    for record in new_records:
        k_number = record.get("k_number", "")
        product_code = record.get("product_code", "UNKNOWN")

        # Save metadata
        metadata = build_metadata(record)
        save_metadata(metadata, product_code)

        # Download summary PDF
        if not args.skip_pdf:
            pdf_path = RAW_DIR / product_code.upper() / f"{k_number}.pdf"
            if not pdf_path.exists():
                download_summary_pdf(k_number, pdf_path)
                time.sleep(0.5)  # Be polite to FDA servers

        already_processed.add(k_number)

    # Update state
    state["last_510k_run"] = datetime.utcnow().strftime("%Y-%m-%d")
    state["processed_510k"] = list(already_processed)
    save_state(state)

    print("Done.")


if __name__ == "__main__":
    main()
