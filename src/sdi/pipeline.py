"""Ingest: one LangGraph graph per Source, run as a batch.

load → extract → validate → [second read] → store. The second read is a conditional edge:
it runs for image Sources and for Sources with a line whose critical checks failed.
"""

from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import TypedDict

from langchain_core.exceptions import OutputParserException
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from pydantic import ValidationError

from sdi.extraction.schema import SUPPORTED_DOCUMENT_TYPES, Quotation
from sdi.fx import FxRate, FxSource
from sdi.loaders import DocumentContent, NoLoaderError, file_type, load
from sdi.read_model import ReadModel
from sdi.reconcile import reconcile
from sdi.store import Store
from sdi.validation import LineVerification, verify, wants_second_read

Extractor = Callable[[DocumentContent], Quotation]

# Malformed or schema-violating model output: worth asking again, unlike a missing file.
STRUCTURED_OUTPUT_ERRORS = (OutputParserException, ValidationError)


class SourceState(TypedDict, total=False):
    source_path: str
    content: DocumentContent
    quotation: Quotation
    verifications: list[LineVerification]


def build_graph(
    extractor: Extractor,
    second_reader: Extractor | None,
    fx_source: FxSource,
    store: Store,
    max_attempts: int,
):
    fx = _cached(fx_source, store)

    def load_node(state: SourceState) -> SourceState:
        return {"content": load(Path(state["source_path"]))}

    def extract_node(state: SourceState) -> SourceState:
        return {"quotation": extractor(state["content"])}

    def validate_node(state: SourceState) -> SourceState:
        return {"verifications": verify(state["quotation"], state["content"], fx)}

    def after_validate(state: SourceState) -> str:
        wanted = second_reader is not None and wants_second_read(
            state["content"], state["verifications"]
        )
        return "second_read" if wanted else "store"

    def second_read_node(state: SourceState) -> SourceState:
        assert second_reader is not None
        second = _second_read(second_reader, state["content"], max_attempts)
        return {
            "verifications": verify(state["quotation"], state["content"], fx, second_read=second)
        }

    def store_node(state: SourceState) -> SourceState:
        path, quotation = state["source_path"], state["quotation"]
        if quotation.document_type in SUPPORTED_DOCUMENT_TYPES:
            store.save_processed(
                path,
                file_type(Path(path)),
                quotation,
                state["verifications"],
                read_as_images=bool(state["content"].images),
            )
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
    graph.add_node("second_read", second_read_node)
    graph.add_node("store", store_node)
    graph.add_edge(START, "load")
    graph.add_edge("load", "extract")
    graph.add_edge("extract", "validate")
    graph.add_conditional_edges("validate", after_validate, ["second_read", "store"])
    graph.add_edge("second_read", "store")
    graph.add_edge("store", END)
    return graph.compile()


def ingest(
    sources: Sequence[Path],
    extractor: Extractor,
    fx_source: FxSource,
    store: Store,
    *,
    second_reader: Extractor | None = None,
    max_concurrency: int = 2,
    max_attempts: int = 3,
) -> None:
    graph = build_graph(extractor, second_reader, fx_source, store, max_attempts)
    inputs = [{"source_path": str(path)} for path in sources]
    results = graph.batch(
        inputs, config={"max_concurrency": max_concurrency}, return_exceptions=True
    )
    # A failure anywhere in one Source's graph is recorded against that Source only.
    for path, result in zip(sources, results, strict=True):
        if isinstance(result, Exception):
            store.save_failed(str(path), file_type(path), _error_message(result))
    # Reconciliation spans every stored Source, including those from earlier runs. It compares
    # what the Sources say, so reviewers' corrections don't hide a Conflict.
    lines = ReadModel(store.db_path).quotation_lines(corrected=False)
    store.save_reconciliation(*reconcile(lines))


def _second_read(
    reader: Extractor, content: DocumentContent, max_attempts: int
) -> Quotation | Exception:
    """Retried like extraction, but a failure is returned, not raised: the second read only
    adds confidence, so losing it (e.g. to a quota error) must not lose the first read."""
    error: Exception | None = None
    for _ in range(max_attempts):
        try:
            return reader(content)
        except STRUCTURED_OUTPUT_ERRORS as malformed:
            error = malformed
        except Exception as unavailable:
            return unavailable
    assert error is not None
    return error


def _cached(fx_source: FxSource, store: Store) -> FxSource:
    """Rates are read from the store first; a fetched rate is saved, so re-runs need no
    network. A missing rate isn't saved: the next run asks again."""

    missing: set[tuple[str, date]] = set()  # asked once per run, so an offline run doesn't stall

    def rate(currency: str, on: date) -> FxRate | None:
        cached = store.fx_rate(currency, on)
        if cached is None and (currency, on) not in missing:
            cached = fx_source(currency, on)
            if cached is None:
                missing.add((currency, on))
            else:
                store.save_fx_rate(currency, on, cached)
        return cached

    return rate


def _error_message(error: Exception) -> str:
    if isinstance(error, NoLoaderError):
        return str(error)
    return f"{type(error).__name__}: {error}"
