"""Run orchestration: call the components in order, persist each artifact, build the RunView.

This is application composition only. Applicability, source selection, obligation wording,
deduplication and export policy live in the agent and quality modules.
"""

from __future__ import annotations

import json
import time
import traceback
from typing import Any

from certivity.api.components import (
    ComponentError, call_accepting, describe_llm, llm_configured, load_components, snapshot_id_of,
)
from certivity.api.serialization import first, to_jsonable
from certivity.api.settings import Settings
from certivity.run_store import RunState, RunStore
from certivity.run_store.store import now_iso


class EventSink:
    """The emit_event callback handed to modules. Accepts a RunEvent record, a dict, or keywords.

    It never raises into a module (the agent swallows callback errors anyway), so cancellation is
    checked by the app at stage boundaries instead.
    """

    def __init__(self, store: RunStore, run_id: str):
        self.store, self.run_id = store, run_id
        self.stage = "starting"

    def set_stage(self, stage: str, message: str) -> None:
        self.check_cancelled()
        self.stage = stage
        self.store.update(self.run_id, stage=stage)
        self({"stage": stage, "message": message})

    def check_cancelled(self) -> None:
        if self.store.read(self.run_id).get("cancel_requested"):
            raise RunCancelled()

    def __call__(self, event: Any = None, **fields: Any) -> None:
        data = to_jsonable(event) if event is not None else {}
        if not isinstance(data, dict):
            data = {"message": str(data)}
        data.update(to_jsonable(fields))
        data.setdefault("stage", self.stage)
        data.setdefault("timestamp", now_iso())
        self.store.append_event(self.run_id, data)
        progress = {k: data.get(k) for k in ("stage", "message", "completed", "total")}
        self.store.update(self.run_id, progress=progress)


class RunCancelled(Exception):
    """Raised at the next stage boundary after a user asks to cancel."""


REVISION_REQUEST_FIELDS = ("requested_context", "requested_correction")


def _needs_revision(report: dict) -> list[int]:
    """Indices of findings asking extraction for more context or a correction."""
    if report.get("needs_revision") is False:
        return []
    return [i for i, f in enumerate(report.get("findings") or [])
            if isinstance(f, dict) and any(f.get(k) for k in REVISION_REQUEST_FIELDS)]


def review_incomplete(report: dict) -> bool:
    """The quality module marks a partial review (no credential, call budget spent) as a review_incomplete issue."""
    return "review_incomplete" in json.dumps(report.get("remaining_issues") or [])


def _part(result: Any, name: str) -> Any:
    return result.get(name) if isinstance(result, dict) else getattr(result, name, None)


def _findings_of(report_obj: Any, indices: list[int]) -> list:
    """The module's own finding records (not their JSON copies), for revise_extraction."""
    findings = report_obj.get("findings") if isinstance(report_obj, dict) else getattr(report_obj, "findings", None)
    return [list(findings)[i] for i in indices] if findings else []


def _passage_ids(draft: dict, report: dict) -> list[str]:
    ids: list[str] = []
    for d in draft.get("draft_requirements") or []:
        ids += d.get("supporting_passage_ids") or []
        if d.get("primary_passage_id"):
            ids.append(d["primary_passage_id"])
    for f in report.get("findings") or []:
        ids += f.get("supporting_passage_ids") or []
    for a in draft.get("applicability_decisions") or []:
        ids += [x for x in a.get("scope_evidence") or [] if isinstance(x, str)]
    return list(dict.fromkeys(ids))


