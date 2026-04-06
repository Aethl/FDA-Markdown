# FDA Open Regulatory Intelligence

An open-source, autonomously maintained repository of structured FDA regulatory documents — guidance documents, 510(k) summaries, and PMA summaries — converted from PDF to markdown and version-controlled for transparency, searchability, and LLM consumption.

## Why This Exists

FDA regulatory documents are public domain (U.S. government works), but they're trapped in PDFs that are hard to search, impossible to diff, and painful to integrate into modern workflows. This repo solves that by:

- **Converting** FDA PDFs into structured, consistently formatted markdown
- **Tracking changes** via git history — see exactly what changed when FDA revises a guidance
- **Running daily** via GitHub Actions to automatically pick up new and updated documents
- **Structuring** documents with standardized YAML frontmatter for programmatic access

## Repository Structure

```
├── guidances/              # FDA guidance documents (draft and final)
│   ├── CDRH/               # Center for Devices and Radiological Health
│   ├── CDER/               # Center for Drug Evaluation and Research
│   └── CBER/               # Center for Biologics Evaluation and Research
├── 510k-summaries/         # 510(k) summary documents by product code
│   └── {PRODUCT_CODE}/     # Organized by FDA product code/
├── pma-summaries/          # PMA summaries of safety and effectiveness (SSED)
│   └── .../
├── scripts/                # Pipeline scripts
│   ├── fetch_510k.py       # Fetch new 510(k) data from openFDA
│   ├── fetch_pma.py        # Fetch new PMA data from openFDA
│   ├── fetch_guidances.py  # Fetch new/updated guidance documents
│   ├── extract_pdf.py      # PDF text extraction utilities
│   ├── structure_md.py     # LLM-powered markdown structuring
│   └── update_index.py     # Rebuild the searchable index
├── templates/              # Prompt templates for LLM structuring
├── index.json              # Master index of all documents
└── .github/workflows/      # GitHub Actions for daily automation
```

## Document Format

Every document follows a consistent structure:

```markdown
---
type: 510k_summary | pma_summary | guidance
document_number: K231234
title: "Device Name - 510(k) Summary"
applicant: "Company Name"
date_received: 2024-01-15
decision_date: 2024-03-20
decision: SESE  # substantially equivalent
product_code: QMT
predicate_devices:
  - K201234
  - K191234
classification: Class II
regulation_number: "878.4018"
source_pdf: "https://www.accessdata.fda.gov/..."
last_updated: 2024-03-25
---

# Device Name - 510(k) Summary

## Indications for Use
...

## Device Description
...

## Substantial Equivalence Comparison
...

## Performance Testing
...
```

## How It Works

1. **Daily cron** (GitHub Actions) triggers the pipeline
2. **openFDA API** is queried for new/updated 510(k), PMA, and guidance entries
3. **PDF download** from FDA servers for any new documents
4. **Text extraction** via pdfplumber (primary) with PyMuPDF fallback
5. **LLM structuring** via Anthropic API — raw text is converted to consistent markdown with proper frontmatter
6. **Commit & push** — new/updated documents are committed with descriptive messages
7. **Index rebuild** — master index.json is regenerated

## Product Codes

Product codes are configurable via the `--product-codes` argument. Pass any valid FDA product codes as a comma-separated list. See [FDA Product Classification Database](https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPCD/classification.cfm) for the full list.

## Setup

### Prerequisites

- Python 3.11+
- Anthropic API key (for LLM structuring step)

### Environment Variables

```bash
ANTHROPIC_API_KEY=sk-ant-...    # Required for markdown structuring
```

### Local Development

```bash
pip install -r requirements.txt

# Run a full pipeline pass
python scripts/run_pipeline.py

# Fetch only (no LLM structuring)
python scripts/fetch_510k.py --product-codes OZP,NIQ

# Re-structure an existing extracted document
python scripts/structure_md.py --input raw/K231234.txt --output 510k-summaries/OZP/K231234.md
```

## Contributing

Contributions welcome — especially:
- Adding new product codes and regulatory areas
- Improving extraction quality for tricky PDF formats
- Enhancing the LLM structuring prompts
- Building tools on top of the structured data

## License

The pipeline code is MIT licensed. The FDA documents themselves are U.S. government works and are in the public domain.

---

*This repository is not affiliated with or endorsed by the U.S. Food and Drug Administration.*
