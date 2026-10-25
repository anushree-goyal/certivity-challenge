# certivity-challenge

Turns a product specification and/or exploded-view drawing into the engineering requirements it must fulfil under UN Regulations. Each requirement cites the base-version page and paragraph. The output is the hackathon's submission JSON, checked with the official validator.

## What a good result is

From the challenge brief: pick the regulations that apply to the product, write **one clear obligation per requirement** in terms an engineer can act on (such as a measurable limit), and cite a version and page someone can open and check. **Recall matters somewhat more than precision**: a missed requirement can mean a non-compliant product, while an extra one only costs reading time. But the list must not be padded to be safe.

The pipeline is tuned to that balance and measured, not guessed: a change is kept only if recall rises without precision falling, scored with a local judge that matches requirements one to one as the platform does (it reproduced the platform's score within about one point). On the dev case the platform scores this version at precision 56.2%, recall 71.6%, F1 63.0%, F2 67.9%. Variants that asked the model to keep borderline paragraphs, or to look only for measurable limits, were tested and lowered recall or precision, so they are not used.

## How it works

Code does the reading and the model makes the judgements, so a run is fast and cheap: about 20 calls on Sonnet and 150 on Haiku, about 3 minutes, about $0.55 at list prices (the CLI prints the estimate). Nothing is specific to one kind of part.

1. **Corpus (code only).** Each base PDF in the participant package is split into numbered paragraphs (`6.9.1`, `Annex 9 3.1`, `Annex 6 Part 1 3`). Each paragraph keeps its page and section path. The result is cached in `data/cache/paragraphs/`.
2. **Regulations (1 call, Sonnet).** The spec, the drawings and the list of regulations in `index.json` go to the model. It describes the product and marks each regulation it considers as applying or not.
3. **Scope (1 call, Sonnet).** The model reads only the chosen regulations' tables of contents and drops whole sections that concern something else. For a steering wheel that means the automated-steering and trailer sections of R79; for a rear lamp, the sections about other lamps. It also writes a short focus note per regulation.
4. **Screen (batched calls, Haiku).** The remaining paragraphs that state an obligation (shall, must, may only, limits…) go to the model 60 at a time, together with the product description and the regulation's focus note. It returns only the ids of paragraphs that may apply, and keeps a paragraph when in doubt. This runs twice with the batches cut at different points, and a paragraph either pass keeps goes on. The output is just ids, so both passes together cost about a cent.
5. **Write (batched calls, Haiku with more thinking).** Only the kept paragraphs are read closely, 15 at a time. The model drops the ones that clearly don't apply (other variants, permissions that constrain nothing, steps of a test method) and writes one requirement per obligation, list item, limit value or table row. Test procedures contribute only test levels, conditions and pass criteria, not laboratory steps, and introductory paragraphs give way to their sub-paragraphs. The same sentence cited from two paragraphs is kept once. The writing pass runs three times with the batches cut at different points. Paragraphs all three passes keep stay. The few they disagree on go to Sonnet, which settles each one (one model read of a paragraph is noisy; this spends the strong model only where the cheap one is unsure). On the dev case this gave the best precision and recall together. `CERTIVITY_MODEL_ARBITER=` (empty) switches arbitration off; then `CERTIVITY_WRITE_VOTES=1` keeps any paragraph one pass kept (highest recall). A requirement can only cite a paragraph the parser found, so the page and paragraph always come from the PDF.
6. **Completeness (a few calls, Sonnet).** For every cited paragraph, Sonnet sees the requirements already written from it and adds only obligations the paragraph states that they miss, for this product's functions only. It never changes or removes a requirement. On the dev case it raised recall about 3.5 points and F2 about 2 for about 2 points of precision; `CERTIVITY_MODEL_GAPFILL=` (empty) turns it off.
7. **Checks.** The participant package's `tools/validate.py` runs on every result. On the dev case, `tools/score_local.py` also gives regulation and page scores.

Every model answer is cached in `data/cache/llm/` by request content, so running the same input again costs nothing.

**House style.** How requirements are worded, and which paragraphs an expert leaves out, is in [src/certivity/style.md](src/certivity/style.md), which the writing and arbitration prompts include. It was distilled from the worked example by having a model compare each regulation paragraph with the requirements the expert wrote from it, and it holds general rules only (no product, regulation number, paragraph or value). The most important: keep the regulation's own subject and full scope instead of tailoring it to the product, keep exceptions inside the requirement they modify, and split per row or case, not per value. Replace it (or set `CERTIVITY_STYLE`) to follow another house style.

**Another set of obligations.** Everything specific to UN Regulations lives in [src/certivity/notes.md](src/certivity/notes.md), which every prompt includes: how vehicle and component regulations relate, which lighting regulations replaced which, when EMC applies. For a different corpus, write a new notes file and set `CERTIVITY_NOTES`. The parser expects numbered paragraphs (`5.1.2.`) and `Annex N` headings. A corpus laid out differently needs a change to the regular expressions at the top of `corpus.py`.

## Setup

Requirements: Python 3.12+, plus Node 20+ to build the UI.

```bash
git clone https://github.com/Certivity/industrial-ai-hackathon data/participant-package
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env              # set CERTIVITY_TEAM_ID and ANTHROPIC_API_KEY
.venv/bin/certivity fetch         # download the base regulation PDFs
(cd web && npm install && npm run build)
.venv/bin/certivity serve         # http://127.0.0.1:8000 (API + UI)
```

Headless run of one case: `.venv/bin/certivity run --case dev-rear-lamp`. It writes `<case>_<team>.json` and prints validation, the local score and the model calls it made.

Frontend development with hot reload: run `certivity serve` and `cd web && npm run dev` together, then open http://localhost:5173 (it proxies `/api`).

Tests: `.venv/bin/python -m pytest`. A scripted model stands in for Claude, so the tests make no API calls.

## Code

| File | What it does |
| --- | --- |
| `src/certivity/corpus.py` | Reads the index and parses PDFs into paragraphs; renders pages |
| `src/certivity/pipeline.py` | The steps above, plus their prompts |
| `src/certivity/notes.md` | What is specific to this regulation corpus; included in every prompt |
| `src/certivity/llm.py` | The one model call (structured JSON output), with the disk cache |
| `src/certivity/checks.py` | The participant package's validator and local scorer |
| `src/certivity/runs.py` | Runs on disk (`runs/<id>/`) and the background worker |
| `src/certivity/api.py` | HTTP API for the UI, plus the built UI |
| `src/certivity/cli.py` | `serve`, `run`, `fetch` |
| `web/` | React UI |

## Held-out case

1. `git -C data/participant-package pull`, then `.venv/bin/certivity fetch`.
2. Start a run with the new case from the dropdown (or use `certivity run --case <folder>`).
3. Download the validated JSON and upload it. For the held-out case the **last valid upload counts**.
