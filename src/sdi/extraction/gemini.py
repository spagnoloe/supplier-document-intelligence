"""Gemini extractor: DocumentContent → Quotation via structured output (ADR-0005).

Thin adapter, not unit tested; the eval harness exercises it against real documents.
"""

import base64
import threading

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import ValidationError

from sdi.extraction.prompt import EXTRACTION_PROMPT
from sdi.extraction.schema import Quotation
from sdi.loaders import DocumentContent


class GeminiExtractor:
    def __init__(self, model: str):
        self._structured = ChatGoogleGenerativeAI(model=model).with_structured_output(Quotation)
        # The LLM cache stores a response before it is parsed, so a cached malformed response
        # would be replayed on every retry. Retries for a Source that already failed bypass it.
        self._structured_uncached = ChatGoogleGenerativeAI(
            model=model, cache=False
        ).with_structured_output(Quotation)
        self._failed: set[str] = set()
        self._lock = threading.Lock()

    def __call__(self, content: DocumentContent) -> Quotation:
        with self._lock:
            retrying = content.source_path in self._failed
        model = self._structured_uncached if retrying else self._structured
        try:
            result = model.invoke([SystemMessage(EXTRACTION_PROMPT), _document_message(content)])
            if not isinstance(result, Quotation):
                raise OutputParserException("model returned no structured output")
            return result
        except (OutputParserException, ValidationError):
            with self._lock:
                self._failed.add(content.source_path)
            raise


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
