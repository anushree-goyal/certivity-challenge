# Pre-review of feature/corpus → main

**Reviewer:** Anushree · **Reviewed at:** `df6c058` · **Date:** 2026-10-24

Reviewed before the PR was opened, by merging `origin/feature/corpus` into
current `main` locally, rebuilding the snapshot and re-running Checkpoint 2.

## Verdict: approve

Merge is clean, no conflicts. **155 tests pass on the merged tree** (116 agent +
39 corpus). Every claim in the hand-off checks out.

### Verified independently

| Claim | Result |
| --- | --- |
| Superscripts explicit | ✅ 28 R148 passages carry `10^n`; **0 flattened** remain. `cm^2` present. |
| `index_entry` populated | ✅ `regulation, title, source_url, versions`, stored in the snapshot |
| `Section.ordinal` set | ✅ 76 distinct ordinals in R148 (was 0 everywhere) |
| `target_namespace` | ✅ no `AttributeError`; references resolve with my `ReferenceRequest` |
| Canonical namespaces | ✅ `annex:2/part:a`, `annex:2/part:b` — 1.3.0 grammar adopted |
| `CorpusReader` Protocol | ✅ `isinstance(corpus, CorpusReader)` is true |
| Snapshot build | ✅ 56 docs, 3,029 pages, 31,878 passages, 107 s, zero parse failures |

### The superscript fix is confirmed end to end

Checkpoint 2 re-run on the new snapshot, same two R148 sections:

| Before | After |
| --- | --- |
| `maximum luminous intensity of 4.2∙101 cd` | `4.2∙10^1 cd` (42 cd) ✅ |
| `minimum luminous intensity of 5∙10-2 cd` | `5∙10^-2 cd` (0.05 cd) ✅ |

Extraction went from 12 reference failures and 12 warnings to **zero warnings**,
and from 5 model calls to 9 — the increase is reference targets now resolving,
so sections are re-read with real incorporated context. Draft output nearly
doubled (105 KB → 192 KB). Catching the `No. 45 + footnote 1 → No. 451` and
`Annex 4 + 2 → Annex 42` corruption as part of the same fix was a good find.

## Two gaps to consider before merging (neither blocking)

### 1. A trailing sub-item stops a reference resolving — one-line fix

`Par. 4.8.3.1. (a)` through `(d)` come back `missing`, 12 occurrences in two
sections. The target exists; only the parenthetical blocks it:

```
target_paragraph="4.8.3.1 (a)"  -> missing   (0 targets)
target_paragraph="4.8.3.1"      -> resolved  (1 target)
```

Suggestion: on a miss, strip a trailing `(x)` / `(a)` sub-item and retry,
keeping the sub-item in `explanation` so the caller knows which part was meant.

### 2. Table and figure references are not addressable

`Table 4`, `Table A2-1` and `Figure A3-I` all come back `missing`. The targets
are in the corpus — `Table 4` is a passage under 5.2.1, `Table A2-1` under
`annex:2/part:a` — but they are addressed by caption, not by paragraph label, so
a paragraph resolver cannot reach them.

This one has content consequences rather than cosmetic ones: R148's photometric
limits live in Tables 3 and 4, so an obligation reading *"shall comply with the
values in Table 4"* is incomplete while the table cannot be resolved. Indexing
caption text (`Table <n>`, `Figure <id>`) as a resolvable target would close it.

## Housekeeping

Your nested `data/cache/corpus/.gitignore` correctly keeps the 51 MB parse cache
and snapshots out of git — `git add .` on the merged tree stages 2 paths, none
of them cache. Thank you for that.

`runs/` is still untracked and unignored. It is Teammate 4's directory, so it
belongs in their root `.gitignore`, not yours.

## Note for whoever merges

`feature/corpus` and `feature/quality` are both unmerged, and the quality
component is several contract versions behind. Merging corpus first is the right
order: the quality component depends on real source records, and this branch
provides them.
