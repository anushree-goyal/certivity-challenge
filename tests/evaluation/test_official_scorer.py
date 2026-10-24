"""evaluate_submission with the REAL tools/score_local.py and the dev case's expected.json.

Skipped unless the participant package is found (CERTIVITY_PARTICIPANT_PACKAGE or
data/participant-package/). The submissions here are tiny hand-made files, so the numbers
mean nothing; the tests check the wrapper and that our metrics agree with the official ones.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from certivity.evaluation import evaluate_submission_detailed


def _package():
    p = os.environ.get("CERTIVITY_PARTICIPANT_PACKAGE") or \
        Path(__file__).resolve().parents[2] / "data" / "participant-package"
    p = Path(p)
    return p if (p / "tools" / "score_local.py").is_file() else None


PKG = _package()


def req(i, reg, ver, page, para=None):
    src = {"regulation": reg, "version": ver, "page": page}
    if para:
        src["paragraph"] = para
    return {"id": f"R{i}", "text": "synthetic", "source": src}


@unittest.skipIf(PKG is None, "participant package not found (set CERTIVITY_PARTICIPANT_PACKAGE)")
class OfficialScorer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.expected = PKG / "cases" / "dev-rear-lamp" / "expected.json"

    def _eval(self, reqs):
        p = Path(self.tmp.name) / "s.json"
        p.write_text(json.dumps({"team_id": "t", "case_id": "dev-rear-lamp", "requirements": reqs}))
        return evaluate_submission_detailed(p, self.expected, participant_package_path=PKG)

    def test_scorer_runs_and_regulation_level_agrees(self):
        r = self._eval([req(1, "UN R10", "7.2.0", 5), req(2, "UN R3", "2.17.0", 4)])
        off = r.official_local_scorer
        self.assertTrue(off["ran"])
        self.assertEqual(off["returncode"], 0, off["stderr"])
        self.assertIsNotNone(off["parsed"], off["stdout"])
        self.assertEqual(off["parsed"]["regulation_level"]["n_matched"], r.own_regulation_level["true_positives"])
        self.assertAlmostEqual(off["parsed"]["regulation_level"]["precision"], r.own_regulation_level["precision"])
        self.assertEqual(r.metrics_source, "official score_local.py")
        self.assertAlmostEqual(r.report.page_recall, off["parsed"]["page_level"]["recall"])
        self.assertIsNone(r.report.semantic_score)

    def test_official_page_matches_are_a_superset_of_exact_matches(self):
        # The official scorer also credits a page via its paragraph, so it can only match more.
        r = self._eval([req(1, "UN R150", "1.2.1", 7, "3.3.1.2"), req(2, "UN R150", "1.2.1", 99, "3.3.1.1"),
                        req(3, "UN R48", "9.3.0", 1)])
        off = r.official_local_scorer["parsed"]["page_level"]
        self.assertGreaterEqual(off["n_matched"], r.own_page_level["true_positives"])

    def test_latest_version_scores_as_miss_and_is_categorised(self):
        r = self._eval([req(1, "UN R150", "1.2.1", 7), req(2, "UN R10", "7.3.0", 5)])
        wrong = r.error_items.get("wrong_version_or_page", [])
        self.assertIn(("UN R10", "7.3.0"), {(x["regulation"], x["version"]) for x in wrong})
        self.assertEqual(r.official_local_scorer["parsed"]["page_level"]["n_submitted"], 2)


if __name__ == "__main__":
    unittest.main()
