import json
import os
import tempfile
import unittest

from evals.run_evals import run_evals


class TestEvals(unittest.TestCase):
    def test_all_eval_cases_pass(self):
        # Write the artifact to a temp path so the committed evals/eval_results.json is not
        # mutated by the test run.
        tmp = os.path.join(tempfile.mkdtemp(), "results.json")
        report = run_evals(results_path=tmp)

        failures = [r for r in report["results"] if not r["passed"]]
        self.assertTrue(report["summary"]["all_passed"], msg=f"eval failures: {failures}")
        self.assertEqual(report["summary"]["passed"], report["summary"]["total"])
        self.assertEqual(report["summary"]["total"], 5)

    def test_results_artifact_is_well_formed(self):
        tmp = os.path.join(tempfile.mkdtemp(), "results.json")
        run_evals(results_path=tmp)
        with open(tmp, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertIn("summary", on_disk)
        self.assertEqual(len(on_disk["results"]), 5)
        for r in on_disk["results"]:
            self.assertIn("scenario", r)
            self.assertIn("passed", r)
            self.assertIn("expected", r)
            self.assertIn("actual", r)


if __name__ == "__main__":
    unittest.main()
