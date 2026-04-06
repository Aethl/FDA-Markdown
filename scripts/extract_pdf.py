"""
Extract raw text from FDA PDF documents using pdfplumber (primary) 
with PyMuPDF fallback for scanned/image-based PDFs.

Usage:
    python scripts/extract_pdf.py --input .raw/510k/OZP/K231234.pdf
    python scripts/extract_pdf.py --batch .raw/510k/QMT/
"""

import argparse
import sys
from pathlib import Path


def extract_with_pdfplumber(pdf_path: Path) -> str | None:
    """Extract text using pdfplumber — best for native-text PDFs."""
    try:
        import pdfplumber
    except ImportError:
        print("pdfplumber not installed, skipping")
        return None

    try:
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(f"--- Page {i + 1} ---\n{page_text}")
                    
                # Also try to extract tables
                tables = page.extract_tables()
                for table in tables:
                    if table:
                        # Convert table to markdown-ish format
                        table_text = "\n".join(
                            " | ".join(str(cell or "") for cell in row)
                            for row in table
                        )
                        text_parts.append(f"\n[TABLE]\n{table_text}\n[/TABLE]\n")

        full_text = "\n\n".join(text_parts)
        
        # Check if we got meaningful content
        stripped = full_text.replace("-", "").replace("Page", "").strip()
        if len(stripped) < 100:
            return None  # Probably a scanned PDF
            
        return full_text
        
    except Exception as e:
        print(f"  pdfplumber error: {e}")
        return None


def extract_with_pymupdf(pdf_path: Path) -> str | None:
    """Extract text using PyMuPDF — good fallback, handles more PDF types."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print("PyMuPDF not installed, skipping")
        return None

    try:
        doc = fitz.open(str(pdf_path))
        text_parts = []
        
        for i, page in enumerate(doc):
            text = page.get_text("text")
            if text.strip():
                text_parts.append(f"--- Page {i + 1} ---\n{text}")

        doc.close()
        
        full_text = "\n\n".join(text_parts)
        if len(full_text.strip()) < 100:
            return None
            
        return full_text
        
    except Exception as e:
        print(f"  PyMuPDF error: {e}")
        return None


def extract_text(pdf_path: Path) -> str | None:
    """
    Extract text from a PDF, trying pdfplumber first, then PyMuPDF.
    
    Returns the extracted text or None if extraction fails.
    """
    print(f"  Extracting: {pdf_path.name}")
    
    # Try pdfplumber first (better table handling)
    text = extract_with_pdfplumber(pdf_path)
    if text:
        print(f"    -> pdfplumber: {len(text)} chars")
        return text
    
    # Fall back to PyMuPDF
    text = extract_with_pymupdf(pdf_path)
    if text:
        print(f"    -> PyMuPDF: {len(text)} chars")
        return text
    
    print(f"    -> FAILED: Could not extract text from {pdf_path.name}")
    print(f"       This may be a scanned PDF requiring OCR (not yet implemented)")
    return None


def main():
    parser = argparse.ArgumentParser(description="Extract text from FDA PDFs")
    parser.add_argument("--input", type=str, help="Path to a single PDF")
    parser.add_argument("--batch", type=str, help="Path to directory of PDFs")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for .txt files (default: same as input)")
    args = parser.parse_args()

    if args.input:
        pdf_path = Path(args.input)
        if not pdf_path.exists():
            print(f"File not found: {pdf_path}")
            sys.exit(1)

        text = extract_text(pdf_path)
        if text:
            out_dir = Path(args.output_dir) if args.output_dir else pdf_path.parent
            out_path = out_dir / f"{pdf_path.stem}.txt"
            out_path.write_text(text, encoding="utf-8")
            print(f"  Saved: {out_path}")

    elif args.batch:
        batch_dir = Path(args.batch)
        if not batch_dir.is_dir():
            print(f"Not a directory: {batch_dir}")
            sys.exit(1)

        pdfs = list(batch_dir.glob("*.pdf"))
        print(f"Found {len(pdfs)} PDFs in {batch_dir}")

        out_dir = Path(args.output_dir) if args.output_dir else batch_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        success = 0
        for pdf_path in sorted(pdfs):
            text = extract_text(pdf_path)
            if text:
                out_path = out_dir / f"{pdf_path.stem}.txt"
                out_path.write_text(text, encoding="utf-8")
                success += 1

        print(f"\nExtracted {success}/{len(pdfs)} PDFs")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
