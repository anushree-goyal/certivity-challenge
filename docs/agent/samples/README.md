# Sample extraction artifact

One real `PipelineDraft`, committed so the quality component can score offline
without an API key or a run of its own. Runs normally live under `runs/`, which
is not in git.

## `pipeline_draft-run-checkpoint3.json` (192 KB)

| | |
| --- | --- |
| Run | `run-checkpoint3` |
| Case | `dev-rear-lamp` (the real supplied specification) |
| Regulation | UN R148, base, v1.6.0 |
| Scope | paragraphs 5.1–5.2, **two sections only** |
| Extracted from snapshot | `snap-101212227810` (corpus `df6c058`) |
| Also verified against | `snap-23dc9c8c077d` (corpus `67ed3de`) — all 22 cited passage IDs resolve |
| Model | `claude-opus-5`, 9 calls, 77,121 in / 52,174 out |
| Prompt versions | `extract_obligations` v5, `product_profile` v2 |

Load it as:

```python
blob = json.load(open("docs/agent/samples/pipeline_draft-run-checkpoint3.json"))
draft = from_jsonable(PipelineDraft, blob["draft"])
```

The wrapper around `"draft"` carries `input_hash`, `corpus_snapshot_id`,
`model_configuration` and `prompt_versions`, so a score is attributable to what
produced it.

## Read this before reporting numbers

**Recall is meaningless.** Two sections of one regulation, against a golden
answer spanning four. Judge precision and citation correctness only.

**It is still marked `partial`,** correctly — only two sections were planned, so
coverage is not a claim about R148.

**Passage IDs are stable across snapshots.** Verified above: the draft was
produced on `snap-101212227810` and every cited ID still resolves on
`snap-23dc9c8c077d`, across a change from 6,273 to 6,300 sections. That is the
point of label-keyed passage IDs — re-sectioning does not invalidate a citation.

**Values are the corrected ones.** This draft postdates the superscript fix, so
photometric values read `4.2∙10^1 cd` (42 cd) and `5∙10^-2 cd` (0.05 cd). The
earlier `run-checkpoint2` draft has them corrupted; do not score that one.

**23 unresolved references are recorded**, all of one of two shapes: a trailing
sub-item (`Par. 4.8.3.1. (a)`) and table or figure targets (`Table 4`,
`Table A2-1`, `Figure A3-I`). Both are open corpus items raised in
`docs/agent/corpus-pr-review.md`, not extraction defects — the obligations
citing them are correspondingly incomplete.


## `pipeline_draft-v6.json` — use this one

Same two R148 sections, re-extracted with `extract_obligations` **v6**, which
answers all five findings from the first offline scoring round.

| | |
| --- | --- |
| Run | `run-v6check` |
| Snapshot | `snap-e31747586003` |
| Obligations | **6** (v5 produced 29) |
| Model | 9 calls, 87,669 in / 46,270 out |

What changed, measured:

- **24 front-lamp obligations excluded.** Every remaining draft is from 5.2
  (rear lamps). The excluded ones carry reasons naming the categories, e.g.
  "written for front position lamps (A, MA) and front end-outline marker lamps
  (AM); the product is a rear lamp module".
- **5.2.1 is no longer over-split.** One mandatory obligation carrying Table 4,
  plus one `permitted_alternative` for the separate 60 cd allowance — down from
  12 drafts.
- **Plain decimals in `text`**: "4 cd", "17 cd", "60 cd", "0.05 cd". `values`
  still carries the source notation (`4∙10^0`, `5∙10^-2`), so text is readable
  and evidence stays faithful.
- **Citations are on the operative paragraph**: 5.2.7 now cites 5.2.7, not the
  parent "Specific technical requirements" passage.
- One `exclusion` remains (5.2.3, "No requirements apply to..."). It is marked
  `obligation_kind=exclusion` and **should be filtered in export** — it is
  applicability information, not a requirement.
