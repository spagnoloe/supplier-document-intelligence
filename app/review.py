"""Review UI over the read model: `uv run streamlit run app/review.py`.

A POC: production would put an API service in front of the read model and Store and give it a
proper frontend. This page holds no business logic. Everything it shows comes from ReadModel,
and its only writes are Store's review decisions.
"""

import io
import json
from pathlib import Path
from typing import get_args

import streamlit as st

from sdi.config import database_path
from sdi.export import export_csv
from sdi.read_model import LineStatus, LineView, ReadModel
from sdi.store import CORRECTABLE_FIELDS, Store

LINE_DETAIL = "Line detail"
STATUS_ICONS = {"needs_review": "🟠", "auto_ok": "🟢", "reviewed": "✅"}


def main() -> None:
    st.set_page_config(page_title="Quotation review", layout="wide")
    db_path = database_path()
    if not db_path.is_file():
        st.error(f"No database at {db_path}; run `sdi ingest <folder>` first.")
        st.stop()
    store, read_model = _open(db_path)
    if "line_id" in st.session_state:
        # Streamlit forgets a widget's value while it isn't drawn; keep the open line across
        # views.
        st.session_state.line_id = st.session_state.line_id

    views = {
        "Review queue": lambda: review_queue(read_model),
        LINE_DETAIL: lambda: line_detail(read_model, store),
        "Conflicts": lambda: conflicts(read_model),
        "Price comparison": lambda: price_comparison(read_model),
        "Source status": lambda: source_status(read_model),
        "CSV export": lambda: csv_export(read_model),
    }
    with st.sidebar:
        st.title("Quotation review")
        view = st.radio("View", views, key="view")
        st.text_input("Reviewer", key="reviewer", help="Recorded with every decision.")
        st.caption(f"Database: `{db_path}`")

    if message := st.session_state.pop("flash", None):
        st.success(message)

    views[view]()


@st.cache_resource
def _open(db_path: Path) -> tuple[Store, ReadModel]:
    # Store first: it brings a database from an earlier schema up to date, once per session.
    return Store(db_path), ReadModel(db_path)


def review_queue(read_model: ReadModel) -> None:
    st.header("Review queue")
    all_lines = read_model.quotation_lines()
    columns = st.columns(4)
    supplier = columns[0].selectbox(
        "Supplier", _options(line.supplier for line in all_lines), index=None
    )
    review_status = columns[1].selectbox("Review status", get_args(LineStatus), index=None)
    source = columns[2].selectbox(
        "Source", _options(line.source for line in all_lines), index=None, format_func=_name
    )
    reason_code = columns[3].selectbox(
        "Reason code",
        _options(r.code for line in all_lines for r in line.review_reasons),
        index=None,
    )
    lines = read_model.review_queue(
        supplier=supplier, review_status=review_status, source=source, reason_code=reason_code
    )
    st.caption(f"{len(lines)} lines. Select a row to open it.")
    _lines_table(
        lines,
        "queue",
        lambda line: {
            "Status": _status(line.review_status),
            "Reasons": sorted({r.code for r in line.review_reasons}),
            "Supplier": line.supplier,
            "Product": _product(line),
            "Unit price": line.headline_unit_price,
            "Pack price": line.headline_pack_price,
            "Currency": line.currency,
            "USD per UOM": line.usd_per_uom,
            "Source": _name(line.source),
            "Line": line.line_no,
        },
    )


