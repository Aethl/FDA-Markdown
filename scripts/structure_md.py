"""
LLM-powered structuring of raw FDA document text into consistent markdown.

Uses the Anthropic API to transform raw extracted PDF text into well-structured
markdown with standardized frontmatter and sections.

Usage:
    python scripts/structure_md.py --input .raw/510k/OZP/K231234.txt --meta .raw/510k/OZP/K231234_meta.json
    python scripts/structure_md.py --batch .raw/510k/QMT/ --doc-type 510k
"""

import argparse
import json
import os
import sys
from pathlib import Path

import anthropic
import yaml

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096

# ── Prompt templates ──────────────────────────────────────────────────────────

SYSTEM_PROMPT_510K = """You are a regulatory document structuring assistant. Your job is to take raw text extracted from an FDA 510(k) summary PDF and convert it into clean, well-structured markdown.

You will also receive JSON metadata from the openFDA API for this clearance.

Output ONLY the markdown document. No commentary, no code fences. Start with the YAML frontmatter block (---).

Rules:
1. Begin with YAML frontmatter containing all metadata fields
2. Use consistent section headers matching FDA 510(k) summary structure
3. Preserve all technical content accurately — do not paraphrase or summarize scientific claims
4. Clean up PDF extraction artifacts (broken words, page headers/footers, garbled tables)
5. Reconstruct tables as proper markdown tables where possible
6. If a section is not present in the source text, omit it (do not fabricate)
7. Keep the device name, indications, and predicate information exactly as stated
8. Normalize dates to YYYY-MM-DD format in frontmatter"""

USER_PROMPT_510K = """Convert this 510(k) summary into structured markdown.

## openFDA Metadata
```json
{metadata}
```

## Raw Extracted Text
{raw_text}

## Required Markdown Structure

---
type: 510k_summary
document_number: <K-number>
title: <device name>
applicant: <company>
date_received: <YYYY-MM-DD>
decision_date: <YYYY-MM-DD>
decision: <decision code>
product_code: <code>
predicate_devices:
  - <K-numbers of predicates mentioned>
regulation_number: <CFR number>
source_url: <FDA URL>
last_updated: <today>
---

# <Device Name> — 510(k) Summary

## Indications for Use
<exact indications as stated>

## Device Description
<description of the device>

## Substantial Equivalence Comparison
<comparison to predicate device(s), ideally as a table>

## Performance Testing
### Biocompatibility
### Bench Testing
### Clinical Data (if any)

## Conclusion
<substantial equivalence determination>

Use only the sections that are present in the source. Add subsections as needed to preserve detail."""


SYSTEM_PROMPT_GUIDANCE = """You are a regulatory document structuring assistant. Your job is to take raw text extracted from an FDA guidance document PDF and convert it into clean, well-structured markdown.

Output ONLY the markdown document. No commentary, no code fences. Start with the YAML frontmatter block (---).

Rules:
1. Begin with YAML frontmatter containing document metadata
2. Preserve the document's original section structure and numbering
3. Preserve all regulatory content accurately — these are legally significant documents
4. Clean up PDF extraction artifacts (broken words, page headers/footers)
5. Reconstruct tables and flowcharts as best as possible in markdown
6. Include FDA's standard disclaimer about guidance documents if present
7. Normalize any cross-references to other FDA documents"""

USER_PROMPT_GUIDANCE = """Convert this FDA guidance document into structured markdown.

## Raw Extracted Text
{raw_text}

## Required Frontmatter Structure

---
type: guidance
document_number: <FDA document number if available>
title: <guidance title>
status: <draft | final | withdrawn>
date_issued: <YYYY-MM-DD>
fda_center: <CDRH | CDER | CBER | ORA | multi>
docket_number: <if available>
topics:
  - <relevant topic tags>
related_regulations:
  - <CFR references>
related_guidances:
  - <other guidance documents referenced>
source_url: <FDA URL if known>
last_updated: <today>
---

Preserve the document's own structure below the frontmatter. Use the original section headers and numbering."""


SYSTEM_PROMPT_PMA = """You are a regulatory document structuring assistant. Your job is to take raw text extracted from an FDA PMA Summary of Safety and Effectiveness (SSED) PDF and convert it into clean, well-structured markdown.

Output ONLY the markdown document. No commentary, no code fences. Start with the YAML frontmatter block (---).

Rules:
1. Begin with YAML frontmatter containing all metadata fields
2. Use consistent section headers matching FDA PMA SSED structure
3. Preserve ALL clinical data, study results, and statistical analyses exactly
4. Clean up PDF extraction artifacts
5. Reconstruct tables (especially clinical results tables) as markdown tables
6. Preserve adverse event data and complication rates exactly as reported
7. Do not summarize or condense clinical data — completeness is critical"""

