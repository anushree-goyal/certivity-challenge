# Applicability on the real index, and what a full run actually costs

**Date:** 2026-10-24 · **Snapshot:** 56 base documents · **Case:** `dev-rear-lamp`

First test of the applicability stage against the real 56-regulation index. Both
earlier checkpoints bypassed it to save model calls, so the stage that decides
*which regulations get read at all* had never run on real data.

Measurable because the dev case's golden answer is known to span four
regulations. Those four were named only in the scoring script, never shown to
the pipeline, which saw the index and the specification and nothing else.

## Result: 4/4 recall, nothing missed

| | |
| --- | --- |
| Decisions | 56 of 56 regulations |
| Applicable | 8: R3, R7, R10, R23, R26, R48, R148, R150 |
| Uncertain | 3: R6, R38, R128 |
| Excluded | 45 |
| **Recall vs the golden answer** | **4/4** — R10, R48, R148, R150 all found |
| Extra selected | 7 |
| Cost | 16 model calls, 146,009 in / 27,792 out, 338 s |

The seven extras are defensible rather than noise: R3 (retro-reflectors — the
spec says the module contains one), R23 (reversing lamps — the spec lists a
reversing function), R7 (the predecessor position/stop-lamp regulation), R26
(external projections), and as *uncertain* R6 (direction indicators), R38 (rear
fog) and R128 (LED light sources). Each costs precision only if it produces
requirements the answer does not contain; none is absurd, and keeping them is
the behaviour the plan asks for.

The product profile behind it produced 16 unknowns, including the ones that
decide scope: mounting position and installation geometry, nominal voltage,
whether the lamp is grouped/combined/reciprocally incorporated, whether the LED
module is replaceable, and all photometric values.

## Throughput: a serial full run does not fit the submission window

Workload for the 11 selected regulations:

| Regulation | Pages | Sections | Passages |
| --- | ---: | ---: | ---: |
| UN R48 | 122 | 260 | 1,300 |
| UN R10 | 148 | 248 | 1,050 |
| UN R150 | 54 | 84 | 585 |
| UN R148 | 48 | 76 | 453 |
| R3, R7, R23, R26, R38, R128, R6 | — | 223 | 1,704 |
| **Total** | | **891** | **5,092** |

At `passages_per_batch=8` that is **~1,201 extraction calls**, before reference
re-reads and revision.

| Configuration | Wall clock |
| --- | --- |
| Serial, ~20 s/call | **6.7 hours** |
| 4 parallel workers | 1.7 hours |
| 8 parallel workers | 0.8 hours |

**The submission window is 5 hours (08:00-13:00).** A serial run does not fit,
with no margin for a retry. Bounded parallel model calls are therefore required,
not an optimisation — this is the first measurement that settles it.

## Cost, which needs a decision from the credential owner

Extrapolating from measured per-call usage (~10k in / ~5k out for an extraction
call), ~1,201 calls is roughly **12M input / 6M output tokens**. On Opus 5
list pricing that is of the order of **$200** for one full run, on a teammate's
credits, and we would want more than one run.

Levers, cheapest first:

1. **Narrow the selection.** The four golden regulations alone are 668 sections,
   ~900 calls — maybe $160. Dropping the 7 extras trades recall for cost, and we
   do not know yet whether the extras contribute real requirements.
2. **A cheaper model for extraction** via `RunConfig.model_overrides`, which
   exists for exactly this. Sonnet 5 is roughly 40% of Opus 5 per token; Haiku
   4.5 roughly 20%. Review can stay on Opus 5.
3. **Lower `effort`** from `high` to `medium`.
4. **Larger batches** — `passages_per_batch=16` roughly halves the call count,
   at some risk to atomicity.

A sensible combination is Sonnet 5 for `extract_obligations` at
`passages_per_batch=12`, Opus 5 retained for the profile, applicability and
review. That is a judgement call about money and quality, so it belongs to the
team rather than to this component.

## What this does not tell us

Extraction quality at this scale is still unmeasured. Everything scored so far
is two sections of one regulation. The 190-requirement dev answer remains the
only real proxy for tomorrow, and running it is what items above are for.
