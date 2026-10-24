"""Part categories and what earlier runs learned about them (owner: Anushree).

A run first names the kind of vehicle part it is looking at - a rear
combination lamp, an exterior mirror, a seat-belt retractor. The category list
is not fixed: a part that matches no known category creates a new one, so the
list grows with the inputs the application sees.

Each category remembers, per corpus snapshot, the applicability decisions and
section-triage verdicts an earlier run produced. A later run of the same kind
of part reuses them instead of asking the model again, which removes the
slowest stages of a repeat run.

What is stored is the model's own earlier output, never a hand-written answer:
the first run of a category always decides from the regulation text, and the
registry only replays that decision. Deleting the registry file loses speed,
not correctness.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from certivity.contracts import (
    ApplicabilityDecision,
    DocumentRole,
    LLMCallError,
    LLMConfigurationError,
    ModelBudgetExceeded,
    ProductProfile,
    content_hash,
    slugify,
    to_jsonable,
)
from certivity.agent.context import RunContext
from certivity.agent.prompts import (
    PART_CATEGORY_INSTRUCTIONS,
    PART_CATEGORY_SCHEMA,
    PROMPT_VERSIONS,
)

logger = logging.getLogger(__name__)

REGISTRY_SCHEMA = 1

_FILE_LOCK = threading.Lock()
"""Serialises read-merge-write of the registry file within this process."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class PartCategory:
    slug: str
    label: str
    description: str
    is_new: bool
    reason: str = ""


