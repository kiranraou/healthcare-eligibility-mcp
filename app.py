"""Streamlit UI for the Healthcare Eligibility MCP project.

    streamlit run app.py
"""

import asyncio
import os
from datetime import date

import anthropic
import pandas as pd
import streamlit as st

from src import agent, db, llm, service
from src.config import get_settings
from src.pipeline import medallion
from src.seed import ensure_seeded

st.set_page_config(page_title="Healthcare Eligibility", page_icon="🩺", layout="wide")
ensure_seeded()
settings = get_settings()

# Status colors are reserved for status and always paired with an icon and label.
STATUS = {
    "ELIGIBLE": ("✅", "Eligible", "#0ca30c"),
    "ELIGIBLE_WITH_ISSUES": ("⚠️", "Eligible with issues", "#fab219"),
    "INELIGIBLE": ("⛔", "Ineligible", "#ec835a"),
    "REJECTED": ("❌", "Rejected", "#d03b3b"),
    "PAYER_UNAVAILABLE": ("🔌", "Payer unavailable", "#8a8984"),
}
RISK = {"high": "🔴 High", "medium": "🟠 Medium", "low": "🟢 Low", "unknown": "⚪ Unknown"}
RISK_ORDER = {"high": 0, "unknown": 1, "medium": 2, "low": 3}
SEVERITY = {"error": "❌ Error", "warning": "⚠️ Warning", "info": "ℹ️ Info"}
SERIES_1 = "#2a78d6"


def status_label(status: str) -> str:
    icon, label, _ = STATUS.get(status, ("", status, ""))
    return f"{icon} {label}"


