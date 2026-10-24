import unittest

from certivity.contracts import ImagePayload, LLMConfigurationError, PageAsset

from certivity.quality import build_submission, review_requirements
from certivity.quality.limits import MAX_TEXT_CHARS  # noqa: F401

from certivity.quality._records import finding_code

from . import fixtures as F
from .fixtures import FakeLLM, ok_review


def run(drafts, llm):
    ev = []
    rep = review_requirements(F.Run(), F.Pipeline(drafts), F.corpus(), llm, ev.append)
    return rep, ev


def reviewer(fn):
    return FakeLLM({"quality_review": lambda p: {"reviews": [fn(d) for d in p["drafts"]]},
                    "quality_dedup": lambda p: {"groups": []}})


def cats(rep, did):
    return {finding_code(f) for f in rep.findings if f.draft_id == did}


def incomplete(rep):
    return [f for f in rep.remaining_issues if finding_code(f) == "review_incomplete"]


GOOD = F.Draft("d1", "Luminous intensity shall be at least 4 cd.", "p-ok", ["p-ok"])


class SemanticReview(unittest.TestCase):
    def test_supported_with_real_quote_accepted_and_complete(self):
        rep, ev = run([GOOD], reviewer(lambda d: ok_review(d["draft_id"])))
        self.assertEqual(list(rep.accepted_draft_ids), ["d1"])
        self.assertEqual(incomplete(rep), [])
        self.assertIn("complete", ev[-1].message)

    def test_fabricated_quote_downgrades_model_approval(self):
        rep, _ = run([GOOD], reviewer(lambda d: ok_review(d["draft_id"], quote="shall glow green")))
        self.assertNotIn("d1", rep.accepted_draft_ids)
        self.assertIn("semantic_needs_revision", cats(rep, "d1"))

    def test_supported_but_a_check_problem_is_not_trusted(self):
        def r(d):
            x = ok_review(d["draft_id"])
            x["checks"]["unit"] = "problem"
            return x
        rep, _ = run([GOOD], reviewer(r))
        self.assertNotIn("d1", rep.accepted_draft_ids)

    def test_unsupported_value_rejected_with_reason(self):
        bad = F.Draft("d1", "Luminous intensity shall be at least 40 cd.", "p-ok", ["p-ok"])
        def r(d):
            x = ok_review(d["draft_id"], verdict="unsupported", explanation="evidence says 4 cd, not 40",
                          evidence_quote=None)
            x["checks"]["value"] = "problem"
            return x
        rep, _ = run([bad], reviewer(r))
        self.assertIn("d1", rep.rejected)
        self.assertIn("evidence says 4 cd", rep.rejected["d1"])

    def test_condition_only_problem_is_revision_not_rejection(self):
        d1 = F.Draft("d1", "Lamps shall be red.", "p-exc", ["p-exc"])
        def r(d):
            x = ok_review(d["draft_id"], verdict="unsupported", explanation="drops the stop-lamp exception",
                          evidence_quote=None)
            x["checks"]["exception"] = "problem"
            return x
        rep, _ = run([d1], reviewer(r))
        self.assertNotIn("d1", rep.rejected)
        self.assertIn("d1", {f.draft_id for f in rep.remaining_issues})

    def test_needs_context_carries_request(self):
        def r(d):
            x = ok_review(d["draft_id"], verdict="needs_context", explanation="table headers missing",
                          requested_context="table header page", evidence_quote=None)
            x["checks"]["unit"] = "unclear"
            return x
        rep, _ = run([GOOD], reviewer(r))
        f = [f for f in rep.findings if finding_code(f) == "semantic_needs_context"][0]
        self.assertEqual(f.requested_context, (F.P_OK.section_id,))  # resolvable ids
        self.assertIn("table header page", f.explanation)            # the model's prose
        self.assertNotIn("d1", rep.accepted_draft_ids)

    def test_unknown_product_fact_conditional_rule_survives(self):
        d1 = F.Draft("d1", "Lamps shall be red, unless combined with a stop lamp.", "p-exc", ["p-exc"],
                     applicability_condition="only if the lamp is a rear position lamp")
        rep, _ = run([d1], reviewer(lambda d: ok_review(d["draft_id"], quote="shall be red")))
        self.assertEqual(list(rep.accepted_draft_ids), ["d1"])

    def test_model_failure_marks_unreviewed_not_complete(self):
        llm = FakeLLM({"quality_review": lambda p: RuntimeError("timeout"),
                       "quality_dedup": lambda p: {"groups": []}})
        rep, ev = run([GOOD], llm)
        self.assertEqual(len(incomplete(rep)), 1)
        self.assertIn("d1", incomplete(rep)[0].explanation)
        self.assertIn("PARTIAL", ev[-1].message)

    def test_missing_review_for_a_draft_is_unreviewed(self):
        a = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        b = F.Draft("d2", "Intensity at most 600 cd.", "p-ok", ["p-ok"])
        rep, _ = run([a, b], FakeLLM({"quality_review": lambda p: {"reviews": [ok_review("d1")]},
                                      "quality_dedup": lambda p: {"groups": []}}))
        self.assertIn("d2", incomplete(rep)[0].explanation)

    def test_call_budget_makes_review_partial(self):
        from certivity.quality.semantic import SemanticConfig
        ds = [F.Draft(f"d{i}", f"Intensity at least 4 cd variant {i}.", "p-ok", ["p-ok"]) for i in range(4)]
        llm = reviewer(lambda d: ok_review(d["draft_id"]))
        rep = review_requirements(F.Run(), F.Pipeline(ds), F.corpus(), llm, None,
                                  config=SemanticConfig(batch_size=2, max_calls=1))
        self.assertEqual(len([c for c in llm.calls if c["task"] == "quality_review"]), 1)
        self.assertEqual(len(incomplete(rep)), 1)

    def test_evidence_in_payload_is_the_real_passage(self):
        llm = reviewer(lambda d: ok_review(d["draft_id"]))
        run([GOOD], llm)
        ev = llm.calls[0]["payload"]["drafts"][0]["evidence"][0]
        self.assertEqual(ev["passage_id"], "p-ok")
        self.assertIn("4 cd", ev["text"])

    def test_table_flagged_passage_sends_page_image(self):
        tp = F.Passage("p-tab", F.BASE.document_id, "Table: 4 cd min.", "9", 30,
                       is_table=True, pdf_page_indices=(31,), table_context="Intensity in cd")
        c = F.FakeCorpus([F.BASE], [tp])
        c.get_page_asset = lambda doc, idx: PageAsset(doc, idx, "30", "image/png", f"/cache/{doc}/{idx}.png")
        llm = reviewer(lambda d: ok_review(d["draft_id"], quote="4 cd min"))
        review_requirements(F.Run(), F.Pipeline([F.Draft("d1", "At least 4 cd.", "p-tab", ["p-tab"])]),
                            c, llm, None)
        (img,) = llm.calls[0]["images"]
        self.assertIsInstance(img, ImagePayload)
        self.assertEqual(img.asset_ref, f"/cache/{F.BASE.document_id}/31.png")
        self.assertEqual(llm.calls[0]["payload"]["drafts"][0]["evidence"][0]["table_context"],
                         "Intensity in cd")


