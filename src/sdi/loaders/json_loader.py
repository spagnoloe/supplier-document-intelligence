import json
from pathlib import Path

from sdi.loaders import DocumentContent, register


@register(".json")
def load_json(path: Path) -> DocumentContent:
    # Parsed and re-serialized so malformed files fail here, not in the LLM, and every
    # export reaches the prompt in the same layout whatever its field names.
    data = json.loads(path.read_text(encoding="utf-8"))
    return DocumentContent(
        source_path=str(path),
        text=json.dumps(data, indent=2, ensure_ascii=False),
        metadata={"file_type": "json"},
    )
