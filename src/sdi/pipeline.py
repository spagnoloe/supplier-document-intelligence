"""Ingest: one LangGraph graph per Source (load → extract → store), run as a batch."""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from sdi.extraction.schema import SUPPORTED_DOCUMENT_TYPES, Quotation
from sdi.fx import FxSource
from sdi.loaders import DocumentContent, NoLoaderError, file_type, load
from sdi.store import Store

Extractor = Callable[[DocumentContent], Quotation]


class SourceState(TypedDict, total=False):
    source_path: str
    content: DocumentContent
    quotation: Quotation


def build_graph(extractor: Extractor, store: Store):
    def load_node(state: SourceState) -> SourceState:
        return {"content": load(Path(state["source_path"]))}

    def extract_node(state: SourceState) -> SourceState:
        return {"quotation": extractor(state["content"])}

    def store_node(state: SourceState) -> SourceState:
        path, quotation = state["source_path"], state["quotation"]
        if quotation.document_type in SUPPORTED_DOCUMENT_TYPES:
            store.save_processed(path, file_type(Path(path)), quotation)
        else:
            store.save_skipped(path, file_type(Path(path)), quotation.document_type)
        return {}

    graph = StateGraph(SourceState)
    graph.add_node("load", load_node)
    graph.add_node("extract", extract_node)
    graph.add_node("store", store_node)
    graph.add_edge(START, "load")
    graph.add_edge("load", "extract")
    graph.add_edge("extract", "store")
    graph.add_edge("store", END)
    return graph.compile()


def ingest(
    sources: Sequence[Path],
    extractor: Extractor,
    fx_source: FxSource,
    store: Store,
    *,
    max_concurrency: int = 2,
) -> None:
    graph = build_graph(extractor, store)
    inputs = [{"source_path": str(path)} for path in sources]
    results = graph.batch(
        inputs, config={"max_concurrency": max_concurrency}, return_exceptions=True
    )
    # A failure anywhere in one Source's graph is recorded against that Source only.
    for path, result in zip(sources, results, strict=True):
        if isinstance(result, Exception):
            store.save_failed(str(path), file_type(path), _describe(result))


def _describe(error: Exception) -> str:
    if isinstance(error, NoLoaderError):
        return str(error)
    return f"{type(error).__name__}: {error}"