class MissingCredential(unittest.TestCase):
    def test_stops_after_first_call_and_reports_every_draft_unreviewed(self):
        drafts = [F.Draft(f"d{i}", f"Luminous intensity shall be at least 4 cd, case {i}.", "p-ok", ["p-ok"])
                  for i in range(7)]  # two batches at the default batch size of 5
        llm = FakeLLM({"quality_review": lambda p: LLMConfigurationError("no credential"),
                       "quality_dedup": lambda p: {"groups": []}})
        rep, _ = run(drafts, llm)
        self.assertEqual(len([c for c in llm.calls if c["task"] == "quality_review"]), 1)
        for d in drafts:
            self.assertIn("semantic_unreviewed", cats(rep, d.draft_id))
        self.assertTrue(incomplete(rep))


class Dedup(unittest.TestCase):
    def test_same_wording_different_primary_passage_kept(self):
        p2 = F.Passage("p-ok2", F.BASE.document_id, "The luminous intensity shall be at least 4 cd.", "6.9", 15)
        c = F.corpus(); c.passages["p-ok2"] = p2
        a = F.Draft("d1", "Intensity at least 4 cd.", "p-ok", ["p-ok"])
        b = F.Draft("d2", "Intensity at least 4 cd.", "p-ok2", ["p-ok2"])
        llm = reviewer(lambda d: ok_review(d["draft_id"]))
        rep = review_requirements(F.Run(), F.Pipeline([a, b]), c, llm, None)
        self.assertEqual(rep.duplicate_mapping, {})
        self.assertEqual(sorted(rep.accepted_draft_ids), ["d1", "d2"])

    def test_different_conditions_survive_exact_dedup(self):
        a = F.Draft("d1", "Lamps shall be red.", "p-exc", ["p-exc"], applicability_condition="rear lamp")
        b = F.Draft("d2", "Lamps shall be red.", "p-exc", ["p-exc"], applicability_condition="stop lamp")
        rep, _ = run([a, b], reviewer(lambda d: ok_review(d["draft_id"], quote="shall be red")))
        self.assertEqual(rep.duplicate_mapping, {})
        self.assertEqual(sorted(rep.accepted_draft_ids), ["d1", "d2"])

    def _near(self, verdict, cond_b=None):
        a = F.Draft("d1", "The luminous intensity shall be at least 4 cd and at most 600 cd.", "p-ok", ["p-ok"])
        b = F.Draft("d2", "Luminous intensity shall be at least 4 cd and at most 600 cd.", "p-ok", ["p-ok"],
                    applicability_condition=cond_b)
        llm = FakeLLM({"quality_review": lambda p: {"reviews": [
                           ok_review(d["draft_id"], quote="at least 4 cd and at most 600 cd") for d in p["drafts"]]},
                       "quality_dedup": lambda p: {"groups": [verdict(p["groups"][0])]}})
        return run([a, b], llm)[0]

    def test_near_duplicate_confirmed_by_model_removed_with_mapping(self):
        rep = self._near(lambda g: {"group_id": g["group_id"], "keep": "d1", "duplicates": ["d2"],
                                    "distinct": [], "explanation": "same limits"})
        self.assertEqual(rep.duplicate_mapping, {"d2": "d1"})
        self.assertEqual(list(rep.accepted_draft_ids), ["d1"])

    def test_model_duplicate_claim_overridden_when_conditions_differ(self):
        rep = self._near(lambda g: {"group_id": g["group_id"], "keep": "d1", "duplicates": ["d2"],
                                    "distinct": [], "explanation": "same"}, cond_b="only for trailers")
        self.assertEqual(rep.duplicate_mapping, {})
        self.assertEqual(sorted(rep.accepted_draft_ids), ["d1", "d2"])
        self.assertIn("duplicate_candidate_kept_distinct", cats(rep, "d2"))

    def test_near_duplicates_without_model_kept_and_flagged(self):
        a = F.Draft("d1", "The luminous intensity shall be at least 4 cd and at most 600 cd.", "p-ok", ["p-ok"])
        b = F.Draft("d2", "Luminous intensity shall be at least 4 cd and at most 600 cd.", "p-ok", ["p-ok"])
        rep, _ = run([a, b], None)
        self.assertEqual(rep.duplicate_mapping, {})
        self.assertEqual(sorted(rep.accepted_draft_ids), ["d1", "d2"])
        self.assertTrue(incomplete(rep))

    def test_removed_duplicate_not_exported(self):
        rep = self._near(lambda g: {"group_id": g["group_id"], "keep": "d1", "duplicates": ["d2"],
                                    "distinct": [], "explanation": "same"})
        drafts = [F.Draft("d1", "The luminous intensity shall be at least 4 cd and at most 600 cd.", "p-ok", ["p-ok"]),
                  F.Draft("d2", "Luminous intensity shall be at least 4 cd and at most 600 cd.", "p-ok", ["p-ok"])]
        sub = build_submission(F.Run(), F.Pipeline(drafts), rep, F.corpus())
        self.assertEqual(len(sub["requirements"]), 1)


if __name__ == "__main__":
    unittest.main()
