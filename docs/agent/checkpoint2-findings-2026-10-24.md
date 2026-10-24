# Checkpoint 2: real corpus through real extraction

**Date:** 2026-10-24 · **Snapshot:** `snap-e3d43fb50521` · **Run:** `run-checkpoint2`

First run of extraction over real regulation text. Built the base snapshot from
the participant package (56 documents, 3,029 pages, 6,280 sections, 31,305
passages, 13,071 reference candidates, 84 s, zero parse failures), then ran the
real `dev-rear-lamp` specification through the product profile and extracted two
real sections of **UN R148**.

**Result: it works.** 5 model calls, 34,003 input / 19,127 output tokens, real
citations with correct printed pages. The draft is saved at
`runs/run-checkpoint2/agent/pipeline_draft.json` (105 KB).

Extraction quality on real text held up: values and units preserved, conditions
carried per category, and `obligation_kind` correctly separated a **mandatory**
limit from an **exclusion** ("No requirements apply to the minimum or maximum
area of the apparent surface") and a **permitted alternative** ("may be
considered, at the request of the applicant").

## Two corpus bugs, in priority order

### 1. Superscript exponents are flattened — every photometric value is wrong

This is the most serious defect found so far, and it is invisible to every
check we currently run.

The parser renders the PDF's `4·10⁰`, `1.40·10²`, `7.0·10¹` as:

```
'r AM | 4∙100 | 1.40∙102 | 7.0∙101 | Figure A3-I | Tab'
```

So `4 cd`, `140 cd` and `70 cd` reach extraction as `4∙100`, `1.40∙102`,
`7.0∙101`. The model copies them faithfully — it is doing the right thing with
the wrong input — and produces requirements like *"shall not exceed a maximum
luminous intensity of 4.2∙101 cd"*, where the source says **42 cd**.

Why no existing check catches it: a reviewer comparing the requirement text
against the cited passage text sees a **match**, because both carry the same
corrupted string. Numeric verification against `DraftRequirement.values` also
passes. The error only shows against the PDF itself.

Photometric limits are the substance of R148, so this affects most of its
requirements. Suggested fix in the parser: detect a superscript span (pymupdf
exposes font size and baseline offset per span) and emit `4e0`, `1.40e2`, or
`4×10^0`. Any unambiguous form is fine — the current one is ambiguous.

### 2. `resolve_reference` reads a field that no longer exists

Every one of the 12 references in two sections failed with:

```
'ReferenceRequest' object has no attribute 'target_annex'
```

The contract field is `target_namespace` (this is the rename requested in
round 2, item 2). Until it lands, **reference following is completely
non-functional on real text** — `Table 4`, `Figure A3-I`, `Table A2-1`,
`Par. 4.8.3.1.` and `paragraph 4.6` all went unresolved. The agent degraded
correctly, recording each as unresolved rather than guessing, but the content
behind those references is missing from the output.

My side is hardened against the related positional-construction bug: a
resolution whose `request` is not a `ReferenceRequest` gets the real request
attached instead of crashing the visited set.

### Also confirmed from the earlier list

- `Section.ordinal` is `0` for every section. I no longer need it urgently:
  `plan.py` now natural-sorts the paragraph label, grouped by namespace, so
  `3.2` sorts before `3.10`. A real `ordinal` is still better, since only the
  corpus knows the physical order.
- `Document.index_entry` is empty, so applicability screening sees only titles.
- `is_table` / `table_context` are unset. Table **content** is captured in the
  passage text with `|` separators and the caption line, which is better than
  expected and may be sufficient.
- `printed_start_page` and `page_span` are integers, not strings. Harmless in
  practice; whoever writes `source.page` should match the official schema.

## For the quality component

`runs/run-checkpoint2/agent/pipeline_draft.json` is ready to score:

```python
blob = json.load(open("runs/run-checkpoint2/agent/pipeline_draft.json"))
draft = from_jsonable(PipelineDraft, blob["draft"])
```

Two caveats before reading the numbers. It covers **two sections of one
regulation**, so recall against `expected.json` is meaningless — judge precision
and citation correctness only. And **every photometric value in it is corrupted
by bug 1 above**, so a numeric mismatch found against the PDF is a corpus
defect, not an extraction defect.

One thing worth your opinion: the model carries the source's own
cross-references into the requirement text, e.g. *"(Par. 4.8.3.1. (b))"*. It is
faithful and aids traceability, but it may read as noise in a submitted
requirement. Tell me which you prefer and I will adjust the prompt.
