# Answers for the quality component, round 2

**From:** Anushree · **To:** Teammate 3 · **Contracts version:** 1.5.0 (on `main`)

Excellent integration report, and the real-corpus page-recall check is the most
useful number anyone has produced so far. Fetch **`main`** — `feature/agent` is
merged there, and contracts have moved 1.1.0 → 1.5.0 since you integrated.

## STOP — item 3 is inverted. Please do not send that patch to Teammate 2.

This is the most urgent thing in this reply.

You are reading contracts **1.1.0**. In **1.2.0 I adopted Teammate 2's names as
canonical**, precisely because she raised them first. So:

| Field | Canonical since 1.2.0 | Your 1.1.0 copy |
| --- | --- | --- |
| `Section.paragraph_label` | ✅ correct | ~~`number`~~ |
| `Section.primary_passage_ids` | ✅ correct | ~~`passage_ids`~~ |
| `SourcePassage.page_span` | ✅ correct | ~~`printed_page_span`~~ |
| `SectionBundle.visual_pdf_page_indices` | ✅ correct | ~~`page_refs`~~ (`PageRef` is deleted) |
| `ReferenceRequest.originating_passage_id` | ✅ correct | ~~`origin_passage_id`~~ |

**Her `reader.py` is already right on all five.** The `TypeError`s you saw are
from your checkout being three contract versions behind, not from her code. A
patch renaming her fields to the 1.1.0 names would break a component that
currently works — please retract it if it has gone out.

Her one genuine bug, which I raised separately, is the **positional**
`ReferenceResolution(...)` call at `reader.py:187`. It raises nothing and
silently puts a list in `explanation` and the message in `request`, leaving
`is_usable` false for every reference. Fix is keyword arguments.

**`page_span` semantics, as you asked:** endpoints are fine. It is the printed
labels the passage spans in order — every label where the corpus knows them,
first and last alone where it stores only the extent. I have documented it so
callers treat it as endpoints, not a page-by-page enumeration. Her start/end
storage needs no change.

## 1. Model credential — a working key exists

Confirmed provider and variables:

```
ANTHROPIC_API_KEY=sk-ant-...        # the only required one
CERTIVITY_MODEL=claude-opus-5       # default; no need to set
CERTIVITY_MODEL_EFFORT=high         # default; no need to set
```

`ANTHROPIC_AUTH_TOKEN` or an `ant auth login` profile also work — the adapter
reports which source it found without reading its value.

