"""Integration tests (Streamlit AppTest) for the load-by-ID outbreak-association guard.

A report id can numerically collide with a disaster id (the user typed 52586, the DRC disaster
id, and got an unrelated report whose *report* id is 52586). These tests assert the app warns and
does NOT silently proceed when a report is not linked to the active outbreak.
"""
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import src.ingestion.live_sources as ls
import src.live.extract_report as er
import src.live.validate_extraction as ve
import src.live.candidate_store as cs
from src.live.candidate_store import write_candidates, promote_candidates, candidate_id

DISABLED = ls.LiveSourceResult([], "disabled", "live sources disabled", 1.0)
ACTIVE_ID = 52586
OTHER_ID = 99999


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

    def test_offscope_load_anyway_disables_promote_button(self):
        # extraction yields one otherwise-valid candidate, but from an off-scope report ->
        # the promote button must render DISABLED with the switch-outbreak message.
        body = "Dili reported 50 confirmed cases and 5 deaths."
        ext = er._ExtractionPayload(records=[er._ExtractedRow(
            date="2026-07-15", health_zone="Dili", confirmed_cases=50, deaths=5, snippet=body)])
        val = ve._ValidationPayload(verdicts=[ve._Verdict(index=0, verdict="PASS")])
        stack = [
            patch.object(ls, "fetch_recent_drc_ebola_reports", return_value=DISABLED),
            patch.object(ls, "fetch_report_meta", return_value=_meta([])),
            patch.object(ls, "fetch_report_body", return_value=body),
            patch.object(er, "_call_extraction_llm", return_value=ext),
            patch.object(ve, "_call_validation_llm", return_value=val),
            patch.object(cs, "write_candidates", return_value=1),   # don't touch the real store
        ]
        for p in stack:
            p.start()
        try:
            at = AppTest.from_file("app_streamlit.py", default_timeout=60).run()
            at.text_input(key="by_id_input").set_value("52586")
            at = _click(at, "Load candidates")
            at = _click(at, "Load anyway")
            promote_btns = [b for b in at.button if b.label.startswith("✅ Promote")]
            self.assertTrue(promote_btns, "promote button should render for an approvable candidate")
            self.assertTrue(all(b.disabled for b in promote_btns), "promote button must be disabled")
            errs = " ".join(e.value for e in at.error)
            self.assertIn("switch the active outbreak", errs)
            self.assertEqual(at.exception, [])
        finally:
            _stop(stack)


class TestOffScopePromotionRefused(unittest.TestCase):
    """Architectural backstop: promote_candidates refuses a record whose disaster_id is not the
    active outbreak, even on a direct call — safety from architecture, not from a UI disclaimer."""

    def _rec(self, disaster_id):
        return {"disaster_id": disaster_id, "date": "2026-07-12", "province": "Dili",
                "health_zone": "Dili", "suspected_cases": None, "confirmed_cases": 50, "deaths": 5,
                "source_url": "http://x", "report_date": "2026-07-14",
                "snippet": "Dili (50 cases, 5 deaths)"}

    def test_offscope_record_is_refused_history_untouched(self):
        tmp = tempfile.mkdtemp()
        cand, hist = os.path.join(tmp, "c.csv"), os.path.join(tmp, "h.csv")
        try:
            rec = self._rec(OTHER_ID)                       # belongs to a different outbreak
            write_candidates([rec], cand)
            result = promote_candidates([candidate_id(rec)], cand, hist, active_disaster_id=ACTIVE_ID)
            self.assertEqual(result.added_to_history, 0)
            self.assertEqual(result.promoted, [])
            self.assertEqual(len(result.rejected), 1)
            self.assertIn("not associated with the active outbreak", result.rejected[0]["reason"])
            self.assertFalse(os.path.exists(hist))          # nothing written
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_inscope_record_promotes(self):
        tmp = tempfile.mkdtemp()
        cand, hist = os.path.join(tmp, "c.csv"), os.path.join(tmp, "h.csv")
        try:
            rec = self._rec(ACTIVE_ID)                       # belongs to the active outbreak
            write_candidates([rec], cand)
            result = promote_candidates([candidate_id(rec)], cand, hist, active_disaster_id=ACTIVE_ID)
            self.assertEqual(result.added_to_history, 1)
            self.assertEqual(len(result.promoted), 1)
            self.assertEqual(result.rejected, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
