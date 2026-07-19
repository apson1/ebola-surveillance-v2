"""Read-only analytics over the durable history store (Phase A).

Pure functions that power the UI's History tab (per-zone trend series and the
since-the-previous-report diff). No Streamlit here, so everything is unit-testable, and
nothing under src/signal, src/alert, src/guardrails, or evals is touched.
"""
