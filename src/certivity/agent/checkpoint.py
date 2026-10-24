"""Per-section checkpoints, written the moment a section is finished.

Why: a run that spent 90 minutes extracting 15 sections was interrupted and
kept nothing. The model answers survived in the call cache, but the drafts
built from them did not, so recovering the work meant replaying the whole
pipeline. Everything a finished section produced now lands on disk before the
next section starts, and a resumed run reads it back instead of re-deriving it.

Design constraints, in order of priority:

1. **An interruption must never corrupt what was already saved.** The file is
   append-only JSON Lines. A process killed mid-write can leave one truncated
   final line and nothing else; ``load`` skips unparseable lines and counts
   them, so a torn tail costs one section rather than the whole file.
2. **Resume must not duplicate.** Section ids are stable (corpus-derived) and
   draft ids are content-derived, so the same work produces the same ids. The
   loader keeps the last record per section id and de-duplicates drafts by
   draft id, which makes a replayed section idempotent rather than additive.
3. **A checkpoint must be exportable on its own.** Each record carries the
   full draft, including the passage id its citation resolves through, so a
   submission can be built from checkpoints without re-running extraction.

This file is written by several worker threads, so appends are serialised by a
lock and flushed with ``fsync``: without the flush, a killed process loses
whatever the OS had buffered, which is exactly the case this exists for.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from certivity.contracts import (
    CoverageStatus,
    content_hash,
    DraftRequirement,
    NumericValue,
    ObligationKind,
    to_jsonable,
)

logger = logging.getLogger(__name__)

SECTIONS_FILE = "sections.jsonl"
STATE_FILE = "state.json"
SCHEMA_VERSION = 1


@dataclass
class SectionRecord:
    """One finished unit of work, as persisted."""

    section_id: str
    status: str
    document_id: Optional[str] = None
    explanation: Optional[str] = None
    inspected_passage_ids: tuple[str, ...] = ()
    drafts: tuple[DraftRequirement, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def completed(self) -> bool:
        """True when this section needs no further work on resume.

        ``FAILED`` is deliberately not completed: a failure is usually a timeout
        or a transport error, and retrying it is the point of resuming.
        """
        return self.status in (CoverageStatus.PROCESSED.value, CoverageStatus.EXCLUDED.value)


@dataclass
class CheckpointLoad:
    """What was recovered, and what could not be."""

    records: dict[str, SectionRecord] = field(default_factory=dict)
    corrupt_lines: int = 0
    total_lines: int = 0

    @property
    def completed_ids(self) -> set[str]:
        return {sid for sid, r in self.records.items() if r.completed}

    def drafts(self) -> list[DraftRequirement]:
        """Every recovered draft, de-duplicated by draft id, in section order."""
        seen: set[str] = set()
        out: list[DraftRequirement] = []
        for section_id in sorted(self.records):
            for draft in self.records[section_id].drafts:
                if draft.draft_id in seen:
                    continue
                seen.add(draft.draft_id)
                out.append(draft)
        return out


class CheckpointStore:
    """Append-only section checkpoints under one directory.

    ``CheckpointStore(None)`` is a working no-op, so the pipeline does not need
    a branch for "checkpointing is off".
    """

    def __init__(self, directory: str | os.PathLike[str] | None) -> None:
        self.directory: Optional[Path] = Path(directory) if directory else None
        self._lock = threading.Lock()
        self._written: set[str] = set()
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    @property
    def path(self) -> Optional[Path]:
        return None if self.directory is None else self.directory / SECTIONS_FILE

    # -- writing ----------------------------------------------------------- #

    def record_section(
        self,
        section_id: str,
        *,
        status: CoverageStatus | str,
        document_id: Optional[str] = None,
        explanation: Optional[str] = None,
        inspected_passage_ids: Iterable[str] = (),
        drafts: Iterable[DraftRequirement] = (),
        notes: Iterable[str] = (),
    ) -> None:
        """Append one section's outcome. Never raises into the run."""
        if self.directory is None:
            return
        status_value = status.value if isinstance(status, CoverageStatus) else str(status)
        payload = {
            "v": SCHEMA_VERSION,
            "section_id": section_id,
            "status": status_value,
            "document_id": document_id,
            "explanation": explanation,
            "inspected_passage_ids": list(inspected_passage_ids),
            "notes": [str(n) for n in notes],
            "drafts": [to_jsonable(d) for d in drafts],
        }
        line = json.dumps(payload, ensure_ascii=False, default=str)
        assert self.path is not None
        try:
            with self._lock:
                # One line, one write, then fsync. The lock keeps two workers
                # from interleaving partial lines; the fsync is what makes the
                # record survive a kill -9 rather than sitting in a buffer.
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                self._written.add(section_id)
        except OSError:
            logger.warning("could not append checkpoint for %s", section_id, exc_info=True)

    def write_state(self, state: dict[str, Any]) -> None:
        """Atomically replace the run-level checkpoint summary."""
        if self.directory is None:
            return
        path = self.directory / STATE_FILE
        tmp = path.with_suffix(".json.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(to_jsonable(state), handle, ensure_ascii=False, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError:
            logger.warning("could not write checkpoint state", exc_info=True)
            Path(tmp).unlink(missing_ok=True)

    def read_state(self) -> dict[str, Any]:
        if self.directory is None:
            return {}
        path = self.directory / STATE_FILE
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    # -- reading ----------------------------------------------------------- #

    def load(self) -> CheckpointLoad:
        """Read every record back. A damaged line is skipped, not fatal."""
        result = CheckpointLoad()
        path = self.path
        if path is None or not path.is_file():
            return result
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("could not read checkpoints at %s", path, exc_info=True)
            return result
        for line in raw.splitlines():
            if not line.strip():
                continue
            result.total_lines += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                result.corrupt_lines += 1
                continue
            record = _record_from_payload(payload)
            if record is None:
                result.corrupt_lines += 1
                continue
            # Last writer wins: a retried section replaces its earlier outcome.
            result.records[record.section_id] = record
        return result


def _record_from_payload(payload: Any) -> Optional[SectionRecord]:
    if not isinstance(payload, dict):
        return None
    section_id = payload.get("section_id")
    status = payload.get("status")
    if not isinstance(section_id, str) or not isinstance(status, str):
        return None
    drafts: list[DraftRequirement] = []
    for item in payload.get("drafts") or ():
        draft = _draft_from_payload(item)
        if draft is not None:
            drafts.append(draft)
    return SectionRecord(
        section_id=section_id,
        status=status,
        document_id=payload.get("document_id"),
        explanation=payload.get("explanation"),
        inspected_passage_ids=tuple(payload.get("inspected_passage_ids") or ()),
        drafts=tuple(drafts),
        notes=tuple(str(n) for n in payload.get("notes") or ()),
    )


def _draft_from_payload(item: Any) -> Optional[DraftRequirement]:
    """Rebuild a draft, dropping anything that would not round-trip.

    A draft that cannot be reconstructed is skipped rather than repaired: a
    half-rebuilt requirement with a citation that no longer resolves is exactly
    the fabricated source this pipeline refuses to emit.
    """
    if not isinstance(item, dict):
        return None
    try:
        supporting = tuple(item.get("supporting_passage_ids") or ())
        primary = item.get("primary_passage_id")
        if primary and primary not in supporting:
            supporting = (primary, *supporting)
        return DraftRequirement(
            draft_id=item["draft_id"],
            text=item["text"],
            supporting_passage_ids=supporting,
            primary_passage_id=primary,
            obligation_kind=_enum(ObligationKind, item.get("obligation_kind")),
            applicability_condition=item.get("applicability_condition"),
            applicability_reason=item.get("applicability_reason"),
            exceptions=tuple(item.get("exceptions") or ()),
            values=tuple(filter(None, (_value(v) for v in (item.get("values") or ())))),
            document_id=item.get("document_id"),
            section_id=item.get("section_id"),
            prompt_version=item.get("prompt_version"),
            revision_of=item.get("revision_of"),
            notes=item.get("notes"),
        )
    except (KeyError, TypeError, ValueError):
        logger.debug("skipped an unreadable checkpointed draft", exc_info=True)
        return None


def _enum(enum_cls: Any, value: Any) -> Any:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except (ValueError, KeyError):
        return enum_cls.MANDATORY if enum_cls is ObligationKind else None


def _value(item: Any) -> Optional[NumericValue]:
    if not isinstance(item, dict):
        return None
    try:
        return NumericValue(
            quantity=item.get("quantity"),
            value=item.get("value"),
            unit=item.get("unit"),
            comparator=item.get("comparator"),
        )
    except (TypeError, ValueError):
        return None

# --------------------------------------------------------------------------- #
# Where checkpoints live
# --------------------------------------------------------------------------- #

CHECKPOINT_ENV = "CERTIVITY_CHECKPOINT_DIR"
DEFAULT_CHECKPOINT_BASE = "data/cache/checkpoints"


def checkpoint_key(run_input: Any, prompt_versions: Any = None) -> str:
    """A directory name that identifies the work, not the run.

    Keyed on the case and the hash of its input, so a second attempt at the
    same case resumes the first attempt rather than starting over. Keying on
    ``run_id`` would make every retry a cold start, which is the failure this
    whole module exists to prevent.

    Two things beyond the input are in the key because they change what a
    section id means or what reading it produces:

    * the corpus snapshot, because section ids are snapshot-derived and
      checkpoints from another snapshot would name sections that no longer
      exist;
    * the prompt versions, because resuming past a prompt change would keep
      obligations the current prompt would not have produced, and silently
      pin the run to the old wording.
    """
    case_id = getattr(run_input, "case_id", None) or "case"
    input_hash = str(getattr(run_input, "input_hash", "") or "")[:12]
    snapshot = str(getattr(run_input, "corpus_snapshot_id", "") or "")[:16]
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in f"{case_id}")
    parts = [safe]
    if snapshot:
        parts.append(snapshot)
    if input_hash:
        parts.append(input_hash)
    if prompt_versions:
        parts.append(content_hash(prompt_versions)[:8])
    return "-".join(parts)


