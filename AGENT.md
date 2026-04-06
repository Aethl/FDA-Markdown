# AGENT.md — How to Use This Repository

This file is intended for LLMs, AI agents, and automated tools that interact with this repository. It describes the data structure, conventions, and best practices for querying and interpreting FDA regulatory documents stored here.

## What This Repository Contains

This is a structured, version-controlled corpus of U.S. FDA regulatory documents converted from PDF to markdown. It includes:

- **510(k) clearance summaries** — premarket notification decisions for medical devices
- **PMA summaries** — premarket approval summaries of safety and effectiveness (SSED) for Class III devices
- **Guidance documents** — FDA's current thinking on regulatory topics, both draft and final

All documents are **public domain** (U.S. government works, 17 U.S.C. § 105). There are no copyright or licensing restrictions on the document content.

## Repository Structure

```
fda-markdown/
├── index.json                  # Master index of all documents with metadata
├── 510k-summaries/
│   └── {PRODUCT_CODE}/         # Organized by FDA product code
│       └── {K_NUMBER}.md       # e.g., K231234.md
├── pma-summaries/
│   └── {PRODUCT_CODE}/
│       └── {PMA_NUMBER}.md     # e.g., P200001.md
├── guidances/
│   └── {FDA_CENTER}/           # CDRH, CDER, CBER, etc.
│       └── {document}.md
```

## Document Format

Every document is a markdown file with YAML frontmatter. The frontmatter is the primary source of structured metadata.

### 510(k) Summary Frontmatter

```yaml
---
type: 510k_summary
document_number: K231234            # Unique 510(k) number
title: "Device Name"                # Trade name of the device
applicant: "Company Name"           # Legal name of the applicant
date_received: 2024-01-15           # Date FDA received the submission
decision_date: 2024-03-20           # Date of FDA decision
decision: SESE                      # Decision code (SESE = substantially equivalent)
product_code: QMT                   # FDA product code
predicate_devices:                  # K-numbers of predicate devices cited
  - K201234
  - K191234
regulation_number: "878.4018"       # 21 CFR regulation number
source_url: "https://..."           # Link to original FDA record
last_updated: 2024-03-25            # Date this markdown was last generated
---
```

### PMA Summary Frontmatter

```yaml
---
type: pma_summary
document_number: P200001
title: "Device Name"
applicant: "Company Name"
date_received: 2023-06-01
approval_date: 2024-01-15
product_code: QMT
classification: Class III
regulation_number: "878.4018"
source_url: "https://..."
last_updated: 2024-01-20
---
```

### Guidance Document Frontmatter

```yaml
---
type: guidance
document_number: "FDA-2024-D-1234"
title: "Guidance Title"
status: final                       # draft | final | withdrawn
date_issued: 2024-06-15
fda_center: CDRH                    # CDRH | CDER | CBER | ORA | multi
docket_number: "FDA-2024-D-1234"
topics:
  - biocompatibility
  - device classification
related_regulations:
  - "21 CFR 878.4018"
related_guidances:
  - "Title of related guidance"
source_url: "https://..."
last_updated: 2024-06-20
---
```

## How to Query This Data

### Using index.json

The `index.json` file at the repository root contains a flat index of all documents with key metadata fields. Use this for:
- Listing all documents by type
- Filtering by product code, decision date, or applicant
- Finding document file paths without traversing the directory tree

### Common Query Patterns

**Find all 510(k) clearances for a product code:**
Look in `510k-summaries/{PRODUCT_CODE}/` or filter `index.json` by `product_code`.

**Find predicate device chains:**
Read the `predicate_devices` field in frontmatter. Follow the chain by loading each predicate's markdown file. Predicate chains can be multiple levels deep.

**Compare a device to its predicates:**
The "Substantial Equivalence Comparison" section typically contains a comparison table between the subject device and its predicate(s). Parse this section for structured comparison data.

**Track regulatory changes over time:**
Use `git log` on any document to see when it was added or modified. For guidance documents, `git diff` between commits shows exactly what FDA changed in a revision.

**Find devices with specific characteristics:**
Search the "Device Description" and "Indications for Use" sections across documents. These sections describe what the device is and what it's cleared for.

## Important Caveats for Agents

1. **These are summaries, not complete submissions.** 510(k) summaries are brief overviews; the actual submission contains far more detail. Do not treat these as comprehensive regulatory files.

2. **Frontmatter metadata comes from openFDA.** It is generally reliable but occasionally has data quality issues (missing fields, inconsistent formatting). Cross-reference with the document body when precision matters.

3. **Document body text was extracted from PDF and structured by LLM.** While high quality, it may contain extraction artifacts, especially in tables and figures. When exact wording matters (e.g., indications for use), verify against the original FDA source using the `source_url` in frontmatter.

4. **Decision codes:**
   - `SESE` = Substantially Equivalent (cleared)
   - `SENE` = Not Substantially Equivalent (denied)
   - `SEKN` = Substantially Equivalent with limitations

5. **Product codes** are FDA's classification system for device types. A single product code may cover many different devices. Use the `--product-codes` argument or filter `index.json` by `product_code` to scope queries.

6. **Guidance documents reflect FDA's current thinking** at the time of publication. They are not legally binding. Always check the `status` field — `draft` guidances are proposals; `final` guidances represent FDA's established position; `withdrawn` guidances are no longer in effect.

7. **This repository updates daily.** New FDA clearances and approvals appear here within 24-48 hours of being published on FDA's servers, subject to openFDA indexing delays.

## Recommended Use as MCP Resource or Tool Context

If connecting this repository to an agent via MCP, file system tool, or RAG pipeline:

- **Start with `index.json`** to identify relevant documents before loading full markdown files
- **Parse YAML frontmatter** for structured queries; use the markdown body for detailed content
- **Scope by product code** to avoid loading irrelevant documents
- **Use git history** for change-tracking questions ("what new devices were cleared this month?")
- **Combine document types** for comprehensive analysis: guidance tells you what FDA expects, 510(k)/PMA summaries tell you what FDA accepted

## Data Freshness

| Field | Update Frequency |
|-------|-----------------|
| 510(k) summaries | Daily (subject to openFDA indexing lag, typically 1-4 weeks) |
| PMA summaries | Daily (same lag) |
| Guidance documents | Daily check; new guidances published irregularly |
| index.json | Rebuilt on every pipeline run |

## Contact

For issues with data quality or missing documents, open a GitHub issue.
