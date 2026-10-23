"""Ingest: one LangGraph graph per Source (load → extract → store), run as a batch."""

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from sdi.extraction.schema import Quotation
from sdi.fx import FxSource
from sdi.loaders import DocumentContent, load
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
        content = state["content"]
        store.save_processed(
            state["source_path"], content.metadata["file_type"], state["quotation"]
        )
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
    sources: Iterable[Path],
    extractor: Extractor,
    fx_source: FxSource,
    store: Store,
    *,
    max_concurrency: int = 2,
) -> None:
    graph = build_graph(extractor, store)
    inputs = [{"source_path": str(path)} for path in sources]
    graph.batch(inputs, config={"max_concurrency": max_concurrency})
