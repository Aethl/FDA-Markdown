"""
Fetch PMA approval data and SSED PDFs from openFDA and FDA servers.

Usage:
    python scripts/fetch_pma.py --product-codes OZP,NIQ --since 2020-01-01
    python scripts/fetch_pma.py --incremental
"""

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from ratelimit import limits, sleep_and_retry

OPENFDA_PMA_URL = "https://api.fda.gov/device/pma.json"
STATE_FILE = Path(__file__).parent.parent / ".pipeline_state.json"
RAW_DIR = Path(__file__).parent.parent / ".raw" / "pma"


@sleep_and_retry
@limits(calls=120, period=60)
def query_openfda(params: dict) -> dict:
    """Query openFDA PMA endpoint with rate limiting."""
    resp = requests.get(OPENFDA_PMA_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_pma_records(product_codes: list[str], since: str | None = None) -> list[dict]:
    """Fetch PMA records from openFDA for given product codes."""
    all_results = []

    for code in product_codes:
        search_parts = [f'product_code:"{code}"']
        if since:
            search_parts.append(f'decision_date:[{since.replace("-", "")} TO *]')

        search = " AND ".join(search_parts)
        skip = 0

        while True:
            params = {"search": search, "limit": 100, "skip": skip}

            try:
                data = query_openfda(params)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
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

    print(f"Total PMA records fetched: {len(all_results)}")
    return all_results


def download_ssed_pdf(pma_number: str, output_path: Path) -> bool:
    """Attempt to download a PMA SSED PDF from FDA servers."""
    pma_clean = pma_number.upper().strip()

    # FDA SSED URLs follow patterns like:
    # https://www.accessdata.fda.gov/cdrh_docs/pdf{YY}/{PMA_NUMBER}B.pdf
    # The 'B' suffix typically indicates the SSED
    num_match = re.match(r'P(\d{2})\d+', pma_clean)
    if not num_match:
        return False

    year_prefix = num_match.group(1)
    year_int = int(year_prefix)
    pdf_segment = f"pdf{year_prefix}" if year_int > 0 else "pdf"

    url_patterns = [
        f"https://www.accessdata.fda.gov/cdrh_docs/{pdf_segment}/{pma_clean}B.pdf",
        f"https://www.accessdata.fda.gov/cdrh_docs/{pdf_segment}/{pma_clean}b.pdf",
        f"https://www.accessdata.fda.gov/cdrh_docs/{pdf_segment}/{pma_clean}S.pdf",
    ]

    for url in url_patterns:
        try:
            resp = requests.get(url, timeout=30, allow_redirects=True)
            if resp.status_code == 200 and "pdf" in resp.headers.get("content-type", ""):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(resp.content)
                print(f"  Downloaded SSED: {pma_clean}")
                return True
        except requests.exceptions.RequestException:
            continue

    print(f"  No SSED PDF found for {pma_clean}")
    return False


def build_metadata(record: dict) -> dict:
    """Extract structured metadata from an openFDA PMA record."""
    openfda = record.get("openfda", {})
    return {
        "type": "pma_summary",
        "document_number": record.get("pma_number", ""),
        "title": record.get("trade_name", record.get("generic_name", "")),
        "applicant": record.get("applicant", ""),
        "date_received": record.get("date_received", ""),
        "decision_date": record.get("decision_date", ""),
        "decision": record.get("decision_code", ""),
        "supplement_number": record.get("supplement_number", ""),
        "supplement_type": record.get("supplement_type", ""),
        "product_code": record.get("product_code", ""),
        "advisory_committee": record.get("advisory_committee_description", ""),
        "regulation_number": openfda.get("regulation_number", [""])[0] if openfda.get("regulation_number") else "",
        "source_url": f"https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpma/pma.cfm?id={record.get('pma_number', '')}",
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
    parser = argparse.ArgumentParser(description="Fetch PMA data from openFDA")
    parser.add_argument("--product-codes", type=str, required=True,
                        help="Comma-separated FDA product codes")
    parser.add_argument("--since", type=str, default=None)
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--skip-pdf", action="store_true")
    args = parser.parse_args()

    codes = [c.strip().upper() for c in args.product_codes.split(",")]
    state = load_state()

    since = args.since
    if args.incremental and state.get("last_pma_run"):
        since = state["last_pma_run"]

    print(f"Fetching PMA records for product codes: {codes}")
    records = fetch_pma_records(codes, since=since)

    processed = set(state.get("processed_pma", []))
    new_records = [r for r in records if r.get("pma_number") not in processed]
    print(f"New PMA records: {len(new_records)}")

    for record in new_records:
        pma_number = record.get("pma_number", "")
        product_code = record.get("product_code", "UNKNOWN")

        metadata = build_metadata(record)
        out_dir = RAW_DIR / product_code.upper()
        out_dir.mkdir(parents=True, exist_ok=True)

        meta_path = out_dir / f"{pma_number}_meta.json"
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)

        if not args.skip_pdf:
            pdf_path = out_dir / f"{pma_number}.pdf"
            if not pdf_path.exists():
                download_ssed_pdf(pma_number, pdf_path)
                time.sleep(0.5)

        processed.add(pma_number)

    state["last_pma_run"] = datetime.utcnow().strftime("%Y-%m-%d")
    state["processed_pma"] = list(processed)
    save_state(state)

    print("Done.")


if __name__ == "__main__":
    main()