**Anushree holds a working key** (a teammate's organization credit) in a
gitignored `.env` at the repo root. Teammate 4 should read `.env` at startup and
need set nothing else. **Ask Anushree for the key directly — never paste it into
a chat, an issue or a commit.**

The `credentials_configured: false` you saw is correct for your own checkout, not
a team-level blocker. The prompts are verified against the live model.

## 2. A real `PipelineDraft` as JSON — path confirmed

Already implemented. Pass `artifacts_dir` and the component writes, inside that
folder only:

```
<run_dir>/agent/pipeline_draft.json        # {run_id, case_id, input_hash,
                                           #  corpus_snapshot_id,
                                           #  model_configuration,
                                           #  prompt_versions, draft}
<run_dir>/agent/pipeline_draft_revised.json
<run_dir>/agent/product_profile.json
<run_dir>/agent/applicability.json
```

The draft is under the `"draft"` key, so load it as:

```python
blob = json.load(open(path))["draft"]
draft = from_jsonable(PipelineDraft, blob)
```

The wrapper carries the input hash, snapshot ID, prompt versions and model
configuration, so a draft you score is attributable to what produced it.

I am building a real snapshot now. **Please send the exact command you used** —
you got one built from R10/R48/R148/R150, so you are ahead of me there, and it
is the last thing blocking real extraction. I will post the path as soon as
there is a draft.

## 4. `ReviewFinding.code` — accepted, plus both categories

`code: Optional[str]` is in 1.5.0. Put `page_unverified`,
`semantic_needs_context`, `numeric_not_in_evidence` there and drop the
`[code]` prefix from `explanation`. It is carried into the run record and the
UI, and it is part of the derived `finding_id`, so two findings differing only
by code get distinct IDs.

Both requested categories are also in, so stop overloading `other`:

- `FindingCategory.REVIEW_INCOMPLETE`
- `FindingCategory.SEMANTIC_UNREVIEWED`

## 5. Revision trigger — confirmed, and now defined once

Your convention is exactly right, and I moved it onto the record so the API
layer and extraction cannot disagree:

```python
ReviewFinding.is_actionable   # requested_correction or requested_context set,
                              # or severity is blocking/revision_requested
```

`revise_extraction` now **skips** draft findings where this is false, so an
informational flag that requests nothing costs no model call, and Teammate 4 can
gate on the same property. Tested both ways: a `DUPLICATE`/informational finding
with no request makes zero calls; an informational finding that *does* request
context is still acted on. Severity alone does not decide — an explicit request
always wins.

Sending the draft's own `section_id` plus its evidence passages' section IDs in
`requested_context` is the right shape. Both resolve: a passage ID is fetched
directly, a section ID pulls in that section's primary and context passages.

## 6. Real-text findings — all three are now prompt rules (`extract_obligations` v5)

Thank you for these; they are the most actionable feedback I have had.

- **Over 2,000 characters.** The prompt now states that a long passage almost
  always holds several obligations and must be split, that the export cap is
  2,000, and that shortening must never drop a value, unit, condition or
  exception. If one genuinely indivisible obligation cannot fit, it is returned
  with a note rather than truncated — so please keep flagging `too_long` rather
  than cutting.
- **Multi-page passages (89 of them).** The prompt now says to cite the
  **narrowest** passage that states the obligation, explaining that a citation
  carries the passage's *start* page, so citing a broad passage for an
  obligation on its later page cites the wrong page. Keep `primary_spans_pages`
  as a flag — where no narrower passage exists, this is a corpus granularity
  limit, not an extraction error.
- **`primary_is_context_passage`.** That note should now be rare: since v4 a
  primary citation must be a primary passage of the section being read, and one
  that is not is **dropped**, not flagged. It was producing each obligation
  twice. Keep the check as a backstop and tell me if it fires.
- **`DraftRequirement.values` — yes, use it, but as a cross-check, not as
  truth.** It is a required schema field, and v5 states that an obligation with
  a limit, tolerance or range and an empty `values` is an error. But it is
  model-produced, so compare it against the cited passage text; a disagreement
  between `values` and `text` is exactly a `numeric_not_in_evidence` finding and
  I would rather hear about it than have it reconciled silently.

## 7. Adapter details — all three confirmed

- **`quality_review` / `quality_dedup`, `prompt_version="review-v1"`.** Correct
  and sufficient. Both feed the cache key and per-task `model_overrides`, so you
  can point review at a cheaper model independently of extraction.
- **`ImagePayload(media_type, asset_ref=...)` — yes, the adapter reads and
  base64-encodes `asset_ref` itself.** Two conditions: it must be a readable
  local filesystem path (a `PageAsset.asset_ref` from the real corpus is; the
  fixture's `synthetic://` URI is not), and it is rejected above 5 MB with a
  clear error rather than silently downscaled.
- **Stopping on `LLMConfigurationError` and `ModelBudgetExceeded`, reporting the
  rest unreviewed — exactly right**, and it matches what extraction does. One
  addition in 1.4.0 you will want: a zero credit balance arrives as HTTP 400,
  not 401, and the adapter now classifies that as `LLMConfigurationError` rather
  than a per-call error. So your existing handler already covers "the key is
  valid but the account has no credit", which previously looked like a transient
  failure.

## Status on my side

113 tests passing. Prompts verified against the live model, which found three
bugs no fixture could: the provider's 16-union schema limit that made every real
run fail, scope statements returned as obligations, and every obligation
extracted twice. Still unverified: extraction over real regulation text — which
is why your bootstrap command is the thing I need most.
