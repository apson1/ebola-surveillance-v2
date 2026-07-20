"""Integration tests (Streamlit AppTest) for the load-by-ID outbreak-association guard.

A report id can numerically collide with a disaster id (the user typed 52586, the DRC disaster
id, and got an unrelated report whose *report* id is 52586). These tests assert the app warns and
does NOT silently proceed when a report is not linked to the active outbreak.
"""
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import src.ingestion.live_sources as ls
import src.live.extract_report as er

DISABLED = ls.LiveSourceResult([], "disabled", "live sources disabled", 1.0)


def _meta(disaster_ids):
    return {"id": "52586", "title": "CWS: aid for East Timor/Indonesia", "source": "CWS",
            "date": "2000-01-01", "url": "https://reliefweb.int/report/52586",
            "disaster_ids": disaster_ids, "disaster_names": []}


def _labels(at):
    return [b.label for b in at.button]


def _click(at, label):
    for b in at.button:
        if b.label == label:
            return b.click().run()
    raise AssertionError(f"button {label!r} not found in {_labels(at)}")


def _run(disaster_ids):
    """Load report 52586 by ID with the given linked disaster ids; return the AppTest after the
    'Load candidates' click."""
    stack = [
        patch.object(ls, "fetch_recent_drc_ebola_reports", return_value=DISABLED),
        patch.object(ls, "fetch_report_meta", return_value=_meta(disaster_ids)),
        patch.object(ls, "fetch_report_body", return_value="No per-zone figures here."),
        patch.object(er, "_call_extraction_llm", return_value=er._ExtractionPayload(records=[])),
    ]
    for p in stack:
        p.start()
    try:
        at = AppTest.from_file("app_streamlit.py", default_timeout=60).run()
        at.text_input(key="by_id_input").set_value("52586")
        at = _click(at, "Load candidates")
        return at, stack
    finally:
        pass  # patches stopped by caller after assertions


def _stop(stack):
    for p in stack:
        p.stop()


class TestLoadByIdGuard(unittest.TestCase):
    def test_offscope_report_warns_and_does_not_proceed(self):
        at, stack = _run(disaster_ids=[])          # unlinked report (the East Timor doc)
        try:
            warns = " ".join(w.value for w in at.warning)
            self.assertIn("not associated with the active outbreak", warns)
            self.assertIn("52586", warns)
            self.assertIn("Load anyway", _labels(at))
            self.assertIn("Clear", _labels(at))
            # crucially, extraction has NOT started
            self.assertIsNone(at.session_state["live_selected_id"])
            self.assertEqual(at.exception, [])
        finally:
            _stop(stack)

    def test_load_anyway_proceeds_and_shows_reminder(self):
        at, stack = _run(disaster_ids=[])
        try:
            at = _click(at, "Load anyway")
            self.assertEqual(at.session_state["live_selected_id"], "52586")
            self.assertEqual(at.session_state["live_offscope_loaded"], "52586")
            # the candidate section carries a persistent out-of-scope reminder
            warns = " ".join(w.value for w in at.warning)
            self.assertIn("not associated with the active outbreak", warns)
            self.assertEqual(at.exception, [])
        finally:
            _stop(stack)

    def test_inscope_report_proceeds_directly(self):
        at, stack = _run(disaster_ids=[52586])     # linked to the active DRC outbreak
        try:
            self.assertEqual(at.session_state["live_selected_id"], "52586")
            self.assertIsNone(at.session_state["live_pending_offscope"])
            warns = " ".join(w.value for w in at.warning)
            self.assertNotIn("not associated with the active outbreak", warns)
            self.assertEqual(at.exception, [])
        finally:
            _stop(stack)


if __name__ == "__main__":
    unittest.main()
