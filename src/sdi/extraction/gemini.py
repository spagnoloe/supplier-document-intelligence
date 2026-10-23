"""Gemini extractor: DocumentContent → Quotation via structured output (ADR-0005).

Thin adapter, not unit tested; the eval harness exercises it against real documents.
"""

import base64
import threading

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from sdi.extraction.prompt import EXTRACTION_PROMPT
from sdi.extraction.schema import Quotation
from sdi.loaders import DocumentContent


class GeminiExtractor:
    def __init__(self, model: str, *, seed_base: int = 0):
        # A second read can use the same model as the first; without its own seeds it would
        # send the identical request and the LLM cache would replay the first read to it.
        self._model = model
        self._seed_base = seed_base
        self._attempts: dict[str, int] = {}
        self._lock = threading.Lock()

    def __call__(self, content: DocumentContent) -> Quotation:
        # The LLM cache stores a response before it is parsed, so a retry with the same
        # request would replay the same malformed answer. Each retry of a Source uses its
        # own seed instead: a fresh sample, still cached, so a re-run replays the same
        # sequence of attempts without new calls.
        with self._lock:
            attempt = self._attempts.get(content.source_path, 0)
            self._attempts[content.source_path] = attempt + 1
        llm = ChatGoogleGenerativeAI(model=self._model, seed=self._seed_base + attempt or None)
        result = llm.with_structured_output(Quotation).invoke(
            [SystemMessage(EXTRACTION_PROMPT), _document_message(content)]
        )
        if not isinstance(result, Quotation):
            raise OutputParserException("model returned no structured output")
        return result


def _document_message(content: DocumentContent) -> HumanMessage:
    blocks: list[str | dict] = []
    if content.text:
        blocks.append({"type": "text", "text": content.text})
    for image in content.images:
        blocks.append(
            {
                "type": "image",
                "base64": base64.b64encode(image).decode(),
                "mime_type": _image_mime_type(image),
            }
        )
    return HumanMessage(content=blocks)


def _image_mime_type(image: bytes) -> str:
    return "image/png" if image.startswith(b"\x89PNG") else "image/jpeg"
