import unittest

from certivity.quality import build_submission, build_submission_detailed, review_requirements
from certivity.quality.deterministic import NEEDS_REVISION, check_drafts
from certivity.quality.semantic import SEM_CONTEXT, SEM_REVISION

from certivity.quality._records import finding_code

from . import fixtures as F
from .fixtures import FakeLLM, ok_review


def has_request(f):
    return bool(getattr(f, "requested_context", None) or getattr(f, "requested_correction", None))


class ExportMapping(unittest.TestCase):
    def test_empty_until_export(self):
        d = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        rep = review_requirements(F.Run(), F.Pipeline([d]), F.corpus(), None, None)
        self.assertEqual(rep.export_mapping, {})

    def test_mapping_matches_requirements_not_export_order(self):
        a = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        bad = F.Draft("d2", "At least 5 cd.", "p-latest", ["p-latest"])   # rejected: latest version
        c = F.Draft("d3", "Intensity at most 600 cd.", "p-ok", ["p-ok"])
        pipe = F.Pipeline([a, bad, c])
        rep = review_requirements(F.Run(), pipe, F.corpus(), None, None)
        res = build_submission_detailed(F.Run(), pipe, rep, F.corpus())
        sub, rep = res.submission, res.report
        self.assertEqual(rep.export_mapping, {"d1": "REQ-0001", "d3": "REQ-0002"})
        self.assertEqual({r["id"] for r in sub["requirements"]}, set(rep.export_mapping.values()))
        texts = {r["id"]: r["text"] for r in sub["requirements"]}
        self.assertEqual(texts[rep.export_mapping["d3"]], "Intensity at most 600 cd.")

    def test_excluded_at_export_is_not_in_mapping(self):
        d = F.Draft("d1", "x" * 2001, "p-ok", ["p-ok"])
        rep = review_requirements(F.Run(), F.Pipeline([d]), F.corpus(), None, None)
        rep = F.with_accepted(rep, ["d1"])         # force it past review
        res = build_submission_detailed(F.Run(), F.Pipeline([d]), rep, F.corpus())
        self.assertEqual(res.export_mapping, {})
        self.assertEqual(rep.export_mapping, {})

    def test_frozen_report_is_copied_with_mapping_not_mutated(self):
        d = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        rep = review_requirements(F.Run(), F.Pipeline([d]), F.corpus(), None, None)
        res = build_submission_detailed(F.Run(), F.Pipeline([d]), rep, F.corpus())
        self.assertEqual(res.report.export_mapping, {"d1": "REQ-0001"})
        self.assertEqual(rep.export_mapping, {})
        self.assertEqual(res.report.run_id, rep.run_id)


class RequestedFields(unittest.TestCase):
    def _all_revision_findings(self):
        """One draft per deterministic revision path + semantic paths."""
        too_long = F.Draft("long", "x" * 2001, "p-ok", ["p-ok"])
        empty = F.Draft("empty", "  ", "p-ok", ["p-ok"])
        nopage = F.Draft("page", "Marking shall be indelible.", "p-nopage", ["p-nopage"])
        bad_support = F.Draft("sup", "Intensity at least 4 cd.", "p-ok", ["p-ok", "p-latest", "p-gone"])
        dup1 = F.Draft("dup", "A 4 cd.", "p-ok", ["p-ok"])
        dup2 = F.Draft("dup", "B 4 cd.", "p-ok", ["p-ok"])
        return [too_long, empty, nopage, bad_support, dup1, dup2]

    def test_every_deterministic_revision_finding_has_a_request(self):
        res = check_drafts(F.Run(), F.Pipeline(self._all_revision_findings()), F.corpus())
        revision = [f for f in res.findings if finding_code(f) in NEEDS_REVISION]
        self.assertGreaterEqual({finding_code(f) for f in revision}.__len__(), 5)
        self.assertEqual([finding_code(f) for f in revision if not has_request(f)], [])

    def test_bad_supporting_passage_asks_for_revision_not_rejection(self):
        d = F.Draft("sup", "Intensity at least 4 cd.", "p-ok", ["p-ok", "p-latest", "p-gone"])
        res = check_drafts(F.Run(), F.Pipeline([d]), F.corpus())
        self.assertNotIn("sup", res.rejected)
        self.assertIn("sup", res.needs_revision)
        self.assertEqual({finding_code(f) for f in res.findings if f.draft_id == "sup"} & {"supporting_passage_invalid"},
                         {"supporting_passage_invalid"})

    def test_semantic_revision_and_context_findings_have_requests(self):
        def r(d):
            if d["draft_id"] == "rev":
                x = ok_review("rev", verdict="needs_revision", explanation="drops exception", evidence_quote=None)
            else:
                x = ok_review("ctx", verdict="needs_context", explanation="table headers missing",
                              requested_context=None, evidence_quote=None)
            return x
        llm = FakeLLM({"quality_review": lambda p: {"reviews": [r(d) for d in p["drafts"]]},
                       "quality_dedup": lambda p: {"groups": []}})
        ds = [F.Draft("rev", "Lamps shall be red.", "p-exc", ["p-exc"], applicability_condition="c"),
              F.Draft("ctx", "Intensity at least 4 cd.", "p-ok", ["p-ok"])]
        rep = review_requirements(F.Run(), F.Pipeline(ds), F.corpus(), llm, None)
        by_cat = {finding_code(f): f for f in rep.findings}
        self.assertTrue(by_cat[SEM_REVISION].requested_correction)
        self.assertEqual(by_cat[SEM_CONTEXT].requested_context, (F.P_OK.section_id,))  # ids, not prose

    def test_fake_quote_downgrade_has_request(self):
        llm = FakeLLM({"quality_review": lambda p: {"reviews": [ok_review("d1", quote="shall glow green")]},
                       "quality_dedup": lambda p: {"groups": []}})
        d = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        rep = review_requirements(F.Run(), F.Pipeline([d]), F.corpus(), llm, None)
        f = [f for f in rep.findings if finding_code(f) == SEM_REVISION][0]
        self.assertTrue(has_request(f))

    def test_all_remaining_revision_issues_carry_a_request_end_to_end(self):
        rep = review_requirements(F.Run(), F.Pipeline(self._all_revision_findings()), F.corpus(), None, None)
        asking = [f for f in rep.remaining_issues if finding_code(f) != "review_incomplete"]
        self.assertTrue(asking)
        self.assertEqual([finding_code(f) for f in asking if not has_request(f)], [])


if __name__ == "__main__":
    unittest.main()