def line_detail(read_model: ReadModel, store: Store) -> None:
    st.header(LINE_DETAIL)
    labels = {line.id: _line_label(line) for line in read_model.quotation_lines()}
    if not labels:
        st.info("No Quotation lines yet.")
        return
    if st.session_state.get("line_id") not in labels:
        st.session_state.line_id = next(iter(labels))
    line_id = st.selectbox("Line", labels, key="line_id", format_func=labels.__getitem__)
    detail = read_model.line_detail(line_id)
    line = detail.line

    st.subheader(f"{_status(line.review_status)} · {_product(line)}")
    st.caption(
        f"{line.supplier or 'Unknown supplier'} · quote {line.quote_ref or '—'} · "
        f"Incoterm {_incoterm(line) or '—'} · {line.source}, line {line.line_no}"
    )
    for reason in line.review_reasons:
        field = f" ({reason.field})" if reason.field else ""
        st.warning(f"**{reason.code}**{field}: {reason.message}")

    values, image = st.columns([3, 2]) if detail.source_image else (st.container(), None)
    with values:
        original, calculated = st.columns(2)
        with original:
            st.markdown("**Stated values**, with corrections")
            st.table(
                _pairs(
                    line,
                    "uom pack_size unit_price pack_price currency discount_pct quantity line_total",
                )
            )
            if line.price_tiers:
                st.markdown("Price tiers")
                st.dataframe(line.price_tiers, hide_index=True)
        with calculated:
            st.markdown("**Calculated values**")
            st.table(
                _pairs(
                    line,
                    "headline_unit_price headline_pack_price net_unit_price net_pack_price "
                    "usd_per_uom fx_rate fx_rate_date fx_source",
                )
            )
        st.markdown("**Fields: verification and evidence**")
        st.dataframe(
            [
                {
                    "Field": f.field,
                    "Result": f.result,
                    "Value": _text(f.value),
                    "Extracted": _text(f.extracted_value)
                    if f.field in line.extracted_values
                    else "",
                    "Evidence": "\n".join(
                        f"[{e.certainty}] {e.span}" if e.span else f"[{e.certainty}]"
                        for e in f.evidence
                    ),
                    "Reasons": [r.code for r in f.reasons],
                }
                for f in detail.fields
            ],
            hide_index=True,
            column_config={"Reasons": st.column_config.ListColumn()},
        )
    if image is not None and detail.source_image:
        with image:
            st.markdown("**Source as read by the model**")
            try:
                pages = _source_images(read_model, detail.source_image)
            except OSError as e:
                st.info(f"Can't show {detail.source_image}: {e.strerror or e}")
            else:
                for page in pages:
                    st.image(page)

    _review_actions(store, line)

    if decisions := read_model.decisions(line_id):
        st.markdown("**Decisions on this line**")
        st.dataframe(
            [
                {
                    "When": d.decided_at,
                    "Reviewer": d.reviewer,
                    "Action": d.action,
                    "Field": d.field,
                    "Old": _text(d.old_value),
                    "New": _text(d.new_value),
                }
                for d in decisions
            ],
            hide_index=True,
        )


def _review_actions(store: Store, line: LineView) -> None:
    reviewer = st.session_state.get("reviewer", "").strip()
    st.markdown("**Review**")
    if not reviewer:
        st.info("Enter your name as Reviewer in the sidebar to approve or correct.")
    approve, correct = st.columns([1, 3])
    if approve.button("Approve", type="primary", disabled=not reviewer):
        store.approve(line.id, reviewer=reviewer)
        st.session_state.flash = f"Approved line {line.line_no} of {_name(line.source)}."
        st.rerun()
    with correct:
        field = st.selectbox("Field to correct", sorted(CORRECTABLE_FIELDS))
        current = getattr(line, field)
        with st.form(f"correct-{line.id}-{field}", border=False):
            text = st.text_area(
                "Value",
                value=_text(current),
                help="Lists are JSON; leave empty to clear the field.",
            )
            if st.form_submit_button("Correct", disabled=not reviewer):
                try:
                    store.correct(line.id, field, _parse(text, current), reviewer=reviewer)
                except ValueError as e:
                    st.error(f"Not a valid {field}: {e}")
                else:
                    st.session_state.flash = f"Corrected {field} on line {line.line_no}."
                    st.rerun()


def conflicts(read_model: ReadModel) -> None:
    st.header("Conflicts")
    conflict_views = read_model.conflicts()
    if not conflict_views:
        st.info("No Conflicts: no two Sources of one Quotation disagree.")
    for conflict in conflict_views:
        term = conflict.scope == "quotation"
        with st.container(border=True):
            st.subheader(f"{conflict.field} · {'Quotation term' if term else _product(conflict)}")
            st.caption(f"{conflict.supplier or 'Unknown supplier'} · quote {conflict.quote_ref}")
            if term:
                st.caption("Applies to every line of each Source, so all of them are in review.")
            for column, value in zip(
                st.columns(len(conflict.values)), conflict.values, strict=True
            ):
                with column:
                    if value.line_id is None:
                        st.markdown(f"**{_name(value.source)}**")
                        st.code(_text(value.value), language=None)
                        continue
                    st.markdown(f"**{_name(value.source)}**, line {value.line_no}")
                    st.code(_text(value.value), language=None)
                    st.button(
                        "Open line",
                        key=f"conflict-{conflict.id}-{value.line_id}",
                        on_click=_open_line,
                        args=(value.line_id,),
                    )


