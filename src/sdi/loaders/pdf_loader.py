from pathlib import Path

import pdfplumber

from sdi.loaders import DocumentContent, register


@register(".pdf")
def load_pdf(path: Path) -> DocumentContent:
    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    # Page breaks are kept visible so a line total on page 2 still reads as page 2's.
    return DocumentContent(
        source_path=str(path),
        text="\n\n".join(f"--- page {n} ---\n{text}" for n, text in enumerate(pages, start=1)),
        metadata={"file_type": "pdf", "page_count": len(pages)},
    )