USER_PROMPT_PMA = """Convert this PMA Summary of Safety and Effectiveness into structured markdown.

## Raw Extracted Text
{raw_text}

## Required Frontmatter Structure

---
type: pma_summary
document_number: <PMA number>
title: <device name>
applicant: <company>
date_received: <YYYY-MM-DD>
approval_date: <YYYY-MM-DD>
product_code: <code>
classification: Class III
regulation_number: <CFR>
source_url: <FDA URL>
last_updated: <today>
---

Preserve the complete SSED structure including all clinical study details, endpoints, results, adverse events, and conditions of approval."""


PROMPTS = {
    "510k": (SYSTEM_PROMPT_510K, USER_PROMPT_510K),
    "guidance": (SYSTEM_PROMPT_GUIDANCE, USER_PROMPT_GUIDANCE),
    "pma": (SYSTEM_PROMPT_PMA, USER_PROMPT_PMA),
}


# ── Core structuring logic ───────────────────────────────────────────────────

def structure_document(raw_text: str, doc_type: str,
                       metadata: dict | None = None) -> str:
    """
    Use the Anthropic API to convert raw PDF text into structured markdown.
    
    Args:
        raw_text: Raw text extracted from PDF
        doc_type: One of '510k', 'guidance', 'pma'
        metadata: Optional openFDA metadata dict (used for 510k and PMA)
    
    Returns:
        Structured markdown string
    """
    if doc_type not in PROMPTS:
        raise ValueError(f"Unknown doc_type: {doc_type}. Use one of: {list(PROMPTS.keys())}")

    system_prompt, user_template = PROMPTS[doc_type]

    # Build the user message
    if metadata:
        user_msg = user_template.format(
            raw_text=raw_text[:50000],  # Truncate very long docs
            metadata=json.dumps(metadata, indent=2),
        )
    else:
        user_msg = user_template.format(
            raw_text=raw_text[:50000],
        )

    client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_msg}],
    )

    # Extract text from response
    structured_md = ""
    for block in response.content:
        if block.type == "text":
            structured_md += block.text

    return structured_md.strip()


def process_single(txt_path: Path, doc_type: str,
                   meta_path: Path | None = None,
                   output_dir: Path | None = None) -> Path | None:
    """Process a single extracted text file into structured markdown."""
    raw_text = txt_path.read_text(encoding="utf-8")

    metadata = None
    if meta_path and meta_path.exists():
        with open(meta_path) as f:
            metadata = json.load(f)

    print(f"  Structuring: {txt_path.name} ({len(raw_text)} chars)")

    try:
        structured = structure_document(raw_text, doc_type, metadata)
    except Exception as e:
        print(f"    ERROR: {e}")
        return None

    # Determine output path
    if output_dir:
        out_path = output_dir / f"{txt_path.stem}.md"
    else:
        out_path = txt_path.parent / f"{txt_path.stem}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(structured, encoding="utf-8")
    print(f"    -> Saved: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Structure FDA documents with LLM")
    parser.add_argument("--input", type=str, help="Path to a single .txt file")
    parser.add_argument("--meta", type=str, default=None,
                        help="Path to metadata JSON (for 510k/PMA)")
    parser.add_argument("--batch", type=str, help="Directory of .txt files")
    parser.add_argument("--doc-type", type=str, required=True,
                        choices=["510k", "guidance", "pma"],
                        help="Document type")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for .md files")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    if args.input:
        txt_path = Path(args.input)
        meta_path = Path(args.meta) if args.meta else None
        out_dir = Path(args.output_dir) if args.output_dir else None
        process_single(txt_path, args.doc_type, meta_path, out_dir)

    elif args.batch:
        batch_dir = Path(args.batch)
        txt_files = sorted(batch_dir.glob("*.txt"))
        out_dir = Path(args.output_dir) if args.output_dir else None

        print(f"Found {len(txt_files)} text files to structure")

        for txt_path in txt_files:
            # Look for matching metadata file
            meta_path = batch_dir / f"{txt_path.stem}_meta.json"
            if not meta_path.exists():
                meta_path = None

            process_single(txt_path, args.doc_type, meta_path, out_dir)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
