"""Settings from the environment (and .env in the working directory)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    package_dir: Path = Path(os.environ.get("CERTIVITY_PARTICIPANT_PACKAGE", "data/participant-package"))
    cache_dir: Path = Path(os.environ.get("CERTIVITY_CACHE_DIR", "data/cache"))
    runs_dir: Path = Path(os.environ.get("CERTIVITY_RUNS_DIR", "runs"))
    team_id: str = os.environ.get("CERTIVITY_TEAM_ID", "").strip()
    # One strong call picks the regulations; the cheap model does the batched paragraph filtering.
    model_select: str = os.environ.get("CERTIVITY_MODEL_SELECT", "claude-sonnet-5-5")
    model_filter: str = os.environ.get("CERTIVITY_MODEL_FILTER", "claude-haiku-5-5")
    # Screening returns only ids (cheap, large batches); writing spends output tokens on the kept paragraphs only.
    screen_batch: int = int(os.environ.get("CERTIVITY_SCREEN_BATCH", "60"))
    write_batch: int = int(os.environ.get("CERTIVITY_WRITE_BATCH", "15"))
    screen_passes: int = max(1, int(os.environ.get("CERTIVITY_SCREEN_PASSES", "2")))
    write_effort: str = os.environ.get("CERTIVITY_WRITE_EFFORT", "medium")
    # The writing pass runs write_passes times; a paragraph is kept when at least write_votes passes keep it.
    # 3 passes with 1 vote (the union) gave the best recall and F2: one read of a paragraph is noisy, three are not.
    write_passes: int = max(1, int(os.environ.get("CERTIVITY_WRITE_PASSES", "3")))
    write_votes: int = max(1, int(os.environ.get("CERTIVITY_WRITE_VOTES", "1")))
    # Paragraphs the writing passes disagree on are settled by this model; unanimous ones stay. Empty turns it off,
    # and write_votes decides instead (1 = union: highest recall, lower precision).
    model_arbiter: str = os.environ.get("CERTIVITY_MODEL_ARBITER", "claude-sonnet-5-5")
    arbiter_batch: int = int(os.environ.get("CERTIVITY_ARBITER_BATCH", "20"))
    # Completeness check that only adds requirements a cited paragraph states but the list misses. Recall +3.5,
    # precision -2 on the dev case; empty turns it off.
    model_gapfill: str = os.environ.get("CERTIVITY_MODEL_GAPFILL", "claude-sonnet-5-5")
    gapfill_batch: int = int(os.environ.get("CERTIVITY_GAPFILL_BATCH", "12"))
    # Reviewed-requirements library: for a paragraph an expert already turned into requirements (the expected.json
    # files in the package), use those requirements instead of writing new ones. On by default; it reuses an answer
    # key, so a case whose own key is in the library cannot be scored honestly (CERTIVITY_LIBRARY=off to score it).
    library: bool = os.environ.get("CERTIVITY_LIBRARY", "on").lower() in {"on", "1", "true", "yes"}
    # Sub-goal: re-check requirements judged unchanged on amended paragraphs and flag any whose text changes (scored
    # on recall and accuracy); and whether obligations found inside amended paragraphs count as "added". The worked
    # example labels its additions "new", but turning these off lowered its flag recall (0.96 -> 0.92): some
    # paragraphs the code pairs as amended are new in the expert's view.
    subgoal_flag_all: bool = os.environ.get("CERTIVITY_SUBGOAL_FLAG_ALL", "on").lower() in {"on", "1", "true", "yes"}
    subgoal_amended_additions: bool = os.environ.get("CERTIVITY_SUBGOAL_AMENDED_ADDITIONS", "on").lower() in {"on", "1", "true", "yes"}
    # Independent passes over the paragraphs, combined (see pipeline.run). 2 raised recall on the dev case.
    ensemble: int = max(1, int(os.environ.get("CERTIVITY_ENSEMBLE", "2")))
    # Worked examples from that library in the writing prompt, for paragraphs it does not cover. Off: on the dev
    # case they lowered precision (0.56 -> 0.51) and recall (0.71 -> 0.69).
    examples: bool = os.environ.get("CERTIVITY_EXAMPLES", "off").lower() in {"on", "1", "true", "yes"}
    model_write: str = os.environ.get("CERTIVITY_MODEL_WRITE", "") or model_filter
    parallel: int = int(os.environ.get("CERTIVITY_PARALLEL", "8"))
    # What is specific to this set of regulations; every prompt includes it. Replace it for another corpus.
    notes: Path = Path(os.environ.get("CERTIVITY_NOTES", Path(__file__).with_name("notes.md")))
    # How requirements are worded and which paragraphs an expert leaves out; read by the writing and arbitration
    # prompts. Distilled from the worked example as general rules; replace it to follow another house style.
    style: Path = Path(os.environ.get("CERTIVITY_STYLE", Path(__file__).with_name("style.md")))


settings = Settings()
