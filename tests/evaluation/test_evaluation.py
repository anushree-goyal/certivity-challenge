"""SYNTHETIC data only. The golden answer is never read here; tiny invented files stand in."""
import json
import tempfile
import unittest
from pathlib import Path

from certivity.evaluation import (compare_candidates, evaluate_submission, list_candidates,
                                  load_candidate, save_candidate)


def req(reg, page, i=1):
    return {"id": f"R{i}", "text": "t", "source": {"regulation": reg, "version": "v", "page": page}}


def write(tmp, name, reqs):
    p = Path(tmp) / name
    p.write_text(json.dumps({"team_id": "t", "case_id": "c", "requirements": reqs}))
    return p


class Evaluate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_metrics_and_scope_label(self):
        exp = write(self.tmp.name, "e.json", [req("A", 1), req("A", 2), req("B", 5)])
        sub = write(self.tmp.name, "s.json", [req("A", 1), req("C", 9)])
        r = evaluate_submission(sub, exp)
        self.assertEqual(r.regulation_level["true_positives"], 1)
        self.assertAlmostEqual(r.regulation_level["recall"], 0.5)
        self.assertAlmostEqual(r.page_level["precision"], 0.5)
        self.assertAlmostEqual(r.page_level["recall"], 1 / 3)
        self.assertIn("NOT measured", r.scope)

    def test_semantic_scores_stay_null(self):
        p = write(self.tmp.name, "s.json", [req("A", 1)])
        r = evaluate_submission(p, p)
        self.assertIsNone(r.semantic_score)
        self.assertIsNone(r.platform_score)

    def test_miss_categories(self):
        exp = write(self.tmp.name, "e.json", [req("A", 10), req("B", 5), req("D", 1), req("E", 1)])
        sub = write(self.tmp.name, "s.json", [req("A", 11), req("C", 9)])
        r = evaluate_submission(sub, exp, run_context={"excluded_regulations": ["D"]})
        c = r.error_categories
        self.assertEqual(c["wrong_version_or_page"][0]["regulation"], "A")
        self.assertEqual(c["applicable_regulation_excluded"][0]["regulation"], "D")
        self.assertEqual(sorted(x["regulation"] for x in c["regulation_not_covered"]), ["B", "E"])
        self.assertEqual(c["unsupported_output"][0]["regulation"], "C")

    def test_official_scorer_missing_is_reported_not_faked(self):
        p = write(self.tmp.name, "s.json", [req("A", 1)])
        r = evaluate_submission(p, p, participant_package_path=self.tmp.name)
        self.assertFalse(r.official_local_scorer["ran"])

    def test_official_scorer_output_captured(self):  # STUB scorer: tests wrapper plumbing only
        pkg = Path(self.tmp.name) / "pkg"
        (pkg / "tools").mkdir(parents=True)
        (pkg / "tools" / "score_local.py").write_text("import json; print(json.dumps({'f1': 0.5}))")
        p = write(self.tmp.name, "s.json", [req("A", 1)])
        r = evaluate_submission(p, p, participant_package_path=pkg)
        self.assertEqual(r.official_local_scorer["parsed"], {"f1": 0.5})


class Candidates(unittest.TestCase):
    def test_save_load_compare(self):
        with tempfile.TemporaryDirectory() as d:
            a = {"team_id": "t", "case_id": "c", "requirements": [req("A", 1)]}
            b = {"team_id": "t", "case_id": "c", "requirements": [req("A", 1), req("B", 2, 2)]}
            ia = save_candidate(d, a, {"run_id": "r1", "prompt": "review-v1"})
            ib = save_candidate(d, b, {"run_id": "r2"})
            self.assertNotEqual(ia, ib)
            self.assertEqual(load_candidate(d, ia), a)
            self.assertEqual({m["run_id"] for m in list_candidates(d)}, {"r1", "r2"})
            self.assertEqual(compare_candidates(a, b)["pairs_only_in_b"], [("B", "v", 2)])


class Boundary(unittest.TestCase):
    def test_golden_answer_only_referenced_in_evaluation_package(self):
        root = Path(__file__).resolve().parents[2] / "src" / "certivity"
        offenders = []
        for f in root.rglob("*.py"):
            if "evaluation" in f.parts:
                continue
            if "expected.json" in f.read_text(encoding="utf-8"):
                offenders.append(str(f))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