def price_comparison(read_model: ReadModel) -> None:
    st.header("Price comparison")
    st.caption(
        "USD per UOM is before discount, at the lowest-volume tier. Select a row to open it."
    )
    comparisons = read_model.price_comparisons()
    if not comparisons:
        st.info("No Product is quoted in more than one Quotation.")
    for comparison in comparisons:
        with st.container(border=True):
            st.subheader(_product(comparison))
            if comparison.incoterm_warning:
                st.warning(comparison.incoterm_warning)
            _lines_table(
                comparison.lines,
                f"comparison-{comparison.id}",
                lambda line: {
                    "Supplier": line.supplier,
                    "Quote": line.quote_ref,
                    "Incoterm": _incoterm(line),
                    "USD per UOM": line.usd_per_uom,
                    "Unit price": line.headline_unit_price,
                    "Currency": line.currency,
                    "FX rate": line.fx_rate,
                    "FX date": line.fx_rate_date,
                    "UOM": line.uom,
                    "Status": _status(line.review_status),
                    "Source": _name(line.source),
                },
            )


def source_status(read_model: ReadModel) -> None:
    st.header("Source status")
    statuses = read_model.document_statuses()
    st.dataframe(
        [
            {
                "Status": s.status,
                "Source": _name(s.source),
                "File type": s.file_type,
                "Document type": s.document_type,
                "Lines": s.line_count,
                "Needs review": s.needs_review_count,
                "Error": s.error,
            }
            for s in statuses
        ],
        hide_index=True,
    )


def csv_export(read_model: ReadModel) -> None:
    st.header("CSV export")
    st.write("Every Quotation line with corrections applied, as `sdi export` writes it.")
    out = io.StringIO()
    export_csv(read_model, out)
    st.download_button("Download CSV", out.getvalue(), "quotation_lines.csv", "text/csv")


def _lines_table(lines: list[LineView], key: str, to_row) -> None:
    """A table of lines, one row each; selecting a row opens it in Line detail."""
    st.dataframe(
        [to_row(line) for line in lines],
        hide_index=True,
        key=key,
        on_select=lambda: _open_selected(key, lines),
        selection_mode="single-row",
        column_config={"Reasons": st.column_config.ListColumn()},
    )


def _open_selected(key: str, lines: list[LineView]) -> None:
    if rows := st.session_state[key].selection.rows:
        _open_line(lines[rows[0]].id)


def _open_line(line_id: int) -> None:
    # Runs as a widget callback, before the next run draws the view radio and line selectbox.
    st.session_state.line_id = line_id
    st.session_state.view = LINE_DETAIL


@st.cache_data(hash_funcs={ReadModel: lambda read_model: read_model.db_path})
def _source_images(read_model: ReadModel, source: str) -> list[bytes]:
    return read_model.source_images(source)


def _parse(text: str, current: object) -> object:
    """The reviewer's text as a value for Store.correct, which validates it: JSON for a list
    field, None when empty, else the text as typed."""
    text = text.strip()
    if isinstance(current, list):
        return json.loads(text) if text else []
    return text or None


def _product(of) -> str:
    """INNs with strengths, and dosage form, of a line, Conflict or Price comparison."""
    ingredients = " + ".join(
        " ".join(
            str(part)
            for part in (i["inn"], _text(i.get("strength_value")), i.get("strength_unit"))
            if part
        )
        for i in of.active_ingredients
    )
    return " · ".join(part for part in (ingredients, of.dosage_form) if part) or "Unknown product"


def _line_label(line: LineView) -> str:
    return (
        f"{_status(line.review_status)} · {_product(line)} · {_name(line.source)} #{line.line_no}"
    )


def _incoterm(line: LineView) -> str | None:
    return " ".join(part for part in (line.incoterm, line.incoterm_place) if part) or None


def _status(status: str) -> str:
    return f"{STATUS_ICONS.get(status, '')} {status}"


def _pairs(line: LineView, fields: str) -> list[dict[str, str]]:
    """Each field's current value, and the extracted value of those a reviewer corrected."""
    names = fields.split()
    corrected = [f for f in names if f in line.extracted_values]
    return [
        {"Field": f, "Value": _text(getattr(line, f))}
        | ({"Extracted": _text(line.extracted_values.get(f))} if corrected else {})
        for f in names
    ]


def _options(values) -> list:
    return sorted({v for v in values if v is not None})


def _name(path: str) -> str:
    return Path(path).name


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list | dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


main()
