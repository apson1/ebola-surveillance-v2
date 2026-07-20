"""Phase B1 tests: the disaster_id plumbing — migration/rollback scripts, the outbreak registry,
dedup-by-disaster, incoming disaster_id precedence, and per-outbreak view/scan scoping.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from scripts.migrate_add_disaster_id import migrate
from scripts.rollback_disaster_id import rollback, RollbackRefused
from src.outbreaks import active_outbreak, profile_for, REGISTRY
from src.contract import CONTRACT_COLUMNS, IDENTITY_COLUMNS
from src.memory.history_store import append_to_history
from src.ingestion.ingestion import load_incoming_report, get_prior_snapshot
from src.live.candidate_store import candidate_id
from src.live.live_scan import prior_excluding_source
from src.insights.history_views import (
    compute_history_diff, zone_trend_series, top_zones_by_recent_change, latest_reporting_round,
)

DID = 52586
OTHER = 99999
_PRE = ["date", "province", "health_zone", "suspected_cases",
        "confirmed_cases", "deaths", "source_url", "report_date"]


def _pre_row(zone="Bunia", confirmed=100):
    return {"date": "2026-06-10", "province": "Ituri", "health_zone": zone,
            "suspected_cases": 10, "confirmed_cases": confirmed, "deaths": 5,
            "source_url": "http://x", "report_date": "2026-06-13"}


class TestContract(unittest.TestCase):
    def test_disaster_id_is_first_and_in_identity(self):
        self.assertEqual(CONTRACT_COLUMNS[0], "disaster_id")
        self.assertEqual(len(CONTRACT_COLUMNS), 9)
        self.assertIn("disaster_id", IDENTITY_COLUMNS)


class TestRegistry(unittest.TestCase):
    def test_active_resolves_to_drc_by_default(self):
        prof = active_outbreak()
        self.assertEqual(prof.disaster_id, 52586)
        self.assertEqual(prof.display_name, "DRC Ebola 2026")
        # B2-reserved fields are populated on the profile, ready to consume later
        self.assertTrue(prof.denied_zone_aliases and prof.disease and prof.country_iso3)

    def test_unknown_id_returns_placeholder_not_crash(self):
        with patch("src.outbreaks.RELIEFWEB_DISASTER_ID", OTHER):
            prof = active_outbreak()
        self.assertEqual(prof.disaster_id, OTHER)
        self.assertNotIn(OTHER, REGISTRY)

    def test_profile_for_resolves_and_degrades(self):
        self.assertEqual(profile_for(52586).country_name, "the Democratic Republic of the Congo")
        ph = profile_for(OTHER)                                 # unconfigured id
        self.assertEqual(ph.denied_zone_aliases, [])            # empty deny-list, no crash
        self.assertTrue(ph.disease and ph.country_name)         # grammatical prompt fallbacks


class TestMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "history.csv")
        pd.DataFrame([_pre_row("Bunia"), _pre_row("Beni", 40)], columns=_PRE).to_csv(self.path, index=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_migrate_adds_column_first_with_backup_and_is_idempotent(self):
        msg = migrate(self.path)
        df = pd.read_csv(self.path)
        self.assertEqual(list(df.columns)[0], "disaster_id")
        self.assertTrue((df["disaster_id"] == DID).all())
        self.assertTrue(os.path.exists(self.path + ".bak"))
        self.assertIn("migrated", msg)
        # second run is a no-op
        self.assertIn("already migrated", migrate(self.path))
        self.assertEqual(len(pd.read_csv(self.path).columns), 9)

    def test_rollback_restores_and_refuses_on_foreign_id(self):
        migrate(self.path)                      # creates .bak (pre-migration 8-col)
        # a second outbreak's row lands in the file -> rollback must refuse (would lose it)
        df = pd.read_csv(self.path)
        df.loc[len(df)] = [OTHER, "2026-06-10", "Ituri", "Zulu", 1, 2, 1, "http://y", "2026-06-13"]
        df.to_csv(self.path, index=False)
        with self.assertRaises(RollbackRefused):
            rollback(self.path)
        # remove the foreign row -> rollback now restores the pre-migration 8-col file
        pd.read_csv(self.path).query("disaster_id == @DID").to_csv(self.path, index=False)
        rollback(self.path)
        self.assertNotIn("disaster_id", pd.read_csv(self.path).columns)


class TestDedupByDisaster(unittest.TestCase):
    def test_same_zone_different_disaster_both_kept(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "h.csv")
        try:
            base = {"date": "2026-06-10", "province": "Ituri", "health_zone": "Bunia",
                    "suspected_cases": 10, "confirmed_cases": 100, "deaths": 5,
                    "source_url": "http://x", "report_date": "2026-06-13"}
            append_to_history([{**base, "disaster_id": DID}], path)
            append_to_history([{**base, "disaster_id": OTHER}], path)
            df = pd.read_csv(path)
            self.assertEqual(len(df), 2)                                  # not deduped across outbreaks
            self.assertEqual(set(df["disaster_id"]), {DID, OTHER})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestIncomingPrecedence(unittest.TestCase):
    def _write(self, payload):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "incoming.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        return tmp, path

    def _rec(self, **over):
        r = {"date": "2026-06-20", "province": "Ituri", "health_zone": "Bunia",
             "suspected_cases": 10, "confirmed_cases": 50, "deaths": 5,
             "source_url": "http://x"}
        r.update(over)
        return r

    def test_file_level_then_active_then_per_record(self):
        # (a) no disaster_id anywhere -> active outbreak
        tmp, path = self._write({"report_date": "2026-06-21", "data": [self._rec()]})
        try:
            self.assertEqual(int(load_incoming_report(path).iloc[0]["disaster_id"]), DID)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        # (b) file-level disaster_id wins over active
        tmp, path = self._write({"report_date": "2026-06-21", "disaster_id": OTHER, "data": [self._rec()]})
        try:
            self.assertEqual(int(load_incoming_report(path).iloc[0]["disaster_id"]), OTHER)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        # (c) per-record disaster_id wins over file-level
        tmp, path = self._write({"report_date": "2026-06-21", "disaster_id": OTHER,
                                 "data": [self._rec(disaster_id=DID)]})
        try:
            self.assertEqual(int(load_incoming_report(path).iloc[0]["disaster_id"]), DID)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCandidateIdScoping(unittest.TestCase):
    def test_disaster_id_changes_candidate_id(self):
        base = {"date": "2026-06-10", "province": "Ituri", "health_zone": "Bunia",
                "source_url": "http://x"}
        self.assertNotEqual(candidate_id({**base, "disaster_id": DID}),
                            candidate_id({**base, "disaster_id": OTHER}))


class TestViewAndScanScoping(unittest.TestCase):
    def _two_outbreak_df(self):
        rows = [
            # outbreak DID: Bunia 100 -> 300 (a mover), and a solo new zone
            {"disaster_id": DID, "date": "2026-06-10", "province": "Ituri", "health_zone": "Bunia",
             "suspected_cases": None, "confirmed_cases": 100, "deaths": 20,
             "source_url": "http://a", "report_date": "2026-06-13"},
            {"disaster_id": DID, "date": "2026-06-17", "province": "Ituri", "health_zone": "Bunia",
             "suspected_cases": None, "confirmed_cases": 300, "deaths": 40,
             "source_url": "http://b", "report_date": "2026-06-19"},
            # outbreak OTHER: a different zone entirely
            {"disaster_id": OTHER, "date": "2026-06-17", "province": "Nord", "health_zone": "Zulu",
             "suspected_cases": None, "confirmed_cases": 9, "deaths": 1,
             "source_url": "http://c", "report_date": "2026-06-19"},
        ]
        return pd.DataFrame(rows, columns=CONTRACT_COLUMNS)

    def test_history_views_scope_to_one_outbreak(self):
        df = self._two_outbreak_df()
        diff_did = {d["health_zone"] for d in compute_history_diff(df, disaster_id=DID)}
        self.assertEqual(diff_did, {"Bunia"})                    # OTHER's Zulu excluded
        self.assertEqual({d["health_zone"] for d in compute_history_diff(df, disaster_id=OTHER)}, {"Zulu"})
        self.assertEqual(set(zone_trend_series(df, disaster_id=DID)["health_zone"]), {"Bunia"})
        self.assertEqual(top_zones_by_recent_change(df, disaster_id=DID), ["Bunia"])
        # unscoped (None) sees both outbreaks
        self.assertEqual(len({d["health_zone"] for d in compute_history_diff(df)}), 2)

    def test_get_prior_snapshot_scopes(self):
        df = self._two_outbreak_df()
        snap = get_prior_snapshot(df, disaster_id=DID)
        self.assertEqual(set(snap["health_zone"]), {"Bunia"})

    def test_prior_excluding_source_scopes(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "h.csv")
        try:
            self._two_outbreak_df().to_csv(path, index=False)
            prior = prior_excluding_source(path, "http://none", DID)   # exclude nothing real
            self.assertEqual({r["health_zone"] for r in prior}, {"Bunia"})   # OTHER not present
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
