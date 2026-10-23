from io import BytesIO
from pathlib import Path

import pdfplumber

from sdi.loaders import DocumentContent, register

# Below this many non-blank characters per page on average, a PDF is a scan: what text it
# has is a page number or a scanner's stamp, not the content. A two-line text quote still
# clears it.
_MIN_CHARS_PER_PAGE = 25

# Rendering resolution for scanned pages: small print stays legible to the model.
_SCAN_DPI = 200


@register(".pdf")
def load_pdf(path: Path) -> DocumentContent:
    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
        text_chars = sum(len("".join(page.split())) for page in pages)
        if text_chars < _MIN_CHARS_PER_PAGE * max(len(pages), 1):
            # A scan gets its pages as images and no text, so a stray stamp can't pass as
            # a text Source and fail every evidence check.
            return DocumentContent(
                source_path=str(path),
                images=[_render(page) for page in pdf.pages],
                metadata={"file_type": "pdf", "page_count": len(pages), "scanned": True},
            )
    # Page breaks are kept visible so a line total on page 2 still reads as page 2's.
    text = "\n\n".join(f"--- page {n} ---\n{page}" for n, page in enumerate(pages, start=1))
    return DocumentContent(
        source_path=str(path),
        text=text,
        metadata={"file_type": "pdf", "page_count": len(pages)},
    )


def _render(page: pdfplumber.page.Page) -> bytes:
    out = BytesIO()
    page.to_image(resolution=_SCAN_DPI).original.save(out, format="PNG")
    return out.getvalue()
