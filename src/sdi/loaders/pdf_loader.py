from pathlib import Path

import pdfplumber

from sdi.loaders import DocumentContent, register


@register(".pdf")
def load_pdf(path: Path) -> DocumentContent:
    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    # Page breaks are kept visible so a line total on page 2 still reads as page 2's. A PDF
    # with no text layer (a scan) gets None, not a page of bare markers, so it can't pass
    # as a text Source.
    text = None
    if any(page.strip() for page in pages):
        text = "\n\n".join(f"--- page {n} ---\n{page}" for n, page in enumerate(pages, start=1))
    return DocumentContent(
        source_path=str(path),
        text=text,
        metadata={"file_type": "pdf", "page_count": len(pages)},
    )
