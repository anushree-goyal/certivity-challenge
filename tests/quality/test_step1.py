import json
import tempfile
import unittest
from pathlib import Path

from certivity.quality import (SubmissionBuildError, build_submission,
                               build_submission_detailed, review_requirements,
                               validate_submission)
from certivity.quality.limits import MAX_TEXT_CHARS

from certivity.quality._records import finding_code

from . import fixtures as F


def review(drafts):
    events = []
    rep = review_requirements(F.Run(), F.Pipeline(drafts), F.corpus(), None, events.append)
    return rep, events


def cats(rep, did):
    return {finding_code(f) for f in rep.findings if f.draft_id == did}


class DeterministicChecks(unittest.TestCase):
    def test_valid_draft_accepted(self):
        d = F.Draft("d1", "Luminous intensity shall be at least 4 cd and at most 600 cd.",
                    "p-ok", ["p-ok"])
        rep, _ = review([d])
        self.assertEqual(list(rep.accepted_draft_ids), ["d1"])
        self.assertEqual(rep.rejected, {})

    def test_wrong_latest_version_rejected(self):
        rep, _ = review([F.Draft("d1", "Intensity shall be at least 5 cd.", "p-latest", ["p-latest"])])
        self.assertIn("d1", rep.rejected)
        self.assertIn("wrong_version_or_document", cats(rep, "d1"))
        self.assertNotIn("d1", rep.accepted_draft_ids)

    def test_nonexistent_source_rejected(self):
        rep, _ = review([F.Draft("d1", "Anything.", "p-nope", ["p-nope"])])
        self.assertIn("d1", rep.rejected)
        self.assertIn("source_missing", cats(rep, "d1"))

    def test_unverified_page_needs_revision_not_accepted(self):
        rep, _ = review([F.Draft("d1", "Marking shall be indelible.", "p-nopage", ["p-nopage"])])
        self.assertNotIn("d1", rep.accepted_draft_ids)
        self.assertNotIn("d1", rep.rejected)
        self.assertIn("page_unverified", cats(rep, "d1"))
        self.assertTrue(any(f.draft_id == "d1" for f in rep.remaining_issues))

    def test_unsupported_value_flagged_not_silently_accepted(self):
        d = F.Draft("d1", "Luminous intensity shall be at least 40 cd.", "p-ok", ["p-ok"])
        rep, _ = review([d])
        self.assertIn("numeric_not_in_evidence", cats(rep, "d1"))

    def test_lost_exception_flagged(self):
        d = F.Draft("d1", "Lamps shall be red.", "p-exc", ["p-exc"])
        rep, _ = review([d])
        self.assertIn("possible_lost_condition", cats(rep, "d1"))

    def test_kept_exception_not_flagged(self):
        d = F.Draft("d1", "Lamps shall be red, unless combined with a stop lamp.", "p-exc", ["p-exc"])
        rep, _ = review([d])
        self.assertNotIn("possible_lost_condition", cats(rep, "d1"))

    def test_conditional_obligation_with_condition_field_survives(self):
        d = F.Draft("d1", "Lamps shall be red.", "p-exc", ["p-exc"],
                    applicability_condition="lamp is not combined with a stop lamp")
        rep, _ = review([d])
        self.assertEqual(list(rep.accepted_draft_ids), ["d1"])
        self.assertNotIn("possible_lost_condition", cats(rep, "d1"))

    def test_text_over_limit_not_truncated(self):
        d = F.Draft("d1", "x" * (MAX_TEXT_CHARS + 1), "p-ok", ["p-ok"])
        rep, _ = review([d])
        self.assertIn("text_too_long", cats(rep, "d1"))
        self.assertNotIn("d1", rep.accepted_draft_ids)

    def test_duplicate_draft_ids_flagged(self):
        a = F.Draft("d1", "A 4 cd.", "p-ok", ["p-ok"])
        b = F.Draft("d1", "B 4 cd.", "p-ok", ["p-ok"])
        rep, _ = review([a, b])
        self.assertEqual(list(rep.accepted_draft_ids), [])
        self.assertIn("duplicate_draft_id", cats(rep, "d1"))

    def test_multi_page_primary_flagged(self):
        rep, _ = review([F.Draft("d1", "Long paragraph.", "p-span", ["p-span"])])
        self.assertIn("primary_spans_pages", cats(rep, "d1"))

    def test_corpus_that_raises_on_unknown_ids_does_not_abort_review(self):
        class Raising(F.FakeCorpus):  # feature/corpus behaviour: UnknownId instead of omission
            def get_passages(self, ids):
                missing = [i for i in ids if i not in self.passages]
                if missing:
                    raise KeyError(missing)
                return super().get_passages(ids)
        c = F.corpus()
        c = Raising(c.docs, list(c.passages.values()))
        ok = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        bad = F.Draft("d2", "Anything.", "p-nope", ["p-nope"])
        rep = review_requirements(F.Run(), F.Pipeline([ok, bad]), c, None, None)
        self.assertEqual(list(rep.accepted_draft_ids), ["d1"])
        self.assertIn("d2", rep.rejected)

    def test_unresolved_reference_flagged_without_blocking(self):
        from certivity.contracts import ReferenceRequest, ReferenceResolution, ReferenceStatus
        ref = ReferenceResolution(ReferenceRequest("p-ok", "UN R999 Annex 9"),
                                  ReferenceStatus.OUTSIDE_CORPUS, "not in this snapshot")
        d = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"], unresolved_references=(ref,))
        rep, _ = review([d])
        self.assertIn("unresolved_reference", cats(rep, "d1"))
        self.assertEqual(list(rep.accepted_draft_ids), ["d1"])

    def test_review_never_claims_semantic_completion(self):
        d = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        rep, events = review([d])
        self.assertTrue(any(finding_code(f) == "review_incomplete" for f in rep.remaining_issues))
        self.assertIn("NOT performed", events[-1].message)

    def test_exact_duplicate_mapped_without_model(self):
        a = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        b = F.Draft("d2", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        rep, _ = review([a, b])
        self.assertEqual(rep.duplicate_mapping, {"d2": "d1"})
        self.assertEqual(list(rep.accepted_draft_ids), ["d1"])


class Export(unittest.TestCase):
    def _build(self, drafts, accepted=None):
        rep, _ = review(drafts)
        if accepted is not None:
            rep = F.with_accepted(rep, accepted)
        return F.Run(), F.Pipeline(drafts), rep

    def test_export_shape_and_source_resolution(self):
        d = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        run, pipe, rep = self._build([d])
        sub = build_submission(run, pipe, rep, F.corpus())
        self.assertEqual(set(sub), {"team_id", "case_id", "requirements"})
        self.assertEqual(sub["team_id"], "team-x")
        item = sub["requirements"][0]
        self.assertEqual(set(item), {"id", "text", "source"})
        self.assertEqual(item["source"], {"regulation": "UN R999", "version": "01 series",
                                          "page": 12, "paragraph": "6.2.1"})

    def test_ids_unique_and_trace_kept(self):
        ds = [F.Draft(f"d{i}", f"Intensity {i} at least 4 cd.", "p-ok", ["p-ok"]) for i in range(3)]
        run, pipe, rep = self._build(ds)
        res = build_submission_detailed(run, pipe, rep, F.corpus())
        ids = [r["id"] for r in res.submission["requirements"]]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual([t["draft_id"] for t in res.trace], ["d0", "d1", "d2"])

    def test_rejected_draft_not_exported(self):
        good = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        bad = F.Draft("d2", "Intensity at least 5 cd.", "p-latest", ["p-latest"])
        run, pipe, rep = self._build([good, bad])
        sub = build_submission(run, pipe, rep, F.corpus())
        self.assertEqual(len(sub["requirements"]), 1)

    def test_export_re_checks_even_if_report_lies(self):
        bad = F.Draft("d2", "Intensity at least 5 cd.", "p-latest", ["p-latest"])
        run, pipe, rep = self._build([bad], accepted=["d2"])
        res = build_submission_detailed(run, pipe, rep, F.corpus())
        self.assertEqual(res.submission["requirements"], [])
        self.assertIn("d2", res.excluded)

    def test_oversize_text_excluded_not_truncated(self):
        d = F.Draft("d1", "x" * 2001, "p-ok", ["p-ok"])
        run, pipe, rep = self._build([d], accepted=["d1"])
        res = build_submission_detailed(run, pipe, rep, F.corpus())
        self.assertIn("d1", res.excluded)
        self.assertEqual(res.submission["requirements"], [])

    def test_too_many_requirements_is_explicit_error(self):
        ds = [F.Draft(f"d{i}", f"Intensity at least 4 cd, variant {i}.", "p-ok", ["p-ok"]) for i in range(5001)]
        run, pipe, rep = self._build(ds)
        with self.assertRaises(SubmissionBuildError):
            build_submission(run, pipe, rep, F.corpus())


class Validation(unittest.TestCase):
    def _package(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tools = Path(tmp.name) / "tools"
        tools.mkdir()
        (tools / "validate.py").write_text(body)
        return tmp.name

    # STUB validators test the wrapper's plumbing only, not the real official validator.
    PASS = "import sys, json; json.load(open(sys.argv[1])); print('ok')"
    FAIL = "import sys; print('bad source.page'); sys.exit(1)"

    def _sub(self):
        d = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        rep, _ = review([d])
        return build_submission(F.Run(), F.Pipeline([d]), rep, F.corpus())

    def test_valid_when_both_layers_pass(self):
        rep = validate_submission(self._sub(), self._package(self.PASS))
        self.assertTrue(rep.valid)
        self.assertTrue(rep.official_validator_ran)

    def test_official_failure_surfaces(self):
        rep = validate_submission(self._sub(), self._package(self.FAIL))
        self.assertFalse(rep.valid)
        self.assertIn("bad source.page", rep.official_validator_output)

    def test_missing_official_validator_is_not_valid(self):
        with tempfile.TemporaryDirectory() as empty:
            rep = validate_submission(self._sub(), empty)
        self.assertFalse(rep.valid)
        self.assertFalse(rep.official_validator_ran)

    def test_structural_errors_have_json_paths(self):
        sub = self._sub()
        sub["requirements"][0]["source"]["page"] = 0
        sub["requirements"].append(dict(sub["requirements"][0]))  # duplicate id
        rep = validate_submission(sub, self._package(self.PASS))
        paths = {e.path for e in rep.errors}
        self.assertIn("$.requirements[0].source.page", paths)
        self.assertIn("$.requirements[1].id", paths)
        self.assertFalse(rep.valid)


if __name__ == "__main__":
    unittest.main()
