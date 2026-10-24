"""Export + validation against the REAL participant package (tools/validate.py, index.json, schema).

Skipped unless the package is found: set CERTIVITY_PARTICIPANT_PACKAGE, or check it out at
data/participant-package/. Drafts, passages and the corpus are still SYNTHETIC; only the
regulation/version labels are real index entries, so the official validator accepts them.
"""
import os
import unittest
from pathlib import Path

from certivity.quality import build_submission_detailed, review_requirements, validate_submission

from . import fixtures as F


def _package():
    p = os.environ.get("CERTIVITY_PARTICIPANT_PACKAGE") or \
        Path(__file__).resolve().parents[2] / "data" / "participant-package"
    p = Path(p)
    return p if (p / "tools" / "validate.py").is_file() else None


PKG = _package()

R148 = F.Doc("doc-r148-base", "UN R148", "1.6.0")
R10 = F.Doc("doc-r10-base", "UN R10", "7.2.0")
R10_LATEST = F.Doc("doc-r10-latest", "UN R10", "7.3.0", role="latest")
PASSAGES = [
    F.Passage("p148", R148.document_id, "The luminous intensity shall be at least 4 cd.", "5.4.1", 12),
    F.Passage("p10", R10.document_id, "The ESA shall be marked with the trade name.", "4.1.1", 9),
    F.Passage("p10-nopara", R10.document_id, "Marking shall be indelible.", None, 10),
    F.Passage("p10-latest", R10_LATEST.document_id, "The ESA shall be marked.", "4.1.1", 9),
]


def corpus():
    return F.FakeCorpus([R148, R10, R10_LATEST], PASSAGES)


def export(drafts):
    pipe, c = F.Pipeline(drafts), corpus()
    rep = review_requirements(F.Run(), pipe, c, None, lambda e: None)
    return rep, build_submission_detailed(F.Run(), pipe, rep, c)


@unittest.skipIf(PKG is None, "participant package not found (set CERTIVITY_PARTICIPANT_PACKAGE)")
class OfficialValidator(unittest.TestCase):
    def test_supported_drafts_pass_official_validator(self):
        _, res = export([F.Draft("d1", "The luminous intensity shall be at least 4 cd.", "p148", ["p148"]),
                         F.Draft("d2", "The ESA shall be marked with the trade name.", "p10", ["p10"]),
                         F.Draft("d3", "Marking shall be indelible.", "p10-nopara", ["p10-nopara"])])
        self.assertEqual(len(res.submission["requirements"]), 3)
        self.assertNotIn("paragraph", res.submission["requirements"][2]["source"])  # omitted, not null
        rep = validate_submission(res.submission, PKG)
        self.assertTrue(rep.official_validator_ran, rep.official_validator_output)
        self.assertTrue(rep.valid, [str(e) for e in rep.errors])

    def test_latest_version_passes_official_validator_but_never_exports(self):
        # The official validator only checks the label is SOME version in the index, so a latest-version
        # citation is "valid" there. Our export must stop it before it gets that far.
        rep, res = export([F.Draft("d1", "The ESA shall be marked.", "p10-latest", ["p10-latest"])])
        self.assertIn("d1", rep.rejected)
        self.assertEqual(res.submission["requirements"], [])
        sub = {"team_id": "t", "case_id": "c", "requirements": [
            {"id": "R1", "text": "x", "source": {"regulation": "UN R10", "version": "7.3.0", "page": 9}}]}
        self.assertTrue(validate_submission(sub, PKG).valid)

    def test_official_validator_rejects_unknown_version_and_regulation(self):
        sub = {"team_id": "t", "case_id": "c", "requirements": [
            {"id": "R1", "text": "x", "source": {"regulation": "UN R10", "version": "06 series", "page": 9}},
            {"id": "R2", "text": "x", "source": {"regulation": "UN R999", "version": "1.0.0", "page": 9}}]}
        rep = validate_submission(sub, PKG)
        self.assertTrue(rep.official_validator_ran)
        self.assertFalse(rep.valid)
        self.assertIn("$.requirements[0].source.version", rep.official_validator_output)
        self.assertIn("$.requirements[1].source.regulation", rep.official_validator_output)

    def test_null_paragraph_rejected_by_both_layers(self):
        sub = {"team_id": "t", "case_id": "c", "requirements": [
            {"id": "R1", "text": "x", "source": {"regulation": "UN R10", "version": "7.2.0", "page": 9,
                                                 "paragraph": None}}]}
        rep = validate_submission(sub, PKG)
        self.assertIn("$.requirements[0].source.paragraph", {e.path for e in rep.errors})
        self.assertIn("$.requirements[0].source.paragraph", rep.official_validator_output)
        self.assertFalse(rep.valid)


if __name__ == "__main__":
    unittest.main()
