# FDA Markdown

A free, open dataset of FDA regulatory documents converted from PDF to structured markdown. Updated automatically.

**Browse the documents directly in this repo** — every FDA guidance, 510(k) summary, and PMA summary is a clean markdown file with structured YAML metadata.

## What's in here

| Folder | Contents | Source |
|--------|----------|--------|
| `guidances/` | FDA guidance documents (draft and final) organized by center (CDRH, CDER, CBER, etc.) | [FDA Guidance Search](https://www.fda.gov/regulatory-information/search-fda-guidance-documents) |
| `510k-summaries/` | 510(k) premarket notification summaries organized by product code | [openFDA 510(k) API](https://open.fda.gov/apis/device/510k/) |
| `pma-summaries/` | PMA summaries of safety and effectiveness (SSED) organized by product code | [openFDA PMA API](https://open.fda.gov/apis/device/pma/) |
| `index.json` | Master index of every document with key metadata fields | |

All FDA documents are **public domain** (U.S. government works, 17 U.S.C. 105).

## How to use this data

### Browse on GitHub

Every document is readable directly on GitHub. Click into any folder and open a `.md` file.

### Clone locally

```bash
git clone https://github.com/Aethl/FDA-Markdown.git
```

### Use the index

`index.json` lists every document with metadata so you don't have to crawl directories:

```python
import json

with open("index.json") as f:
    index = json.load(f)

# List all guidance documents
for doc in index["documents"]["guidances"]:
    print(doc["title"], doc["file"])

# Find 510(k) clearances by product code
for doc in index["documents"]["510k_summaries"]:
    if doc.get("product_code") == "QMT":
        print(doc["document_number"], doc["title"])
```

### Feed to an LLM or RAG pipeline

Every file has YAML frontmatter with structured metadata, making it easy to ingest:

```yaml
---
type: guidance
title: "Guidance Title"
status: final
date_issued: 2024-06-15
fda_center: CDRH
topics:
  - biocompatibility
  - device classification
source_url: "https://www.fda.gov/..."
---
```

See [`AGENT.md`](AGENT.md) for detailed instructions on querying this data programmatically.

### Track FDA changes over time

Since everything is in git, you can see exactly when documents were added or changed:

```bash
# What new documents were added this week?
git log --since="1 week ago" --name-only --diff-filter=A

# What changed in a specific guidance?
git log -p guidances/CDRH/some-guidance.md
```

## Document format

Every document follows a consistent pattern:

- **YAML frontmatter** — structured metadata (type, title, date, center, status, source URL, etc.)
- **Markdown body** — the full document content with proper headings, tables, and sections

The original PDF content is preserved faithfully. The conversion cleans up PDF artifacts and imposes consistent structure — it does not paraphrase or summarize.

## How this repo stays updated

An automated pipeline runs via GitHub Actions:

1. Fetches metadata from the [openFDA API](https://open.fda.gov/) and [FDA guidance index](https://www.fda.gov/files/api/datatables/static/search-for-guidance.json)
2. Downloads PDFs from FDA servers
3. Extracts text with pdfplumber / PyMuPDF
4. Structures into markdown using the Anthropic API (Claude)
5. Commits new and updated documents to this repo

The pipeline scripts live in `scripts/` if you're curious, but you don't need them to use the data.

## Contributing

Contributions welcome:
- Report data quality issues (bad extractions, missing documents) via [GitHub Issues](https://github.com/Aethl/FDA-Markdown/issues)
- Suggest additional document types or product codes to include
- Improve extraction or structuring quality

## License

Pipeline code is MIT licensed. FDA documents are U.S. government works in the public domain.

---

*This repository is not affiliated with or endorsed by the U.S. Food and Drug Administration.*
