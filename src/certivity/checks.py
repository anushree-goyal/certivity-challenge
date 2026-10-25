"""The participant package's own tools: the official validator and the offline regulation/page scorer."""

from __future__ import annotations

import importlib.util
import json
import sys
from functools import cache

from certivity.config import settings


@cache
def _tool(name: str):
    path = settings.package_dir / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"participant_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses in the tool look their module up
    spec.loader.exec_module(module)
    return module


def validate(submission: dict, requirements_old: list[dict] | None = None) -> dict:
    tool = _tool("validate")
    index = tool.load_index(settings.package_dir / "regulations" / "index.json")
    problems = tool.validate(submission, index=index, requirements_old=requirements_old)
    return {"valid": not problems, "errors": [{"path": p.path, "message": p.message} for p in problems]}


def score(submission: dict, case_id: str) -> dict | None:
    """Regulation and page level against the case's expected.json, if the package ships one (dev case only)."""
    expected = settings.package_dir / "cases" / case_id / "expected.json"
    if not expected.is_file():
        return None
    result = _tool("score_local").score(submission, json.loads(expected.read_text()))
    return {level: {k: round(v, 3) if isinstance(v, float) else v for k, v in values.items()}
            for level, values in result.items()}
