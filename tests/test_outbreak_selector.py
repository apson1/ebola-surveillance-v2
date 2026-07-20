"""Phase B3 tests: the active-outbreak selector + session plumbing (Streamlit AppTest).

The ReliefWeb fetches are mocked so the tests are hermetic and do not hit the network.
"""
import contextlib
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import src.ingestion.live_sources as ls
from src.outbreaks import OutbreakProfile, REGISTRY

ENV_ID = 52586
UGA_ID = 90000
UGA = OutbreakProfile(disaster_id=UGA_ID, display_name="Uganda SVD 2026",
                      disease="Sudan virus disease (Ebola)", country_name="Uganda",
                      denied_zone_aliases=["uganda", "kampala"])
TWO = {ENV_ID: REGISTRY[ENV_ID], UGA_ID: UGA}
DISABLED = ls.LiveSourceResult([], "disabled", "live sources disabled", 1.0)


@contextlib.contextmanager
def _no_network(*extra):
    """Patch out ReliefWeb + body fetches so the app runs offline."""
    patches = [
        patch.object(ls, "fetch_recent_drc_ebola_reports", return_value=DISABLED),
        patch.object(ls, "fetch_report_body", return_value=""),
        *extra,
    ]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


def _all_md(at):
    return " ".join(m.value for m in at.markdown)


class TestOutbreakSelector(unittest.TestCase):
    def test_default_active_is_env_and_selector_shows_name(self):
        with _no_network():
            at = AppTest.from_file("app_streamlit.py", default_timeout=60).run()
        self.assertEqual(at.exception, [])
        self.assertEqual(at.session_state["active_disaster_id"], ENV_ID)
        self.assertTrue([s for s in at.selectbox if "Active outbreak" in s.label])

    def test_disaster_id_lives_in_details_not_the_header_line(self):
        with _no_network():
            at = AppTest.from_file("app_streamlit.py", default_timeout=60).run()
        md = _all_md(at)
        self.assertIn("ReliefWeb disaster_id", md)          # expander body
        self.assertIn(str(ENV_ID), md)
        self.assertNotIn("· disaster_id", md)               # old always-visible combined pill is gone

    def test_invalid_session_id_falls_back_to_env(self):
        with _no_network():
            at = AppTest.from_file("app_streamlit.py", default_timeout=60)
            at.session_state["active_disaster_id"] = 99999   # not in the registry
            at.run()
        self.assertEqual(at.exception, [])
        self.assertEqual(at.session_state["active_disaster_id"], ENV_ID)

    def test_switch_updates_session_and_resets_live_flow(self):
        with _no_network(patch("src.outbreaks.REGISTRY", TWO)):
            at = AppTest.from_file("app_streamlit.py", default_timeout=60).run()
            self.assertEqual(at.session_state["active_disaster_id"], ENV_ID)
            at.session_state["live_selected_id"] = "4221419"   # in-progress flow for outbreak A
            at.run()
            sel = next(s for s in at.selectbox if "Active outbreak" in s.label)
            sel.set_value(UGA_ID).run()
            self.assertEqual(at.session_state["active_disaster_id"], UGA_ID)
            self.assertIsNone(at.session_state["live_selected_id"])   # reset-on-switch cleared it
            self.assertEqual(at.exception, [])


if __name__ == "__main__":
    unittest.main()
