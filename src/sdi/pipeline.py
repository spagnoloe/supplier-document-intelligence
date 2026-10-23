"""Ingest: one LangGraph graph per Source (load → extract → validate → store), run as a batch."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypedDict

from langchain_core.exceptions import OutputParserException
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from pydantic import ValidationError

from sdi.extraction.schema import SUPPORTED_DOCUMENT_TYPES, Quotation
from sdi.fx import FxSource
from sdi.loaders import DocumentContent, NoLoaderError, file_type, load
from sdi.read_model import ReadModel
from sdi.reconcile import reconcile
from sdi.store import Store
from sdi.validation import LineVerification, verify

Extractor = Callable[[DocumentContent], Quotation]

# Malformed or schema-violating model output: worth asking again, unlike a missing file.
STRUCTURED_OUTPUT_ERRORS = (OutputParserException, ValidationError)


class SourceState(TypedDict, total=False):
    source_path: str
    content: DocumentContent
    quotation: Quotation
    verifications: list[LineVerification]


def build_graph(extractor: Extractor, store: Store, max_attempts: int):
    def load_node(state: SourceState) -> SourceState:
        return {"content": load(Path(state["source_path"]))}

    def extract_node(state: SourceState) -> SourceState:
        return {"quotation": extractor(state["content"])}

    def validate_node(state: SourceState) -> SourceState:
        return {"verifications": verify(state["quotation"], state["content"])}

    def store_node(state: SourceState) -> SourceState:
        path, quotation = state["source_path"], state["quotation"]
        if quotation.document_type in SUPPORTED_DOCUMENT_TYPES:
            store.save_processed(path, file_type(Path(path)), quotation, state["verifications"])
        else:
            store.save_skipped(path, file_type(Path(path)), quotation.document_type)
        return {}

    graph = StateGraph(SourceState)
    graph.add_node("load", load_node)
    graph.add_node(
        "extract",
        extract_node,
        retry_policy=RetryPolicy(
            max_attempts=max_attempts, initial_interval=0.1, retry_on=STRUCTURED_OUTPUT_ERRORS
        ),
    )
    graph.add_node("validate", validate_node)
    graph.add_node("store", store_node)
    graph.add_edge(START, "load")
    graph.add_edge("load", "extract")
    graph.add_edge("extract", "validate")
    graph.add_edge("validate", "store")
    graph.add_edge("store", END)
    return graph.compile()


def ingest(
    sources: Sequence[Path],
    extractor: Extractor,
    fx_source: FxSource,
    store: Store,
    *,
    max_concurrency: int = 2,
    max_attempts: int = 3,
) -> None:
    graph = build_graph(extractor, store, max_attempts)
    inputs = [{"source_path": str(path)} for path in sources]
    results = graph.batch(
        inputs, config={"max_concurrency": max_concurrency}, return_exceptions=True
    )
    # A failure anywhere in one Source's graph is recorded against that Source only.
    for path, result in zip(sources, results, strict=True):
        if isinstance(result, Exception):
            store.save_failed(str(path), file_type(path), _error_message(result))
    # Reconciliation spans every stored Source, including those from earlier runs.
    store.save_reconciliation(*reconcile(ReadModel(store.db_path).quotation_lines()))


def _error_message(error: Exception) -> str:
    if isinstance(error, NoLoaderError):
        return str(error)
    return f"{type(error).__name__}: {error}"