def money(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def anthropic_error(exc: Exception) -> str:
    if isinstance(exc, anthropic.APIStatusError):
        body = exc.body if isinstance(exc.body, dict) else {}
        return body.get("error", {}).get("message") or str(exc)
    return str(exc)


# ---------------------------------------------------------------- sidebar

patients = db.list_patients()
labels = {}
for p in patients:
    coverage = db.get_primary_coverage(p.patient_id)
    labels[p.patient_id] = f"{p.patient_id} · {p.first_name} {p.last_name} ({coverage.payer_id if coverage else 'no coverage'})"

with st.sidebar:
    st.header("Eligibility check")
    patient_id = st.selectbox("Patient", list(labels), format_func=labels.get)
    dos = st.date_input("Date of service", value=date.today())
    rendering_npi = st.text_input("Rendering provider NPI (optional)", placeholder="e.g. 1801234567").strip()
    record = st.toggle("Record for analytics", value=True, help="Save the check so the pipeline can load it.")
    if st.button("Run eligibility check", type="primary", use_container_width=True):
        st.session_state.validation = service.validate_request(patient_id, dos)
        st.session_state.result = service.check_eligibility(
            patient_id, dos, rendering_npi or None, save=record
        )
        st.session_state.explanation = None
    st.divider()
    st.caption(f"Provider: {settings.provider_name} · NPI {settings.provider_npi}")
    st.caption("Databricks: " + ("configured" if settings.databricks_enabled else "not configured"))
    st.caption("All data is synthetic.")

st.title("🩺 Healthcare Eligibility")
check_tab, worklist_tab, analytics_tab, ai_tab = st.tabs(
    ["Eligibility check", "Worklist", "Analytics", "AI assistant"]
)

# ---------------------------------------------------------------- eligibility check

with check_tab:
    result = st.session_state.get("result")
    if result is None:
        st.info("Pick a patient in the sidebar and click **Run eligibility check**.")
    else:
        patient, coverage = result["patient"], result["coverage_on_file"]
        analysis, benefits = result["denial_analysis"], result["benefits"]
        st.subheader(f"{patient['first_name']} {patient['last_name']} · {coverage['payer_name']}")

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Status", status_label(result["status"]))
        k2.metric("Denial risk", RISK.get(analysis["denial_risk"], analysis["denial_risk"]))
        k3.metric("Office visit copay", money(benefits["office_visit_copay"]))
        k4.metric("Deductible remaining", money(benefits["deductible_remaining"]))

        st.markdown("#### Denial analysis")
        if analysis["category"]:
            carc = ", ".join(analysis["carc_codes"]) or "none"
            st.markdown(f"**{analysis['category']}** · CARC {carc}")
        st.write(analysis["root_cause"])
        st.dataframe(
            pd.DataFrame(analysis["actions"]).rename(columns=str.title),
            hide_index=True, use_container_width=True,
        )

        if result["findings"]:
            st.markdown("#### Rule findings")
            findings = pd.DataFrame(result["findings"])[["severity", "rule", "message"]]
            findings["severity"] = findings["severity"].map(SEVERITY)
            st.dataframe(findings.rename(columns=str.title), hide_index=True, use_container_width=True)

        left, right = st.columns(2)
        with left:
            st.markdown("### ➡️ Input")
            validation = st.session_state.get("validation") or {}
            for error in validation.get("errors", []):
                st.error(error)
            for warning in validation.get("warnings", []):
                st.warning(warning)
            if validation.get("valid") and not validation.get("warnings"):
                st.success("Request passed pre-submission validation.")
            st.markdown("**Patient**")
            st.json(patient, expanded=False)
            st.markdown("**Coverage on file (registration)**")
            st.json(coverage, expanded=False)
            st.markdown("**X12 270 request**")
            st.code(result["x12_270"], language=None)
        with right:
            st.markdown("### ⬅️ Output")
            payer = result["payer_response"]
            for reject in payer["rejections"]:
                st.error(f"AAA {reject['code']}: {reject['description']} — {reject['follow_up_description']}")
            st.markdown("**Benefits (from 271)**")
            st.dataframe(
                pd.DataFrame([
                    ("Coverage active", "Yes" if benefits["coverage_active"] else "No"),
                    ("Plan", benefits["plan"] or "—"),
                    ("Plan begin / end", f"{payer['plan_begin'] or '—'} / {payer['plan_end'] or '—'}"),
                    ("Member ID (payer)", payer["member_id"] or "—"),
                    ("Office visit copay", money(benefits["office_visit_copay"])),
                    ("Deductible", money(benefits["deductible"])),
                    ("Deductible remaining", money(benefits["deductible_remaining"])),
                    ("Coinsurance", "—" if benefits["coinsurance_pct"] is None else f"{benefits['coinsurance_pct']}%"),
                    ("Out-of-pocket max", money(benefits["out_of_pocket_max"])),
                    ("Out-of-pocket remaining", money(benefits["out_of_pocket_remaining"])),
                    ("Payer messages", "; ".join(benefits["messages"]) or "—"),
                ], columns=["Field", "Value"]),
                hide_index=True, use_container_width=True,
            )
            st.markdown("**X12 271 response**")
            st.code(result["x12_271"], language=None)

        st.divider()
        if st.button("✨ Explain this result with Claude"):
            with st.spinner("Asking Claude…"):
                try:
                    st.session_state.explanation = llm.explain_eligibility(result)
                except Exception as exc:
                    st.session_state.explanation = None
                    st.error(f"Claude is unavailable: {anthropic_error(exc)}")
        if st.session_state.get("explanation"):
            st.markdown(st.session_state.explanation)

# ---------------------------------------------------------------- worklist

with worklist_tab:
    st.write("Run eligibility for every patient and rank them by denial risk.")
    if st.button("Check all patients"):
        rows = []
        for p in patients:
            r = service.check_eligibility(p.patient_id, dos, save=record)
            a = r["denial_analysis"]
            first = a["actions"][0] if a["actions"] else {"owner": "", "action": ""}
            rows.append({
                "Patient": f"{p.patient_id} · {p.first_name} {p.last_name}",
                "Payer": r["coverage_on_file"]["payer_name"],
                "Status": status_label(r["status"]),
                "Risk": RISK.get(a["denial_risk"]),
                "Category": a["category"] or "—",
                "Next action": f"{first['owner']}: {first['action']}",
                "_order": RISK_ORDER.get(a["denial_risk"], 9),
            })
        st.session_state.worklist = sorted(rows, key=lambda r: r["_order"])
    if st.session_state.get("worklist"):
        st.dataframe(
            pd.DataFrame(st.session_state.worklist).drop(columns="_order"),
            hide_index=True, use_container_width=True,
        )

# ---------------------------------------------------------------- analytics

with analytics_tab:
    backends = ["sqlite"] + (["databricks"] if settings.databricks_enabled else [])
    backend = st.radio(
        "Backend", backends, horizontal=True,
        format_func={"sqlite": "Local SQLite", "databricks": "Databricks (Delta)"}.get,
    )
    if st.button("Run Bronze → Silver → Gold pipeline"):
        with st.spinner(f"Running pipeline on {backend}…"):
            try:
                summary = medallion.run(backend)
                st.success(
                    f"Bronze +{summary['bronze_rows_added']} rows, Silver +{summary['silver_rows_added']} rows, "
                    f"Gold rebuilt: {', '.join(summary['gold_tables'])}"
                )
            except Exception as exc:
                st.error(f"Pipeline failed: {exc}")

    try:
        with st.spinner("Loading Gold tables…"):
            gold = medallion.read_gold(backend)
    except Exception as exc:
        gold = None
        st.error(f"Could not read Gold tables: {exc}")

    if gold is not None and not gold["gold_payer_performance"]:
        st.info("No Gold data yet. Run some eligibility checks, then run the pipeline.")
    elif gold is not None:
        perf = pd.DataFrame(gold["gold_payer_performance"])
        for col in perf.columns.drop("payer_id"):
            perf[col] = pd.to_numeric(perf[col])

        total = int(perf["total_checks"].sum())
        failed = int(perf["rejected"].sum() + perf["ineligible"].sum())
        m1, m2, m3 = st.columns(3)
        m1.metric("Eligibility checks", total)
        m2.metric("Rejected or ineligible", failed)
        m3.metric("Failure rate", f"{100 * failed / total:.1f}%" if total else "—")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Checks by status, per payer**")
            status_cols = {
                "eligible": "ELIGIBLE", "eligible_with_issues": "ELIGIBLE_WITH_ISSUES",
                "ineligible": "INELIGIBLE", "rejected": "REJECTED", "payer_unavailable": "PAYER_UNAVAILABLE",
            }
            by_status = perf.set_index("payer_id")[list(status_cols)].rename(
                columns={c: status_label(s) for c, s in status_cols.items()}
            )
            st.bar_chart(
                by_status, horizontal=True, stack=True,
                color=[STATUS[s][2] for s in status_cols.values()],
                x_label="Checks", y_label="Payer",
            )
        with c2:
            st.markdown("**Failure rate by payer (%)**")
            st.bar_chart(
                perf.set_index("payer_id")[["failure_rate_pct"]], horizontal=True,
                color=SERIES_1, x_label="Rejected or ineligible (%)", y_label="Payer",
            )

        with st.expander("Gold tables", expanded=False):
            for name, rows in gold.items():
                st.markdown(f"`{name}`")
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

# ---------------------------------------------------------------- AI assistant

with ai_tab:
    st.write(
        "Claude answers using this project's MCP server: it starts `server.py` and calls its tools "
        "(patient lookup, 270/271, rules, denial analysis)."
    )
    question = st.text_area(
        "Question",
        value="Check John Smith's eligibility and tell me why he was rejected and what the RCM team should do.",
    )
    if not os.getenv("ANTHROPIC_API_KEY"):
        st.warning("Set ANTHROPIC_API_KEY in .env to use the AI assistant.")
    if st.button("Ask the agent", type="primary"):
        with st.spinner("Claude is working through the MCP tools…"):
            try:
                answer = asyncio.run(agent.ask(question))
                st.session_state.agent_answer = answer
            except Exception as exc:
                st.session_state.agent_answer = None
                st.error(f"Claude is unavailable: {anthropic_error(exc)}")
    answer = st.session_state.get("agent_answer")
    if answer:
        st.caption("MCP tools called: " + (" → ".join(answer.tool_calls) or "none"))
        st.markdown(answer.answer)
