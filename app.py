from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dq_agent import ui_support as ui


st.set_page_config(
    page_title="Data Quality Monitor",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --dq-ink: #172033;
        --dq-muted: #5f6b7a;
        --dq-line: #d8dee8;
        --dq-soft: #f6f8fb;
        --dq-teal: #006d77;
        --dq-blue: #2f5f98;
        --dq-amber: #9a6700;
        --dq-red: #b42318;
        --dq-green: #16703c;
    }
    .block-container {padding-top: 1.4rem; padding-bottom: 3rem;}
    h1, h2, h3 {color: var(--dq-ink); letter-spacing: 0;}
    [data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid var(--dq-line);
        border-radius: 8px;
        padding: 14px 16px;
        box-shadow: 0 1px 2px rgba(23, 32, 51, 0.04);
    }
    [data-testid="stMetricLabel"] p {color: var(--dq-muted); font-size: 0.82rem;}
    [data-testid="stMetricValue"] {color: var(--dq-ink); font-weight: 700;}
    .dq-band {
        border: 1px solid var(--dq-line);
        border-radius: 8px;
        padding: 14px 16px;
        background: #ffffff;
        margin: 8px 0 14px 0;
    }
    .dq-subtle {color: var(--dq-muted); font-size: 0.9rem;}
    .dq-demo {
        border-left: 4px solid var(--dq-amber);
        background: #fff8e6;
        color: #4f3600;
        padding: 10px 12px;
        border-radius: 6px;
        margin-bottom: 12px;
    }
    .dq-status {
        display: inline-block;
        border-radius: 999px;
        padding: 3px 10px;
        font-size: 0.78rem;
        font-weight: 700;
        border: 1px solid transparent;
    }
    .dq-pass {background: #e9f7ef; color: var(--dq-green); border-color: #b8dfc5;}
    .dq-fail {background: #fff0ee; color: var(--dq-red); border-color: #f3b4ad;}
    .dq-warn {background: #fff7df; color: var(--dq-amber); border-color: #ead28a;}
    .dq-review {background: #edf4ff; color: var(--dq-blue); border-color: #bed2f1;}
    .dq-neutral {background: #f1f4f8; color: var(--dq-muted); border-color: var(--dq-line);}
    </style>
    """,
    unsafe_allow_html=True,
)


STATUS_CLASS = {
    "Passed": "dq-pass",
    "Failed": "dq-fail",
    "Warning": "dq-warn",
    "Approval Required": "dq-review",
}


def status_badge(status: Any) -> str:
    label = str(status or "Unknown")
    css = STATUS_CLASS.get(label, "dq-neutral")
    return f'<span class="dq-status {css}">{label}</span>'


def dataframe(frame: pd.DataFrame, height: int | None = None) -> None:
    if frame is None or frame.empty:
        st.info("No records available.")
        return
    st.dataframe(frame, width="stretch", hide_index=True, height=height)


def jsonish(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, default=str)
    text = str(value)
    try:
        return json.dumps(json.loads(text), indent=2, default=str)
    except (json.JSONDecodeError, TypeError):
        return text


def init_state() -> None:
    if "inputs_loaded" in st.session_state:
        return
    data = ui.load_runtime_inputs(ROOT)
    st.session_state.table_mappings = data["table_mappings"]
    st.session_state.column_mappings = data["column_mappings"]
    st.session_state.filter_rows = data["filter_rows"]
    st.session_state.human_tests = data["human_tests"]
    st.session_state.context_hints = data["context_hints"]
    st.session_state.llm = data["llm"]
    st.session_state.current_run_id = ui.utc_run_id()
    st.session_state.last_run_id = None
    st.session_state.inputs_loaded = True


def load_run_selector() -> tuple[pd.DataFrame, str | None]:
    history = ui.load_run_history(ROOT)
    if history.empty:
        return history, None
    options = history["run_id"].astype(str).tolist()
    selected = st.sidebar.selectbox("Validation run", options, index=0)
    return history, selected


def page_header(title: str, caption: str | None = None) -> None:
    st.title(title)
    if caption:
        st.caption(caption)


def demo_notice(snapshot: dict[str, Any]) -> None:
    if snapshot.get("is_demo"):
        st.markdown(
            '<div class="dq-demo">Sample monitoring data is shown because no completed validation run exists yet. Real run artifacts automatically replace it.</div>',
            unsafe_allow_html=True,
        )


def dashboard_page(snapshot: dict[str, Any]) -> None:
    page_header("Data Quality Dashboard", "Organization-wide validation health and action summary")
    demo_notice(snapshot)
    metrics = ui.dashboard_metrics(snapshot)
    row1 = st.columns(4)
    for column, label in zip(row1, ["Total tables validated", "Passed tables", "Failed tables", "Tables with warnings"]):
        column.metric(label, metrics[label])
    row2 = st.columns(4)
    for column, label in zip(row2, ["Total checks executed", "Failed checks", "Checks requiring approval", "Latest validation run status"]):
        column.metric(label, metrics[label])

    summary = snapshot["summary"]
    issues = ui.issue_counts(snapshot)
    rca = ui.rca_completion(snapshot)
    left, right = st.columns([1.35, 1])
    with left:
        st.subheader("Data Quality Status By Table")
        display = summary[["Table name", "Overall status", "Failed checks", "Approval required", "Main issue category", "RCA status"]].copy()
        dataframe(display, height=250)
    with right:
        st.subheader("Issue Count By Category")
        if issues.empty:
            st.success("No failed checks in the selected run.")
        else:
            st.bar_chart(issues.set_index("Category"), height=250)

    left, right = st.columns([1, 1])
    with left:
        st.subheader("Top Failed Tables")
        failed = summary.sort_values(["Failed checks", "Approval required"], ascending=False).head(6)
        dataframe(failed[["Table name", "Failed checks", "Approval required", "RCA status"]], height=230)
    with right:
        st.subheader("RCA Completion")
        if rca.empty:
            st.info("No RCA records available.")
        else:
            st.bar_chart(rca.set_index("RCA status"), height=230)

    st.subheader("Recent Validation Runs")
    history = snapshot.get("history", pd.DataFrame())
    if history.empty:
        st.info("No validation run history found.")
    else:
        cols = [column for column in ["run_id", "status", "started_at", "completed_at", "tables", "approval_proposals"] if column in history]
        dataframe(history[cols].head(8), height=260)


def configure_page() -> None:
    page_header("Configure & Run", "Runtime configuration, execution controls, and latest run progress")
    init_state()

    with st.expander("Import existing or uploaded inputs", expanded=False):
        cols = st.columns(4)
        table_upload = cols[0].file_uploader("Table mappings", type=["xlsx", "xls", "csv"], key="table_upload")
        column_upload = cols[1].file_uploader("Column mappings", type=["xlsx", "xls", "csv"], key="column_upload")
        overrides_upload = cols[2].file_uploader("Runtime overrides", type=["yaml", "yml"], key="overrides_upload")
        tests_upload = cols[3].file_uploader("Human tests", type=["yaml", "yml"], key="tests_upload")
        if st.button("Load uploaded files"):
            try:
                base = ui.load_runtime_inputs(ROOT)
                context_tables = base["context_tables"]
                if table_upload is not None:
                    st.session_state.table_mappings = ui._ensure_columns(ui.read_tabular_upload(table_upload), ui.TABLE_MAPPING_COLUMNS)
                if column_upload is not None:
                    st.session_state.column_mappings = ui._ensure_columns(ui.read_tabular_upload(column_upload), ui.COLUMN_MAPPING_COLUMNS)
                if overrides_upload is not None:
                    st.session_state.filter_rows = ui.filters_to_frame(ui.read_yaml_upload(overrides_upload), context_tables)
                if tests_upload is not None:
                    tests_yaml = ui.read_yaml_upload(tests_upload)
                    st.session_state.human_tests = ui.human_tests_to_frame(tests_yaml.get("tests", []))
                st.success("Uploaded inputs loaded into the UI session.")
            except Exception as exc:
                st.error(str(exc))
        if st.button("Reload repository inputs"):
            data = ui.load_runtime_inputs(ROOT)
            st.session_state.table_mappings = data["table_mappings"]
            st.session_state.column_mappings = data["column_mappings"]
            st.session_state.filter_rows = data["filter_rows"]
            st.session_state.human_tests = data["human_tests"]
            st.session_state.context_hints = data["context_hints"]
            st.session_state.llm = data["llm"]
            st.success("Repository inputs reloaded.")

    with st.expander("Validation setup", expanded=True):
        st.subheader("Table Mappings")
        st.session_state.table_mappings = st.data_editor(
            st.session_state.table_mappings,
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "enabled": st.column_config.CheckboxColumn("enabled"),
                "mode": st.column_config.SelectboxColumn("mode", options=["migration", "bigquery_only"]),
            },
            key="table_mapping_editor",
        )
        st.subheader("Column Mappings")
        st.session_state.column_mappings = st.data_editor(
            st.session_state.column_mappings,
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "status": st.column_config.SelectboxColumn("status", options=["manual", "approved", "confirmed", "draft", "pending", "rejected"]),
            },
            key="column_mapping_editor",
        )

    with st.expander("Filters, context hints, and human tests", expanded=True):
        st.subheader("Business Filters And SCD Current Filters")
        st.session_state.filter_rows = st.data_editor(
            st.session_state.filter_rows,
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "enabled": st.column_config.CheckboxColumn("enabled"),
                "kind": st.column_config.SelectboxColumn("kind", options=["Runtime filter", "SCD current filter"]),
                "side": st.column_config.SelectboxColumn("side", options=["source", "target"]),
                "operator": st.column_config.SelectboxColumn("operator", options=["eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "is_null", "not_null"]),
            },
            key="filter_editor",
        )
        st.subheader("Primary Keys, Audit Columns, And Business Context")
        st.session_state.context_hints = st.data_editor(
            st.session_state.context_hints,
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "table_type": st.column_config.SelectboxColumn("table_type", options=["fact", "dimension", "bridge", "reference", "aggregate", "audit", "unknown"]),
                "scd2_enabled": st.column_config.CheckboxColumn("scd2_enabled"),
            },
            key="context_hint_editor",
        )
        st.subheader("Human Added Test Cases")
        st.session_state.human_tests = st.data_editor(
            st.session_state.human_tests,
            width="stretch",
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "enabled": st.column_config.CheckboxColumn("enabled"),
                "type": st.column_config.SelectboxColumn("type", options=["null", "accepted_values", "row_count", "duplicate", "freshness", "aggregate", "custom_sql"]),
                "scope": st.column_config.SelectboxColumn("scope", options=["source", "target", "both", "compare"]),
                "aggregation": st.column_config.SelectboxColumn("aggregation", options=["sum", "avg", "min", "max", "count"]),
                "comparison": st.column_config.SelectboxColumn("comparison", options=["eq", "gte", "lte"]),
            },
            key="human_test_editor",
        )

    with st.expander("Relationships, measures, and reusable assets", expanded=False):
        assets = ui.context_asset_summaries(ROOT)
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Dimension Registry")
            dataframe(assets["dimensions"], height=220)
            st.subheader("Custom Relationships")
            dataframe(assets["custom_relationships"], height=220)
        with c2:
            st.subheader("Measures And KPIs")
            dataframe(assets["measures"], height=460)

    with st.expander("Run settings and LLM configuration", expanded=True):
        left, right = st.columns([1, 1])
        with left:
            st.session_state.current_run_id = st.text_input("Run ID", value=st.session_state.current_run_id)
            if st.button("Create new run ID"):
                st.session_state.current_run_id = ui.utc_run_id()
                st.rerun()
            stop_after = st.selectbox("Run through", ["metadata", "inference", "rules", "execution", "rca", "reports"], index=5)
        with right:
            llm = st.session_state.llm
            backend = st.selectbox("LLM backend", ["ollama", "vertex", "openai", "anthropic"], index=["ollama", "vertex", "openai", "anthropic"].index(llm.get("backend", "ollama")))
            api_family = st.selectbox("API family", ["openai_compatible", "google_genai", "anthropic"], index=["openai_compatible", "google_genai", "anthropic"].index(llm.get("api_family", "openai_compatible")))
            model = st.text_input("Model", value=str(llm.get("model") or ""))
            endpoint = st.text_input("Endpoint", value=str(llm.get("endpoint") or ""))
            temperature = st.number_input("Temperature", min_value=0.0, max_value=2.0, value=float(llm.get("temperature", 0.0)), step=0.1)
            timeout = st.number_input("Timeout seconds", min_value=10, max_value=900, value=int(llm.get("timeout_seconds", 90)), step=10)

        llm_overrides = {
            "backend": backend,
            "api_family": api_family,
            "model": model,
            "endpoint": endpoint or None,
            "temperature": float(temperature),
            "timeout_seconds": int(timeout),
        }
        c1, c2 = st.columns([1, 1])
        if c1.button("Generate UI run configuration"):
            try:
                files = ui.prepare_ui_run(
                    ROOT,
                    st.session_state.current_run_id,
                    st.session_state.table_mappings,
                    st.session_state.column_mappings,
                    st.session_state.filter_rows,
                    st.session_state.human_tests,
                    st.session_state.context_hints,
                    llm_overrides,
                )
                st.session_state.last_run_id = files.run_id
                st.success(f"UI run configuration generated under {files.ui_inputs_dir}")
            except Exception as exc:
                st.error(str(exc))
        if c2.button("Start validation run", type="primary"):
            try:
                with st.spinner("Validation run in progress. Logs are written under logs/."):
                    manifest = ui.run_workflow_from_ui(
                        ROOT,
                        st.session_state.current_run_id,
                        st.session_state.table_mappings,
                        st.session_state.column_mappings,
                        st.session_state.filter_rows,
                        st.session_state.human_tests,
                        st.session_state.context_hints,
                        llm_overrides,
                        stop_after,
                    )
                st.session_state.last_run_id = manifest.get("run_id")
                st.success(f"Run finished with status {manifest.get('status')}")
            except Exception as exc:
                st.session_state.last_run_id = st.session_state.current_run_id
                st.error(str(exc))

    run_id = st.session_state.last_run_id or st.session_state.current_run_id
    progress = ui.run_progress(ROOT, run_id)
    st.subheader("Run Progress")
    cols = st.columns(4)
    manifest = progress["manifest"]
    checkpoint = progress["checkpoint"]
    cols[0].metric("Run ID", run_id)
    cols[1].metric("Status", manifest.get("status", "Not started"))
    cols[2].metric("Current stage", checkpoint.get("current_stage") or checkpoint.get("last_completed_stage") or "None")
    cols[3].metric("Tables processed", len(manifest.get("tables") or []))
    events = pd.DataFrame(progress["events"])
    if not events.empty:
        dataframe(events.tail(12), height=280)
    if manifest.get("error"):
        st.error(manifest["error"])


def report_page(snapshot: dict[str, Any]) -> None:
    page_header("Data Quality Report", "Table-level summary, investigation detail, RCA, and recommended actions")
    demo_notice(snapshot)
    summary = snapshot["summary"]
    results = snapshot["results"]
    rca = snapshot["rca"]
    approvals = snapshot["approvals"]
    report = snapshot.get("report", {})

    st.subheader("Validated Tables")
    display_cols = ["Table name", "Overall status", "Total checks", "Passed checks", "Failed checks", "Warning checks", "Approval required", "Main issue category", "RCA status"]
    dataframe(summary[display_cols] if not summary.empty else summary, height=280)
    if summary.empty:
        return

    selected_table = st.selectbox("Table deep dive", summary["Table name"].tolist())
    selected = summary[summary["Table name"] == selected_table].iloc[0].to_dict()
    pair_id = selected.get("Pair ID")
    st.markdown(status_badge(selected.get("Overall status")), unsafe_allow_html=True)
    cols = st.columns(4)
    cols[0].metric("Checks", selected.get("Total checks", 0))
    cols[1].metric("Failed", selected.get("Failed checks", 0))
    cols[2].metric("Approvals", selected.get("Approval required", 0))
    cols[3].metric("RCA", selected.get("RCA status", "Not required"))

    st.markdown('<div class="dq-band">', unsafe_allow_html=True)
    st.write({
        "source_table": selected.get("Source table"),
        "target_table": selected.get("Target table"),
        "main_issue_category": selected.get("Main issue category"),
    })
    st.markdown('</div>', unsafe_allow_html=True)

    mappings = report.get("Mappings", pd.DataFrame())
    pair_mappings = mappings[mappings["pair_id"].astype(str) == str(pair_id)] if not mappings.empty and "pair_id" in mappings else pd.DataFrame()
    if not pair_mappings.empty:
        st.subheader("Table And Column Mapping Summary")
        cols_to_show = [column for column in ["source_column", "target_column", "status", "confidence", "rationale"] if column in pair_mappings]
        dataframe(pair_mappings[cols_to_show], height=220)

    pair_results = results[results["pair_id"].astype(str) == str(pair_id)] if not results.empty and "pair_id" in results else pd.DataFrame()
    if not pair_results.empty:
        st.subheader("Rule Execution Results")
        pair_results = pair_results.copy()
        pair_results["quality_category"] = pair_results.apply(lambda row: ui.ui_category(row.to_dict()), axis=1)
        for category in ui.UI_CATEGORIES:
            category_frame = pair_results[pair_results["quality_category"] == category]
            if category_frame.empty:
                continue
            with st.expander(category, expanded=category_frame["status"].astype(str).str.upper().isin(["FAIL", "ERROR"]).any()):
                cols_to_show = [column for column in ["rule_id", "status", "type", "severity", "origin", "description"] if column in category_frame]
                dataframe(category_frame[cols_to_show], height=220)

        failed = pair_results[pair_results["status"].astype(str).str.upper().isin(["FAIL", "ERROR"])]
        st.subheader("Failed Checks And RCA")
        if failed.empty:
            st.success("No failed checks for the selected table.")
        for _, row in failed.iterrows():
            rule_id = str(row.get("rule_id"))
            title = f"{rule_id} | {ui.ui_category(row.to_dict())} | {row.get('status')}"
            with st.expander(title, expanded=True):
                st.write(row.get("description") or "No description available.")
                c1, c2 = st.columns(2)
                with c1:
                    st.caption("Evidence")
                    st.code(jsonish(row.get("evidence")), language="json")
                with c2:
                    st.caption("Comparison")
                    st.code(jsonish(row.get("comparison")), language="json")
                if not rca.empty and "rule_id" in rca:
                    matches = rca[rca["rule_id"].astype(str) == rule_id]
                else:
                    matches = pd.DataFrame()
                if matches.empty:
                    st.warning("RCA pending for this failed check.")
                else:
                    rca_row = matches.iloc[0].to_dict()
                    st.caption("RCA conclusion")
                    st.write({
                        "classification": rca_row.get("conclusion.classification") or rca_row.get("classification"),
                        "confidence": rca_row.get("conclusion.confidence") or rca_row.get("confidence"),
                        "conclusion": rca_row.get("conclusion.conclusion") or rca_row.get("conclusion"),
                        "diagnostics": rca_row.get("diagnostics"),
                    })
                    st.info("Recommended action: review the evidence, correct the upstream or mapping condition, and approve reusable RCA knowledge if the conclusion is useful for future runs.")

    pair_approvals = approvals[approvals["pair_id"].astype(str) == str(pair_id)] if not approvals.empty and "pair_id" in approvals else pd.DataFrame()
    st.subheader("Contextual Approvals")
    dataframe(pair_approvals, height=220)


def context_approvals_page() -> None:
    page_header("Context & Approvals", "Reusable knowledge, proposed inferences, reviewer actions, and approved context reuse")
    st.subheader("Reusable Context Browser")
    try:
        records = ui.load_context_records(ROOT)
    except Exception as exc:
        records = pd.DataFrame()
        st.error(str(exc))
    if not records.empty:
        c1, c2, c3 = st.columns([1, 1, 2])
        type_options = ["All", *sorted(records["context_type"].dropna().astype(str).unique())]
        origin_options = ["All", *sorted(records["origin"].dropna().astype(str).unique())]
        selected_type = c1.selectbox("Context type", type_options)
        selected_origin = c2.selectbox("Origin", origin_options)
        query = c3.text_input("Search context")
        filtered = records.copy()
        if selected_type != "All":
            filtered = filtered[filtered["context_type"].astype(str) == selected_type]
        if selected_origin != "All":
            filtered = filtered[filtered["origin"].astype(str) == selected_origin]
        if query:
            mask = filtered.astype(str).apply(lambda col: col.str.contains(query, case=False, na=False)).any(axis=1)
            filtered = filtered[mask]
        cols = [column for column in ["record_id", "context_type", "subject_key", "pair_id", "target_table", "column_name", "business_entity", "origin", "confidence"] if column in filtered]
        dataframe(filtered[cols], height=310)
        if not filtered.empty:
            record_id = st.selectbox("Context detail", filtered["record_id"].astype(str).tolist())
            detail = filtered[filtered["record_id"].astype(str) == record_id].iloc[0].to_dict()
            st.code(detail.get("payload", ""), language="json")

    with st.expander("Add approved reusable context", expanded=False):
        with st.form("manual_context_form"):
            c1, c2, c3 = st.columns(3)
            context_type = c1.text_input("Context type", value="business_rule")
            subject_key = c2.text_input("Subject key", value="")
            confidence = c3.number_input("Confidence", min_value=0.0, max_value=1.0, value=1.0, step=0.05)
            pair_id = st.text_input("Pair ID", value="")
            target_table = st.text_input("Target table", value="")
            column_name = st.text_input("Column name", value="")
            payload = st.text_area("Payload JSON", value='{"description": ""}', height=130)
            reviewer = st.text_input("Reviewed by", value="ui_reviewer")
            comments = st.text_area("Reviewer comments", value="Approved through Context & Approvals UI", height=80)
            submitted = st.form_submit_button("Publish approved context")
        if submitted:
            try:
                written = ui.add_approved_context_record(ROOT, {
                    "context_type": context_type,
                    "subject_key": subject_key,
                    "payload": payload,
                    "pair_id": pair_id,
                    "target_table": target_table,
                    "column_name": column_name,
                    "confidence": confidence,
                    "reviewed_by": reviewer,
                    "reviewer_comments": comments,
                })
                st.success(f"Published {written} context record.")
            except Exception as exc:
                st.error(str(exc))

    st.subheader("Pending Approval Workbooks")
    pending = ui.list_approval_workbooks(ROOT, "pending")
    if not pending:
        st.info("No pending approval workbooks found.")
    else:
        selected = st.selectbox("Approval workbook", pending, format_func=lambda path: path.name)
        try:
            frame = ui.pending_approval_frame(selected)
            edited = st.data_editor(
                frame,
                width="stretch",
                hide_index=True,
                num_rows="fixed",
                column_config={
                    "approval_status": st.column_config.SelectboxColumn(
                        "approval_status",
                        options=["PENDING", "APPROVE", "REJECT", "NEEDS_CHANGES", "OVERRIDE"],
                    ),
                    "reviewer_comments": st.column_config.TextColumn("reviewer_comments", width="large"),
                    "reviewed_by": st.column_config.TextColumn("reviewed_by"),
                },
                key=f"approval_editor_{selected.name}",
            )
            c1, c2 = st.columns(2)
            if c1.button("Save reviewer decisions"):
                ui.save_approval_frame(selected, edited)
                st.success("Approval workbook saved.")
            if c2.button("Publish reviewed decisions", type="primary"):
                result = ui.publish_approval_decisions(ROOT, selected, edited)
                st.success(f"Published {result.get('approved', 0)} approved context records.")
                st.write(result)
        except Exception as exc:
            st.error(str(exc))

    with st.expander("Approval history", expanded=False):
        history_rows = []
        for state in ("processed", "archive", "reviewed"):
            for path in ui.list_approval_workbooks(ROOT, state):
                history_rows.append({"state": state, "file": path.name, "path": str(path)})
        dataframe(pd.DataFrame(history_rows), height=220)


def main() -> None:
    st.sidebar.title("DQ Monitor")
    page = st.sidebar.radio(
        "Navigation",
        ["Dashboard", "Configure & Run", "Data Quality Report", "Context & Approvals"],
        index=0,
    )
    _, selected_run = load_run_selector()
    snapshot = ui.load_monitoring_snapshot(ROOT, selected_run)
    if page == "Dashboard":
        dashboard_page(snapshot)
    elif page == "Configure & Run":
        configure_page()
    elif page == "Data Quality Report":
        report_page(snapshot)
    else:
        context_approvals_page()


if __name__ == "__main__":
    main()