class PartCategoryRegistry:
    """The category list plus per-snapshot knowledge, backed by one JSON file.

    ``path=None`` keeps everything in memory, which is what tests and a
    registry-less configuration get. A missing or unreadable file starts an
    empty registry rather than failing the run.
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path: Optional[Path] = Path(path) if path else None
        self._data: dict[str, Any] = self._read()
        self._lock = threading.Lock()
        self._dirty: set[str] = set()

    # -- reading ---------------------------------------------------------- #

    def _read(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"schema": REGISTRY_SCHEMA, "categories": {}}
        if self.path is None or not self.path.is_file():
            return empty
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("part category registry %s is unreadable; starting empty", self.path)
            return empty
        if not isinstance(data, dict) or not isinstance(data.get("categories"), dict):
            return empty
        return data

    def categories(self) -> list[dict[str, Any]]:
        """Known categories as the classifier sees them."""
        with self._lock:
            return [
                {
                    "slug": slug,
                    "label": entry.get("label", slug),
                    "description": entry.get("description", ""),
                    "aliases": list(entry.get("aliases") or ()),
                }
                for slug, entry in sorted(self._data["categories"].items())
            ]

    def has(self, slug: str) -> bool:
        with self._lock:
            return slug in self._data["categories"]

    def _snapshot(self, slug: str, snapshot_id: str) -> dict[str, Any]:
        entry = self._data["categories"].setdefault(slug, {})
        return entry.setdefault("snapshots", {}).setdefault(snapshot_id, {})

    def applicability(self, slug: str, snapshot_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            entry = self._data["categories"].get(slug) or {}
            stored = (entry.get("snapshots") or {}).get(snapshot_id, {}).get("applicability")
            return dict(stored) if stored else None

    def triage_verdicts(self, slug: str, snapshot_id: str) -> dict[str, dict[str, str]]:
        with self._lock:
            entry = self._data["categories"].get(slug) or {}
            stored = (entry.get("snapshots") or {}).get(snapshot_id, {}).get("triage") or {}
            return {k: dict(v) for k, v in stored.items()}

    # -- writing ---------------------------------------------------------- #

    def ensure(
        self, slug: str, label: str, description: str, aliases: Iterable[str] = ()
    ) -> bool:
        """Add the category when it is new. Returns True when it was created."""
        with self._lock:
            categories = self._data["categories"]
            created = slug not in categories
            entry = categories.setdefault(slug, {"first_seen": _now(), "runs": 0})
            entry.setdefault("label", label or slug)
            entry.setdefault("description", description or "")
            known = list(entry.get("aliases") or ())
            for alias in aliases:
                alias = str(alias).strip()
                if alias and alias not in known:
                    known.append(alias)
            entry["aliases"] = known
            entry["runs"] = int(entry.get("runs") or 0) + 1
            entry["last_used"] = _now()
            self._dirty.add(slug)
            return created

    def record_applicability(
        self,
        slug: str,
        snapshot_id: str,
        decisions: Iterable[ApplicabilityDecision],
        run_id: str,
    ) -> None:
        with self._lock:
            self._snapshot(slug, snapshot_id)["applicability"] = {
                "learned_from_run": run_id,
                "recorded_at": _now(),
                "decisions": to_jsonable(list(decisions)),
            }
            self._dirty.add(slug)

    def record_triage(
        self, slug: str, snapshot_id: str, verdicts: dict[str, dict[str, str]]
    ) -> None:
        if not verdicts:
            return
        with self._lock:
            self._snapshot(slug, snapshot_id).setdefault("triage", {}).update(verdicts)
            self._dirty.add(slug)

    def save(self) -> None:
        """Merge this run's changes into the file. A failure never fails the run.

        The file is re-read under a lock and only the categories this run
        touched are replaced, so two runs of different categories do not
        overwrite each other's knowledge.
        """
        if self.path is None:
            return
        with self._lock:
            dirty = {slug: self._data["categories"].get(slug) for slug in self._dirty}
            self._dirty.clear()
        if not dirty:
            return
        with _FILE_LOCK:
            current = self._read()
            for slug, entry in dirty.items():
                if entry is not None:
                    current["categories"][slug] = entry
            current["schema"] = REGISTRY_SCHEMA
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                tmp.write_text(
                    json.dumps(current, ensure_ascii=False, indent=1), encoding="utf-8"
                )
                os.replace(tmp, self.path)
            except OSError:
                logger.warning("could not save part category registry %s", self.path, exc_info=True)


def corpus_fingerprint(corpus) -> str:
    """Key for what a category learned: the set of base documents, not the snapshot.

    A snapshot ID changes whenever the corpus is rebuilt, even from identical
    PDFs, so keying by it lost everything at each server restart. Document IDs
    carry each PDF's checksum, and section IDs derive from them, so the same
    document set means the same section IDs and the stored verdicts still fit.
    """
    ids = sorted(d.document_id for d in corpus.list_documents(DocumentRole.BASE))
    return "docs-" + content_hash(*ids)[:16]


def compact_profile(profile: ProductProfile) -> dict[str, Any]:
    def pairs(group) -> list[str]:
        return [f"{f.key}: {f.value}" for f in group if f.value]

    return {
        "product_type": profile.product_type,
        "summary": profile.summary,
        "vehicle_categories": list(profile.vehicle_categories),
        "functions": pairs(profile.functions),
        "components": pairs(profile.components),
    }


def classify_part_category(
    ctx: RunContext, profile: ProductProfile, registry: PartCategoryRegistry
) -> Optional[PartCategory]:
    """Name the product's part category, adding it to the registry when new.

    Returns None when the call fails; the run then proceeds exactly as it
    would without categories, so this step can only save time.
    """
    known = registry.categories()
    try:
        response = ctx.call_json(
            "part_category",
            PART_CATEGORY_INSTRUCTIONS,
            {"product": compact_profile(profile), "known_categories": known},
            PART_CATEGORY_SCHEMA,
            prompt_version=PROMPT_VERSIONS["part_category"],
            max_output_tokens=2000,
        )
    except (LLMCallError, ModelBudgetExceeded, LLMConfigurationError) as exc:
        ctx.warn(
            f"the part category could not be determined ({exc}); nothing learned by "
            "earlier runs is reused",
            stage="part_category",
        )
        return None

    data = response.data
    slug = slugify(str(data.get("slug") or ""))
    if not slug:
        ctx.warn("the part category came back without a slug; ignored", stage="part_category")
        return None
    label = str(data.get("label") or slug).strip()
    description = str(data.get("description") or "").strip()

    if data.get("decision") == "existing" and not registry.has(slug):
        # The model named a category that does not exist. Treat it as new
        # rather than trusting an invented match.
        ctx.warn(
            f"part category {slug!r} was reported as known but is not in the registry; "
            "recorded as a new category",
            stage="part_category",
        )
    created = registry.ensure(slug, label, description, data.get("aliases") or ())
    category = PartCategory(
        slug=slug,
        label=label,
        description=description,
        is_new=created,
        reason=str(data.get("reason") or ""),
    )
    ctx.event(
        "part_category",
        f"Part category: {label} ({'new, added to the list' if created else 'known'})",
        affected_ids=[slug],
    )
    return category