def checkpoint_dir_for(
    run_input: Any,
    explicit: str | os.PathLike[str] | None = None,
    artifacts_dir: str | os.PathLike[str] | None = None,
    prompt_versions: Any = None,
) -> Optional[Path]:
    """Resolve the checkpoint directory for one run, or None for no checkpoints.

    Precedence: an explicit argument, then ``CERTIVITY_CHECKPOINT_DIR`` as a
    base that the work key is appended to, then the shared default base. Set
    ``CERTIVITY_CHECKPOINT_DIR=off`` to confine checkpoints to the run's own
    folder, which still protects against an interruption inside the run but
    does not let a later run resume it.

    Without an ``artifacts_dir`` and without an explicit directory this returns
    None. The agent component writes only where the application told it to, and
    a caller that supplied nowhere to write - a unit test, an in-process
    embedding - has not asked for a run to be checkpointed. Inventing a shared
    location for those callers made separate runs over the same synthetic input
    resume each other, which is a correctness problem, not just untidiness.
    """
    if explicit is not None:
        return Path(explicit)
    if artifacts_dir is None:
        return None
    configured = os.environ.get(CHECKPOINT_ENV, "").strip()
    if configured.lower() in ("off", "none", "0", "false"):
        return Path(artifacts_dir) / "checkpoint"
    base = Path(configured) if configured else Path(DEFAULT_CHECKPOINT_BASE)
    if not base.is_absolute():
        base = _repo_root() / base
    return base / checkpoint_key(run_input, prompt_versions)


def _repo_root() -> Path:
    """The repository root, so a relative default does not depend on the cwd."""
    return Path(__file__).resolve().parents[3]
