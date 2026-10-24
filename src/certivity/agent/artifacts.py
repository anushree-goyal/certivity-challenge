"""Per-run agent artifacts (owner: Anushree).

The agent component writes only inside its own ``agent/`` subfolder of a run
directory. Teammate 4 owns overall run state and the ``runs/`` manifest; this
module must never write outside the folder it is handed.

Saving the draft, the prompt versions, the model configuration and the snapshot
identity is what makes a run auditable afterwards: plan rule 9.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from certivity.contracts import PipelineDraft, RunInput, to_jsonable

logger = logging.getLogger(__name__)


class AgentArtifacts:
    """Writer for one run's agent folder. ``None`` directory disables writing."""

    def __init__(self, directory: str | os.PathLike[str] | None) -> None:
        self.directory: Optional[Path] = Path(directory) if directory else None
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def write_json(self, name: str, payload: Any) -> Optional[Path]:
        """Write one artifact. A failure here must never fail the run."""
        if self.directory is None:
            return None
        path = self.directory / name
        try:
            with path.open("w", encoding="utf-8") as handle:
                json.dump(to_jsonable(payload), handle, ensure_ascii=False, indent=1)
        except OSError:
            logger.warning("could not write agent artifact %s", path, exc_info=True)
            return None
        return path

    def save_draft(
        self,
        draft: PipelineDraft,
        run_input: RunInput,
        *,
        model_configuration: dict[str, Any] | None = None,
        name: str = "pipeline_draft.json",
    ) -> Optional[Path]:
        """Save the draft together with the identity needed to reproduce it."""
        return self.write_json(
            name,
            {
                "run_id": run_input.run_id,
                "case_id": run_input.case_id,
                "team_id": run_input.team_id,
                "input_hash": run_input.input_hash,
                "corpus_snapshot_id": run_input.corpus_snapshot_id,
                "model_configuration": model_configuration or {},
                "prompt_versions": draft.prompt_versions,
                "draft": draft,
            },
        )
