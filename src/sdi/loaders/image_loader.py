from pathlib import Path

from sdi.loaders import DocumentContent, register


@register(".png", ".jpg", ".jpeg")
def load_image(path: Path) -> DocumentContent:
    # A photo or scan goes to the model as it is: no OCR or preprocessing, and no text, so
    # nothing can be evidence-checked against it.
    return DocumentContent(
        source_path=str(path),
        images=[path.read_bytes()],
        metadata={"file_type": path.suffix.lower().lstrip(".")},
    )
