import asyncio
import re
import time

import streamlit as st

from src.orchestrator import run_scan_async
from src.alert.alert_agent import ESCALATION_LINE
from src.ingestion.live_sources import fetch_recent_drc_ebola_reports, fetch_report_body
from src.live.extract_report import extract_report
from src.live.validate_extraction import validate_extraction
from src.live.candidate_store import (
    write_candidates, promote_candidates, reset_candidates, candidate_id,
)
from src.live.review import build_review
from src.live.live_scan import run_scan_on_new_data

# Page Configuration
st.set_page_config(
    page_title="Ebola Outbreak Surveillance",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Session state
_DEFAULTS = {
    # scenario runner (unchanged behavior)
    "scan_result": None, "selected_key": "multi_signal", "simulated_block": False,
    # live scan flow
    "live_selected_id": None, "live_source_url": None, "live_report_title": None,
    "live_report_date": None, "live_extraction": None, "live_validation": None,
    "live_promotion": None, "live_promoted_records": None, "live_scan": None,
}
for _k, _v in _DEFAULTS.items():
    st.session_state.setdefault(_k, _v)

# Inject Unified Zinc Style CSS
st.markdown("""
<style>
header[data-testid="stHeader"], footer { display: none !important; }
html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"], .main, .block-container, section[data-testid="stMain"] {
    font-family: 'DM Sans', -apple-system, sans-serif !important;
}
.block-container { padding: 2rem 2.5rem 3rem !important; max-width: 1200px !important; }
div[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid #e4e4e7 !important; border-radius: 8px !important;
    padding: 1.25rem !important; background-color: transparent !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.01) !important;
}
.escalation-box {
    background-color: rgba(239, 68, 68, 0.08); border-left: 5px solid #dc2626;
    padding: 1.25rem; border-radius: 8px; margin-top: 1.5rem; font-size: 0.9rem; line-height: 1.5;
}
.alert-headline {
    font-size: 1.45rem; font-weight: 700; color: #dc2626; margin-bottom: 1.25rem;
    border-bottom: 2px solid #e4e4e7; padding-bottom: 0.5rem;
}
/* Live-scan candidate visual language: amber = unconfirmed candidate, green = promoted, red = rejected/alert */
.badge { display:inline-block; padding:2px 9px; border-radius:6px; font-size:0.66rem;
         font-weight:800; letter-spacing:0.04em; color:#fff; }
.badge-candidate { background:#d97706; }
.badge-promoted  { background:#16a34a; }
.badge-rejected  { background:#dc2626; }
.cand-num  { font-size:1.35rem; font-weight:800; line-height:1.25; }
.cand-amber { border-left:5px solid #d97706; padding-left:0.9rem; }
.cand-red   { border-left:5px solid #dc2626; padding-left:0.9rem; }
.cand-green { border-left:5px solid #16a34a; padding-left:0.9rem; }
.muted { color:#71717a; font-size:0.85rem; }
@media (prefers-color-scheme: dark) {
    div[data-testid="stVerticalBlockBorderWrapper"] { border-color: #1e1e24 !important; }
    .alert-headline { border-bottom-color: #1e1e24; }
}
</style>
""", unsafe_allow_html=True)


# ---- shared helpers ----------------------------------------------------------------------

def make_urls_clickable(line: str) -> str:
    return re.sub(r'(https?://[^\s,)]+)', r'[\1](\1)', line)


def _ago(ts: float) -> str:
    secs = max(0, int(time.time() - (ts or 0)))
    if secs < 60:
        return f"{secs} second{'s' if secs != 1 else ''} ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    hrs = mins // 60
    return f"{hrs} hour{'s' if hrs != 1 else ''} ago"


def render_alert_brief(result: dict) -> None:
    """Render a scan result ({alert, flags, guardrail}). Shared by both tabs."""
    alert_text = result.get("alert", "")
    flags = result.get("flags", [])
    guardrail = result.get("guardrail", {})
    blocked = guardrail.get("blocked", False)
    violations = guardrail.get("violations", [])

    st.subheader("📋 Output Alert Brief")
    if blocked:
        st.markdown("<div class='alert-headline'>⚠️ Alert Brief Withheld by Guardrails</div>", unsafe_allow_html=True)
        st.error(
            "Automated check failed: the drafted prose brief was withheld to prevent unverified data "
            "from leaving the system. Inspect the underlying signals and source links below."
        )
        if violations:
            with st.expander("Show Guardrail Violation Details", expanded=True):
                for v in violations:
                    st.markdown(f"- 🚫 **{v['type'].replace('_', ' ').upper()}**: `{v['detail']}` did not trace to a source record.")
        st.markdown("#### Sourced Signals & Citations")
        for f in flags:
            src = f.get("source_url") or f.get("source_url_prior") or "No source URL"
            st.markdown(f"- **[{f.get('detector')}]** {f.get('health_zone','?')} ({f.get('province','?')}) — [{src}]({src})")
        st.markdown(f"<div class='escalation-box'><strong>ESCALATION DIRECTIVE:</strong><br>{ESCALATION_LINE}</div>", unsafe_allow_html=True)
    else:
        parts = alert_text.split("\n\n")
        headline = parts[0] if parts else "ALERT: Surveillance Signal Flagged"
        st.markdown(f"<div class='alert-headline'>{headline}</div>", unsafe_allow_html=True)
        with st.container(border=True):
            if len(parts) > 1:
                st.markdown("#### Active Signals")
                for line in parts[1].split("\n"):
                    if line.strip():
                        st.markdown(make_urls_clickable(line))
            if len(parts) > 2:
                st.markdown("---")
                st.markdown(f"**Confidence & Quality Note:**\n{parts[2]}")
        st.markdown(f"<div class='escalation-box'><strong>ESCALATION DIRECTIVE:</strong><br>{ESCALATION_LINE}</div>", unsafe_allow_html=True)

    st.markdown("---")
    with st.expander("Detected Signals (Raw JSON)", expanded=False):
        st.json(flags)


# ---- title + tabs ------------------------------------------------------------------------

st.title("🩺 Ebola Surveillance Signal Detector")
st.markdown(
    "This decision-support system ingests outbreak reports, detects new or accelerating clusters "
    "with rule-based Python agents, and ranks and formats them for coordinator review."
)

tab_live, tab_scenario = st.tabs(["📡 Live scan (ReliefWeb)", "🧪 Scenario runner"])


# ========================================================================================
# TAB 1 — Live scan (fetch -> extract -> review -> promote -> scan)
# ========================================================================================
with tab_live:
    def _select_report(report_id, source_url, title, report_date):
        """Route a chosen report into the shared extract -> validate -> review flow."""
        st.session_state.live_selected_id = report_id
        st.session_state.live_source_url = source_url
        st.session_state.live_report_title = title
        st.session_state.live_report_date = report_date
        for k in ("live_extraction", "live_validation", "live_promotion",
                  "live_promoted_records", "live_scan"):
            st.session_state[k] = None
        st.rerun()

    # --- Step 0: load a specific report by ID (for repeatable demos, e.g. WHO 4221419) ---
    id_col, btn_col = st.columns([3, 1])
    with id_col:
        by_id = st.text_input(
            "Load specific report by ID",
            placeholder="e.g. 4221419 (WHO Bundibugyo External Sit Rep 09)",
            help="Bypasses the recent list and extracts from this ReliefWeb report id directly.",
        )
    with btn_col:
        st.write("")  # align the button with the input box
        load_by_id = st.button("Load candidates", use_container_width=True)
    if load_by_id:
        rid = (by_id or "").strip()
        if not rid.isdigit():
            st.warning("Enter a numeric ReliefWeb report ID.")
        else:
            # Body is fetched directly; no metadata lookup. source_url is the stable node URL.
            _select_report(rid, f"https://reliefweb.int/node/{rid}",
                           f"Report {rid} (loaded by ID)", "")

    st.markdown("---")

    # --- Step 1: recent reports, refresh, and fetched-ago indicator ---
    top = st.columns([1, 3])
    with top[0]:
        refresh_clicked = st.button("🔄 Refresh latest report", use_container_width=True)
    live = fetch_recent_drc_ebola_reports(limit=8, force=refresh_clicked)
    with top[1]:
        if live.fetched_at:
            st.caption(f"Report list fetched **{_ago(live.fetched_at)}** (15-minute cache).")

    if live.mode == "disabled":
        st.info(f"🔌 {live.note}")
    elif live.mode == "error":
        st.warning(f"⚠️ {live.note}")
    elif not live.reports:
        st.info("No recent DRC Ebola reports were returned.")
    else:
        if live.mode == "fallback":
            st.warning(f"⚠️ {live.note}")
        st.markdown("#### Pick a report to extract candidate records from")
        st.caption("The newest report is often a narrative sitrep without per-zone tables — pick one with case figures (e.g. a WHO external sitrep).")
        for r in live.reports:
            row = st.columns([6, 1])
            day = (r.get("date") or "?")[:10]
            row[0].markdown(f"[{r['title']}]({r['url']}) — **{r.get('source') or '?'}**, {day}")
            if row[1].button("Extract", key=f"pick_{r['id']}"):
                _select_report(r["id"], r["url"], r["title"], day)

    # --- Step 2: extract + validate the selected report (cached in session) ---
    sel_id = st.session_state.live_selected_id
    if sel_id:
        st.markdown("---")
        st.markdown(f"### Candidates from: *{st.session_state.live_report_title}*")

        if st.session_state.live_extraction is None:
            with st.spinner("Fetching body → extracting → validating (independent two-model check)…"):
                body = fetch_report_body(sel_id)
                if not body:
                    st.error("Could not fetch this report's body (appname unset or unavailable). Try another report.")
                else:
                    extraction = extract_report(body, st.session_state.live_source_url,
                                                st.session_state.live_report_date)
                    validation = validate_extraction(extraction.records, body) if extraction.records else None
                    st.session_state.live_extraction = extraction
                    st.session_state.live_validation = validation
                    if validation and validation.validated:
                        write_candidates(validation.validated)  # stage as pending

        extraction = st.session_state.live_extraction
        validation = st.session_state.live_validation

        if extraction is not None and not extraction.ok:
            # The extraction SERVICE failed (rate limit / network / parse) — this is NOT the
            # same as the report having no per-zone data. Say so, and offer a retry.
            err = (extraction.error or "").lower()
            if any(t in err for t in ("429", "resource_exhausted", "quota", "rate limit", "rate-limit")):
                st.warning(
                    "⏳ **Extraction is rate-limited right now** (Gemini API quota). No records "
                    "were read from this report — this is a temporary limit, **not** a sign the "
                    "report lacks per-zone data. Wait a moment, then click **Load candidates** / "
                    "**Extract** again to retry."
                )
            else:
                st.error(
                    "⚠️ **The extraction service is unavailable right now**, so no records could "
                    "be read from this report. This is **not** the same as the report having no "
                    "data. Click **Load candidates** / **Extract** again to retry."
                )
            with st.expander("Error details"):
                st.code(extraction.error or "(no detail)")

        elif extraction is not None:
            model = build_review(extraction, validation)

            # Failure states
            if model.is_empty:
                st.info("No per-zone case figures were found in this report. "
                        "National totals and unattributed numbers are intentionally excluded.")
            elif model.approvable_count == 0:
                st.warning("Every extracted record was rejected — nothing to promote. See the reasons below.")

            approved_ids = []

            # --- Step 3: approvable candidate cards (amber, unconfirmed) ---
            if model.approvable:
                st.markdown("#### 🟠 Candidate records — NOT in history yet (require your approval)")
                for card in model.approvable:
                    r = card.record
                    susp = f", suspected {r.get('suspected_cases')}" if r.get("suspected_cases") is not None else ""
                    with st.container(border=True):
                        left, right = st.columns([6, 1])
                        with left:
                            st.markdown(
                                f"<div class='cand-amber'>"
                                f"<div class='cand-num'>{r.get('health_zone','?')} — "
                                f"confirmed {r.get('confirmed_cases')}, deaths {r.get('deaths')}{susp}</div>"
                                f"<div class='muted' style='margin:0.15rem 0 0.55rem;'>{r.get('province','')} · as-of {r.get('date','')}</div>"
                                f"<div style='font-size:0.9rem;'>“{r.get('snippet','')}”</div></div>",
                                unsafe_allow_html=True,
                            )
                        with right:
                            st.markdown("<span class='badge badge-candidate'>CANDIDATE</span>", unsafe_allow_html=True)
                            if st.checkbox("Approve", key=f"approve_{card.candidate_id}"):
                                approved_ids.append(card.candidate_id)

            # --- Rejected candidates (read-only, reasons visible) ---
            if model.rejected:
                st.markdown("#### 🚫 Rejected records — not approvable")
                for card in model.rejected:
                    r = card.record
                    with st.container(border=True):
                        st.markdown(
                            f"<div class='cand-red'>"
                            f"<div class='cand-num' style='color:#71717a;'>{r.get('health_zone') or '(no zone)'} — "
                            f"confirmed {r.get('confirmed_cases')}, deaths {r.get('deaths')}</div>"
                            f"<div class='muted'>“{r.get('snippet','')}”</div>"
                            f"<div style='margin-top:0.45rem;'><span class='badge badge-rejected'>REJECTED</span> "
                            f"<span style='color:#dc2626;font-size:0.85rem;'>{card.reason}</span></div></div>",
                            unsafe_allow_html=True,
                        )

            # --- Step 4: gate 1 — promote (irreversible) ---
            if model.approvable:
                st.markdown("---")
                promote_disabled = len(approved_ids) == 0
                if st.button(f"✅ Promote {len(approved_ids)} approved record(s) to history",
                             type="primary", disabled=promote_disabled):
                    pr = promote_candidates(approved_ids)
                    approved_records = [c.record for c in model.approvable if c.candidate_id in approved_ids]
                    promoted_set = set(pr.promoted)
                    st.session_state.live_promotion = pr
                    st.session_state.live_promoted_records = [
                        rec for rec in approved_records if candidate_id(rec) in promoted_set
                    ]
                    st.session_state.live_scan = None
                    st.rerun()
                if promote_disabled:
                    st.caption("Select at least one candidate above to promote.")

            # --- Promotion summary (green) ---
            pr = st.session_state.live_promotion
            if pr is not None:
                st.success(f"🟢 Promoted **{len(pr.promoted)}** record(s) into history "
                           f"({pr.added_to_history} new row(s) written).")
                for rec in (st.session_state.live_promoted_records or []):
                    st.markdown(
                        f"<div class='cand-green'><span class='badge badge-promoted'>PROMOTED</span> "
                        f"<b>{rec.get('health_zone')}</b> — confirmed {rec.get('confirmed_cases')}, "
                        f"deaths {rec.get('deaths')}</div>",
                        unsafe_allow_html=True,
                    )
                if pr.rejected:
                    st.warning("Some approved records could not enter history:")
                    for rej in pr.rejected:
                        st.markdown(f"- `{rej['candidate_id']}` — {rej['reason']}")

                # --- Step 5: gate 2 — run scan on new data (option B) ---
                st.markdown("---")
                if st.button("🔍 Run scan on new data", type="primary"):
                    with st.spinner("Scanning this report against prior history…"):
                        try:
                            scan = asyncio.run(run_scan_on_new_data(
                                st.session_state.live_promoted_records or [],
                                st.session_state.live_source_url,
                            ))
                            st.session_state.live_scan = scan
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Scan failed: {e}")
                        st.rerun()
                st.caption("Compares this report's zones and numbers against the state of history before "
                           "promotion, so newly emerging and accelerating clusters are visible.")

                if st.session_state.live_scan is not None:
                    st.markdown("---")
                    render_alert_brief(st.session_state.live_scan)

    # --- Candidate store controls (reset behind a two-step confirm; refresh is above) ---
    st.markdown("---")
    with st.expander("⚙️ Candidate store controls"):
        st.caption("The candidate store (`data/candidate_history.csv`) stages validated records. "
                   "Reset clears it and never touches `data/history.csv`.")
        confirm_reset = st.checkbox("Confirm: clear all staged candidates")
        if st.button("🗑️ Reset candidate store", disabled=not confirm_reset):
            reset_candidates()
            for k in ("live_promotion", "live_promoted_records", "live_scan"):
                st.session_state[k] = None
            st.success("Candidate store cleared. `history.csv` untouched.")


# ========================================================================================
# TAB 2 — Scenario runner (unchanged behavior)
# ========================================================================================
with tab_scenario:
    SCENARIOS = {
        "multi_signal": "data/incoming/incoming_multi_signal.json",
        "new_zone": "data/incoming/incoming_new_zone.json",
        "spike": "data/incoming/incoming_spike.json",
        "data_gap": "data/incoming/incoming_data_gap.json",
        "cfr_shift": "data/incoming/incoming_cfr_shift.json",
    }

    st.markdown("### ⚙️ Scan Configuration")
    col1, col2 = st.columns([2, 1])
    with col1:
        scenario_keys = list(SCENARIOS.keys())
        selected_key = st.selectbox(
            "Select Scenario Report", options=scenario_keys,
            index=scenario_keys.index("multi_signal"),
            format_func=lambda x: x.replace("_", " ").title(),
        )
        selected_path = SCENARIOS[selected_key]
        st.caption(f"Target file: `{selected_path}`")
    with col2:
        simulate_block = st.checkbox(
            "Simulate Guardrail Violation", value=False,
            help="Injects an unsourced number into the real alert and runs the actual guardrail, which detects it and withholds the brief.",
        )

    if st.button("Run Scan Pipeline", type="primary", use_container_width=True):
        with st.spinner("Executing pipeline (Ingestion → Signal Detection → LLM Ranking → Guardrails)…"):
            try:
                result = asyncio.run(run_scan_async(selected_path))
                if simulate_block:
                    from src.guardrails.guardrails import enforce_guardrails
                    tampered_alert = result["alert"] + "\n\nUnverified estimate: 99999 additional suspected cases."
                    guard = enforce_guardrails(tampered_alert, result["flags"])
                    result = result.copy()
                    result["alert"] = guard.alert
                    result["guardrail"] = {"passed": guard.passed, "blocked": guard.blocked,
                                           "violations": guard.violations}
                st.session_state.scan_result = result
                st.session_state.selected_key = selected_key
                st.session_state.simulated_block = simulate_block
                st.success("Surveillance scan completed.")
            except Exception as e:  # noqa: BLE001
                st.error(f"Error running pipeline scan: {e}")

    if (st.session_state.scan_result is not None
            and st.session_state.selected_key == selected_key
            and st.session_state.simulated_block == simulate_block):
        st.markdown("---")
        render_alert_brief(st.session_state.scan_result)
