import streamlit as st
import asyncio
import re
import json
from src.orchestrator import run_scan_async
from src.alert.alert_agent import ESCALATION_LINE
from src.ingestion.live_sources import fetch_recent_drc_ebola_reports

# Page Configuration
st.set_page_config(
    page_title="Ebola Outbreak Surveillance",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Initialize Session State
if "scan_result" not in st.session_state:
    st.session_state.scan_result = None
if "selected_key" not in st.session_state:
    st.session_state.selected_key = "multi_signal"
if "simulated_block" not in st.session_state:
    st.session_state.simulated_block = False

# Inject Unified Zinc Style CSS
st.markdown("""
<style>
/* Hide Streamlit chrome */
header[data-testid="stHeader"], footer {
    display: none !important;
}

/* Global Font styling */
html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"], .main, .block-container, section[data-testid="stMain"] {
    font-family: 'DM Sans', -apple-system, sans-serif !important;
}

.block-container {
    padding: 2rem 2.5rem 3rem !important;
    max-width: 1200px !important;
}

/* Custom styling for Streamlit container with border */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border: 1px solid #e4e4e7 !important;
    border-radius: 8px !important;
    padding: 1.5rem !important;
    background-color: transparent !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.01) !important;
}

/* Custom styled Escalation Callout Box */
.escalation-box {
    background-color: rgba(239, 68, 68, 0.08);
    border-left: 5px solid #dc2626;
    padding: 1.25rem;
    border-radius: 8px;
    margin-top: 1.5rem;
    font-size: 0.9rem;
    line-height: 1.5;
}

/* Style for alert headline */
.alert-headline {
    font-size: 1.45rem;
    font-weight: 700;
    color: #dc2626;
    margin-bottom: 1.25rem;
    border-bottom: 2px solid #e4e4e7;
    padding-bottom: 0.5rem;
}

/* Theme variations for borders and lines */
@media (prefers-color-scheme: dark) {
    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #1e1e24 !important;
    }
    .alert-headline {
        border-bottom-color: #1e1e24;
    }
}
</style>
""", unsafe_allow_html=True)

# Helper to make URLs clickable in markdown
def make_urls_clickable(line: str) -> str:
    # Converts https://url to [https://url](https://url)
    return re.sub(r'(https?://[^\s,)]+)', r'[\1](\1)', line)

# Title & Description
st.title("🩺 Ebola Surveillance Signal Detector")
st.markdown(
    "This decision-support system ingests outbreak data reports, detects new or accelerating clusters "
    "using rule-based Python agents, and ranks and formats them for coordinator review."
)

# --- Live official reports (ReliefWeb) --------------------------------------------------
# Metadata only (Phase 1). Sits ABOVE the scenario runner and does not alter it. The fetch
# is cached, so Streamlit reruns do not hammer the network.
st.markdown("### 📡 Live official reports (ReliefWeb)")
with st.container(border=True):
    live = fetch_recent_drc_ebola_reports(limit=8)
    if live.mode == "disabled":
        st.info(f"🔌 {live.note}")
    elif live.mode == "error":
        st.warning(f"⚠️ {live.note}")
    else:
        if live.mode == "fallback":
            # Make drift off the pinned outbreak clearly visible.
            st.warning(f"⚠️ {live.note}")
        else:
            st.caption(live.note)
        if live.reports:
            for r in live.reports:
                day = (r.get("date") or "?")[:10]
                st.markdown(f"- [{r['title']}]({r['url']}) — **{r.get('source') or '?'}**, {day}")
        else:
            st.caption("No matching reports were returned.")

SCENARIOS = {
    "multi_signal": "data/incoming/incoming_multi_signal.json",
    "new_zone": "data/incoming/incoming_new_zone.json",
    "spike": "data/incoming/incoming_spike.json",
    "data_gap": "data/incoming/incoming_data_gap.json",
    "cfr_shift": "data/incoming/incoming_cfr_shift.json",
}

# Configuration inputs panel
st.markdown("### ⚙️ Scan Configuration")
col1, col2 = st.columns([2, 1])

with col1:
    scenario_keys = list(SCENARIOS.keys())
    selected_key = st.selectbox(
        "Select Scenario Report",
        options=scenario_keys,
        index=scenario_keys.index("multi_signal"),
        format_func=lambda x: x.replace("_", " ").title()
    )
    selected_path = SCENARIOS[selected_key]
    st.caption(f"Target file: `{selected_path}`")

with col2:
    simulate_block = st.checkbox(
        "Simulate Guardrail Violation",
        value=False,
        help="Injects an unsourced number into the real alert and runs the actual guardrail, which detects it and withholds the brief."
    )

# Run Scan Trigger
if st.button("Run Scan Pipeline", type="primary", use_container_width=True):
    with st.spinner("Executing pipeline (Ingestion -> Signal Detection -> LLM Ranking -> Guardrails)..."):
        try:
            # Perform the async scan call
            result = asyncio.run(run_scan_async(selected_path))
            
            # Simulate a violation by routing a tampered alert through the REAL guardrail:
            # inject an unsourced number into the genuine alert and let enforce_guardrails
            # actually detect it and withhold the brief. No mocked state is constructed here.
            if simulate_block:
                from src.guardrails.guardrails import enforce_guardrails
                tampered_alert = (
                    result["alert"]
                    + "\n\nUnverified estimate: 99999 additional suspected cases."
                )
                guard = enforce_guardrails(tampered_alert, result["flags"])
                result = result.copy()
                result["alert"] = guard.alert
                result["guardrail"] = {
                    "passed": guard.passed,
                    "blocked": guard.blocked,
                    "violations": guard.violations,
                }
            
            st.session_state.scan_result = result
            st.session_state.selected_key = selected_key
            st.session_state.simulated_block = simulate_block
            st.success("Surveillance scan completed.")
        except Exception as e:
            st.error(f"Error running pipeline scan: {e}")

# Render results
if st.session_state.scan_result is not None:
    # Verify that the rendered results match the currently selected scenario and simulation option
    if (st.session_state.selected_key == selected_key and 
        st.session_state.simulated_block == simulate_block):
        
        result = st.session_state.scan_result
        alert_text = result.get("alert", "")
        flags = result.get("flags", [])
        guardrail = result.get("guardrail", {})
        blocked = guardrail.get("blocked", False)
        violations = guardrail.get("violations", [])

        st.markdown("---")
        st.subheader("📋 Output Alert Brief")

        if blocked:
            # Withheld visual layout
            st.markdown("<div class='alert-headline'>⚠️ Alert Brief Withheld by Guardrails</div>", unsafe_allow_html=True)
            
            st.error(
                "Automated check failed: the drafted prose alert brief has been withheld to prevent unverified data from leaving the system. "
                "You must manually inspect the underlying signals and source links below."
            )
            
            # Violations expander
            if violations:
                with st.expander("Show Guardrail Violations Details", expanded=True):
                    for v in violations:
                        st.markdown(f"- 🚫 **{v['type'].replace('_', ' ').upper()}**: Cited value `{v['detail']}` did not match any source records.")

            # Sourced Signals list
            st.markdown("#### Sourced Signals & Citations")
            for f in flags:
                detector = f.get("detector")
                zone = f.get("health_zone", "Unknown Zone")
                province = f.get("province", "Unknown Province")
                src = f.get("source_url") or f.get("source_url_prior") or "No source URL"
                st.markdown(f"- **[{detector}]** {zone} ({province}) — Source citation: [{src}]({src})")

            # Escalation Line
            st.markdown(f"""
            <div class="escalation-box">
                <strong>ESCALATION DIRECTIVE:</strong><br>
                {ESCALATION_LINE}
            </div>
            """, unsafe_allow_html=True)

        else:
            # Successful brief visual layout
            parts = alert_text.split("\n\n")
            headline = parts[0] if len(parts) > 0 else "ALERT: Surveillance Signal Flagged"
            
            # Clean headline rendering
            st.markdown(f"<div class='alert-headline'>{headline}</div>", unsafe_allow_html=True)

            # Active Signals container
            with st.container(border=True):
                # Signals section
                if len(parts) > 1:
                    st.markdown("#### Active Signals")
                    for line in parts[1].split("\n"):
                        if line.strip():
                            st.markdown(make_urls_clickable(line))

                # Confidence note section
                if len(parts) > 2:
                    st.markdown("---")
                    st.markdown(f"**Confidence & Quality Note:**\n{parts[2]}")

            # Escalation Line
            st.markdown(f"""
            <div class="escalation-box">
                <strong>ESCALATION DIRECTIVE:</strong><br>
                {ESCALATION_LINE}
            </div>
            """, unsafe_allow_html=True)

        # Raw Signals expandable section
        st.markdown("---")
        with st.expander("Detected Signals (Raw JSON)", expanded=False):
            st.json(flags)
