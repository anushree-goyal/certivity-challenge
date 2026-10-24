"""Reference following (owner: Anushree).

The extraction call reports the cross references it saw and which of them it
needs in order to state an obligation correctly. This module decides what to
chase, resolves it through ``CorpusReader.resolve_reference`` and returns the
target passages as extra context.

Three rules hold the whole thing together:

* A visited set keyed on the reference target, so a cycle
  (``A -> B -> A``) terminates instead of looping.
* A depth bound, so "see Annex 4" does not walk the entire corpus.
* A reference is incorporation for a stated purpose, never proof that the whole
  referenced regulation applies. This module therefore returns passages as
  context and never creates an applicability decision.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Iterable

from certivity.contracts import (
    ReferenceRequest,
    ReferenceResolution,
    ReferenceStatus,
    SourcePassage,
)
from certivity.agent.context import RunContext

MAX_CONTEXT_PASSAGES_PER_REFERENCE = 8
"""Bounded context expansion: a resolved target contributes at most this many
passages, so one broad reference cannot crowd out the section being read."""


@dataclass
class ReferenceTracker:
    """Per-run record of which reference targets have already been resolved."""

    visited: dict[str, ReferenceResolution] = field(default_factory=dict)
    unresolved: list[ReferenceResolution] = field(default_factory=list)

    def seen(self, request: ReferenceRequest) -> bool:
        return request.key() in self.visited

    def record(self, resolution: ReferenceResolution) -> None:
        self.visited[resolution.request.key()] = resolution
        if resolution.status is not ReferenceStatus.RESOLVED:
            self.unresolved.append(resolution)

    def get(self, request: ReferenceRequest) -> ReferenceResolution | None:
        return self.visited.get(request.key())


def _with_request(
    resolution: ReferenceResolution, request: ReferenceRequest
) -> ReferenceResolution:
    """Ensure the resolution carries the request it answers.

    A corpus implementation does not have to echo the request back, so this
    attaches it from what we sent. Everything downstream - the visited set, the
    per-draft unresolved list - keys on it, so it must always be present by the
    time a resolution leaves this module.
    """
    # isinstance, not "is not None": a positional construction in a corpus
    # implementation can put something other than a ReferenceRequest here, and
    # the visited set would then crash on request.key(). Replacing a wrong value
    # with the request we actually sent is always correct.
    if isinstance(resolution.request, ReferenceRequest):
        return resolution
    return dataclasses.replace(resolution, request=request)


def requests_from_extraction(
    entries: Iterable[dict[str, Any]],
    *,
    document_id: str,
    valid_passage_ids: set[str],
    ctx: RunContext,
) -> list[ReferenceRequest]:
    """Build requests from the model's reference list, keeping only needed ones.

    A reference whose origin passage was not part of the supplied batch is
    dropped: it cannot be attributed to text the model was actually shown.
    """
    requests: list[ReferenceRequest] = []
    for entry in entries or ():
        if not entry.get("needed"):
            continue
        origin = str(entry.get("originating_passage_id") or "")
        if origin not in valid_passage_ids:
            ctx.warn(
                f"a cross reference was attributed to passage {origin!r}, which was not "
                "in the supplied batch; the reference was dropped",
                stage="references",
            )
            continue
        raw = str(entry.get("raw_reference") or "").strip()
        if not raw:
            continue
        requests.append(
            ReferenceRequest(
                originating_passage_id=origin,
                raw_reference=raw,
                target_regulation=entry.get("target_regulation"),
                target_namespace=entry.get("target_namespace"),
                target_paragraph=entry.get("target_paragraph"),
                origin_document_id=document_id,
                reason=entry.get("reason"),
            )
        )
    return requests


def follow_references(
    ctx: RunContext,
    requests: Iterable[ReferenceRequest],
    tracker: ReferenceTracker,
    *,
    depth: int,
) -> tuple[list[SourcePassage], list[ReferenceResolution]]:
    """Resolve the requests and return (context passages, resolutions).

    Unresolved targets are returned too. An obligation that depends on a
    reference nobody could resolve must say so; that is more useful than an
    obligation that reads as complete but is not.
    """
    context: list[SourcePassage] = []
    resolutions: list[ReferenceResolution] = []
    seen_passage_ids: set[str] = set()

    if depth > ctx.config.max_reference_depth:
        return context, resolutions

    for request in requests:
        cached = tracker.get(request)
        if cached is not None:
            resolutions.append(cached)
            if cached.is_usable:
                context.extend(
                    _fetch(ctx, cached.candidate_passage_ids, seen_passage_ids)
                )
            continue

        try:
            resolution = ctx.corpus.resolve_reference(request)
        except Exception as exc:  # a corpus failure must not end the run
            resolution = ReferenceResolution(
                request=request,
                status=ReferenceStatus.MISSING,
                explanation=f"the corpus raised while resolving this reference: {exc}",
            )
            ctx.warn(
                f"reference {request.raw_reference!r} could not be resolved: {exc}",
                stage="references",
            )

        resolution = _with_request(resolution, request)
        tracker.record(resolution)
        resolutions.append(resolution)

        if resolution.status is ReferenceStatus.AMBIGUOUS:
            ctx.event(
                "references",
                f"{request.raw_reference!r} is ambiguous: "
                f"{len(resolution.candidate_passage_ids)} candidate target(s)",
                affected_ids=resolution.candidate_passage_ids[:5],
                level="warning",
            )
            continue
        if not resolution.is_usable:
            ctx.event(
                "references",
                f"{request.raw_reference!r}: {resolution.status.value}",
                level="warning",
            )
            continue

        fetched = _fetch(ctx, resolution.candidate_passage_ids, seen_passage_ids)
        context.extend(fetched)
        ctx.event(
            "references",
            f"{request.raw_reference!r} resolved to {len(fetched)} passage(s)",
            affected_ids=[p.passage_id for p in fetched][:5],
        )

    return context, resolutions


def _fetch(
    ctx: RunContext, passage_ids: Iterable[str], seen: set[str]
) -> list[SourcePassage]:
    wanted = [pid for pid in passage_ids if pid not in seen][
        :MAX_CONTEXT_PASSAGES_PER_REFERENCE
    ]
    if not wanted:
        return []
    passages = ctx.corpus.get_passages(wanted)
    missing = set(wanted) - {p.passage_id for p in passages}
    if missing:
        ctx.warn(
            f"the corpus resolved a reference to {len(missing)} passage id(s) it then "
            "could not return",
            stage="references",
        )
    for passage in passages:
        seen.add(passage.passage_id)
    return list(passages)