def execute_run(run_id: str, store: RunStore, settings: Settings) -> None:
    """Worker entry point. Never raises: failures are recorded on the run."""
    manifest = store.read(run_id)
    if manifest["state"] != RunState.QUEUED.value:
        return  # cancelled while queued, or already handled
    started = time.monotonic()
    deadline = started + settings.run_time_budget_s
    store.update(run_id, state=RunState.RUNNING.value, started_at=now_iso(), stage="starting")
    emit = EventSink(store, run_id)
    warnings: list[str] = []
    try:
        emit.set_stage("loading", f"Loading {manifest['mode']} components")
        comps = load_components(settings, manifest["mode"])
        corpus = comps.open_corpus()  # opened in the worker thread, used for the whole run
        snapshot_id = snapshot_id_of(corpus)
        if snapshot_id is None:
            warnings.append("Corpus did not report a snapshot_id.")
        run_dir = store.run_dir(run_id)
        fields = {
            "run_id": run_id,
            "team_id": manifest["team_id"],
            "case_id": manifest["case_id"],
            "specification_text": (run_dir / "input" / manifest["spec_file"]).read_text(encoding="utf-8") if manifest.get("spec_file") else None,
            "drawing_paths": [str(run_dir / "input" / n) for n in manifest.get("drawing_files", [])],
            "input_hash": manifest["input_hash"],
            "corpus_snapshot_id": snapshot_id,
        }
        if settings.max_model_calls is not None and comps.make_run_config is not None:
            fields["config"] = comps.make_run_config(max_model_calls=settings.max_model_calls)
        run_input = comps.make_run_input(fields)

        # One adapter for extraction and review, bounded by the run's own limits: the call budget is enforced
        # by the adapter, so it must be passed here or the run has no cap.
        config = run_input.get("config") if isinstance(run_input, dict) else getattr(run_input, "config", None)
        limits = {k: v for k, v in {
            "max_calls": getattr(config, "max_model_calls", settings.max_model_calls),
            "timeout_s": getattr(config, "request_timeout_s", None),
            "max_retries": getattr(config, "max_retries", None),
        }.items() if v is not None}
        agent_dir = store.module_dir(run_id, "agent")
        llm = comps.create_llm(agent_dir, **limits)
        llm_description = describe_llm(llm)
        store.update(run_id, corpus_snapshot_id=snapshot_id, llm_identity=llm_description.get("model") or "",
                     llm_description=llm_description, model_call_budget=limits.get("max_calls"))
        if not llm_configured(llm_description):
            # The adapter refuses calls without credentials; fail now with the reason, before any work.
            raise ComponentError("No model credentials are configured, so no requirements can be extracted. "
                                 "Set the model environment variables (docs/agent/dependencies.md) and restart.")

        emit.set_stage("extraction", "Extracting requirements")
        # Modules exchange their own record objects; the app keeps JSON copies for storage and the view.
        draft_obj = call_accepting(comps.run_extraction, run_input, corpus, llm, emit, artifacts_dir=agent_dir)
        draft = to_jsonable(draft_obj)
        store.write_artifact(run_id, "pipeline_draft.json", draft)

        emit.set_stage("review", "Reviewing drafts")
        report_obj = comps.review_requirements(run_input, draft_obj, corpus, llm, emit)
        report = to_jsonable(report_obj)
        store.write_artifact(run_id, "quality_report.json", report)

        rounds = 0
        while (pending := _needs_revision(report)):
            if rounds >= settings.max_revision_rounds or comps.revise_extraction is None or time.monotonic() > deadline:
                reason = ("revision is not available" if comps.revise_extraction is None
                          else "the time budget ran out" if time.monotonic() > deadline
                          else f"the {settings.max_revision_rounds}-round revision limit was reached")
                warnings.append(f"{len(pending)} review finding(s) still request revision; stopped because {reason}.")
                break
            rounds += 1
            emit.set_stage("revision", f"Revision round {rounds}: {len(pending)} finding(s)")
            draft_obj = call_accepting(comps.revise_extraction, run_input, draft_obj, _findings_of(report_obj, pending),
                                       corpus, llm, emit, artifacts_dir=agent_dir)
            draft = to_jsonable(draft_obj)
            store.write_artifact(run_id, f"pipeline_draft.round{rounds}.json", draft)
            emit.set_stage("review", f"Re-reviewing after round {rounds}")
            report_obj = comps.review_requirements(run_input, draft_obj, corpus, llm, emit)
            report = to_jsonable(report_obj)
            store.write_artifact(run_id, f"quality_report.round{rounds}.json", report)
        store.write_artifact(run_id, "pipeline_draft.json", draft)
        store.write_artifact(run_id, "quality_report.json", report)

        emit.set_stage("export", "Building and validating submission")
        if comps.build_submission_detailed is not None:
            result = comps.build_submission_detailed(run_input, draft_obj, report_obj, corpus)
            submission = to_jsonable(_part(result, "submission"))
            export = {k: to_jsonable(_part(result, k)) for k in ("export_mapping", "excluded", "blocking_issues")}
        else:
            submission = to_jsonable(comps.build_submission(run_input, draft_obj, report_obj, corpus))
            export = {}
        store.write_artifact(run_id, "export.json", export)
        emit.check_cancelled()
        validation = to_jsonable(comps.validate_submission(submission, settings.participant_package))
        valid = bool(first(validation, "valid", "is_valid", default=False))
        if valid:
            # The exact bytes served for download.
            (store.module_dir(run_id, "app") / "submission.json").write_text(
                json.dumps(submission, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            store.write_artifact(run_id, "submission.invalid.json", submission)
        store.write_artifact(run_id, "validation.json", validation)

        emit.set_stage("evidence", "Collecting source evidence")
        passages = to_jsonable(corpus.get_passages(_passage_ids(draft, report)))
        documents = to_jsonable(corpus.list_documents(role="base"))
        store.write_artifact(run_id, "evidence.json", {"passages": passages, "documents": documents})

        # Local scoring is the evaluation module's job end to end: it alone locates and reads the golden answer.
        # The app passes only the case id and returns None when that case has no answer to score against.
        if valid and comps.evaluate_case:
            emit.set_stage("evaluation", "Local regulation/page scoring (evaluation module)")
            try:
                evaluation = to_jsonable(comps.evaluate_case(
                    store.module_dir(run_id, "app") / "submission.json", manifest["case_id"], settings.participant_package))
                if evaluation is not None:
                    store.write_artifact(run_id, "evaluation.json", evaluation)
            except Exception as exc:
                warnings.append(f"Local evaluation failed: {type(exc).__name__}: {exc}")

        coverage_open = [c for c in draft.get("coverage") or [] if c.get("status") in ("pending", "failed")]
        if coverage_open:
            warnings.append(f"Coverage is partial: {len(coverage_open)} planned section(s) pending or failed.")
        remaining = report.get("remaining_issues") or []
        if remaining:
            warnings.append(f"Review left {len(remaining)} unresolved issue(s).")
        if review_incomplete(report):
            warnings.append("Review was incomplete (review_incomplete): requirements are not verified.")
        blocking = export.get("blocking_issues") or []
        if blocking:
            warnings.append(f"Export reported {len(blocking)} blocking issue(s).")
        if not valid:
            warnings.append("Submission did not pass the official validator; no download is offered.")

        state = (RunState.COMPLETED if valid and not coverage_open and not remaining and not blocking
                 and not _needs_revision(report) else RunState.PARTIAL)
        if not valid and not submission.get("requirements"):
            state = RunState.FAILED
        store.update(run_id, state=state.value, stage="done", finished_at=now_iso(),
                     revision_rounds=rounds, submission_valid=valid,
                     warnings=[*store.read(run_id).get("warnings", []), *warnings])
        store.append_event(run_id, {"stage": "done", "message": f"Run {state.value}", "timestamp": now_iso()})
    except Exception as exc:
        cancelled = isinstance(exc, RunCancelled)
        if not cancelled:
            (store.module_dir(run_id, "app") / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        message = ("Cancelled by user." if cancelled else str(exc) if isinstance(exc, ComponentError)
                   else f"{type(exc).__name__}: {exc}")
        current = store.read(run_id)
        store.update(run_id, state=RunState.FAILED.value, finished_at=now_iso(), cancelled=cancelled,
                     cancel_requested=False,
                     warnings=[*current.get("warnings", []), *warnings],
                     errors=[*current.get("errors", []), {"stage": emit.stage, "message": message}])
        try:
            emit({"stage": emit.stage, "message": f"Failed: {message}", "level": "error"})
        except Exception:
            pass


# --- RunView ------------------------------------------------------------------------------------


def build_run_view(store: RunStore, run_id: str) -> dict[str, Any] | None:
    """The shared RunView, assembled from persisted artifacts. None until extraction has produced a draft."""
    manifest = store.read(run_id)
    source_id = manifest.get("cached_from") or run_id
    draft = store.read_artifact(source_id, "pipeline_draft.json")
    if draft is None:
        return None
    report = store.read_artifact(source_id, "quality_report.json") or {}
    evidence = store.read_artifact(source_id, "evidence.json") or {"passages": [], "documents": []}
    validation = store.read_artifact(source_id, "validation.json")
    evaluation = store.read_artifact(source_id, "evaluation.json")
    submission_path = store.artifact_path(source_id, "submission.json")
    submission = json.loads(submission_path.read_text(encoding="utf-8")) if submission_path.is_file() else \
        store.read_artifact(source_id, "submission.invalid.json")

    passages = {p["passage_id"]: p for p in evidence.get("passages", []) if isinstance(p, dict)}
    documents = {d["document_id"]: d for d in evidence.get("documents", []) if isinstance(d, dict)}
    drafts = draft.get("draft_requirements") or []
    accepted = list(report.get("accepted_draft_ids") or [])
    duplicates = report.get("duplicate_mapping") or {}
    rejected_raw = report.get("rejected") or report.get("rejected_ids") or []
    rejected = ({r.get("draft_id"): r.get("reason") for r in rejected_raw if isinstance(r, dict)}
                if isinstance(rejected_raw, list) else dict(rejected_raw))
    findings = report.get("findings") or []

    # Link official requirements back to drafts. Prefer an explicit mapping from the quality module.
    view_warnings = []
    official = (submission or {}).get("requirements") or []
    export = store.read_artifact(source_id, "export.json") or {}
    mapping = export.get("export_mapping") or first(report, "export_mapping", "requirement_id_by_draft")
    if mapping is None and official and len(official) == len(accepted):
        mapping = {d: r["id"] for d, r in zip(accepted, official)}
        view_warnings.append("Draft-to-requirement mapping inferred from export order; ask quality module for export_mapping.")
    mapping = mapping or {}
    official_by_id = {r["id"]: r for r in official}

    requirements = []
    for d in drafts:
        did = d.get("draft_id")
        primary = passages.get(d.get("primary_passage_id") or "")
        doc = documents.get(primary["document_id"]) if primary else None
        official_req = official_by_id.get(mapping.get(did, ""))
        source = (official_req or {}).get("source") or {
            "regulation": doc.get("regulation") if doc else None,
            "version": doc.get("version_label") if doc else None,
            "page": primary.get("printed_start_page") if primary else None,
            "paragraph": primary.get("paragraph_label") if primary else None,
        }
        pdf_pages = (primary or {}).get("pdf_page_indices") or []
        status = ("accepted" if did in accepted else "duplicate" if did in duplicates
                  else "rejected" if did in rejected else "pending")
        requirements.append({
            "draft_id": did,
            "requirement_id": official_req["id"] if official_req else None,
            "status": status,
            "text": (official_req or d).get("text"),
            "source": source,
            "applicability_condition": d.get("applicability_condition"),
            "applicability_reason": d.get("applicability_reason"),
            "primary_passage_id": d.get("primary_passage_id"),
            "supporting_passage_ids": d.get("supporting_passage_ids") or [],
            "unresolved_references": d.get("unresolved_references") or [],
            "duplicate_of": duplicates.get(did),
            "rejection_reason": rejected.get(did),
            "findings": [f for f in findings if f.get("draft_id") == did],
            "source_link": {"document_id": primary["document_id"], "pdf_page_index": pdf_pages[0],
                            "printed_page": primary.get("printed_start_page")} if primary and pdf_pages else None,
        })

    coverage = draft.get("coverage") or []
    counts: dict[str, int] = {}
    for c in coverage:
        counts[c.get("status", "unknown")] = counts.get(c.get("status", "unknown"), 0) + 1

    return {
        "run": {k: manifest.get(k) for k in (
            "run_id", "state", "stage", "origin", "mode", "cached_from", "team_id", "case_id", "package_case",
            "input_hash", "corpus_snapshot_id", "llm_identity", "created_at", "started_at", "finished_at",
            "revision_rounds", "interrupted", "warnings", "errors", "spec_file", "drawing_files")},
        "product_profile": draft.get("product_profile"),
        "applicability_decisions": draft.get("applicability_decisions") or [],
        "requirements": requirements,
        "passages": passages,
        "documents": documents,
        "coverage": {"items": coverage, "counts": counts},
        "findings": findings,
        "remaining_issues": report.get("remaining_issues") or [],
        "review_complete": not review_incomplete(report),
        "export_excluded": export.get("excluded") or [],
        "export_blocking_issues": export.get("blocking_issues") or [],
        "unresolved_references": draft.get("unresolved_references") or [],
        "pipeline_warnings": draft.get("warnings") or [],
        "view_warnings": view_warnings,
        "usage": draft.get("usage"),
        "validation": validation,
        "evaluation": evaluation,
        "platform_metrics": store.read_artifact(run_id, "platform_metrics.json"),
        "engineer_reviews": store.read_artifact(run_id, "engineer_reviews.json") or {},
        "submission": {"available": submission_path.is_file(),
                       "requirement_count": len(official)},
    }
