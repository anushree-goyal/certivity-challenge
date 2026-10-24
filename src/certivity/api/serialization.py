"""Turn shared contract records (pydantic models, dataclasses, enums, paths) into plain JSON data.

The app persists and serves module outputs as JSON, so it never depends on whether a record is a
pydantic model or a dataclass.
"""

from __future__ import annotations

import dataclasses
import enum
from datetime import date, datetime
from pathlib import PurePath
from typing import Any


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "model_dump"):  # pydantic v2
        return to_jsonable(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return to_jsonable(value.value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if hasattr(value, "__dict__"):
        return {k: to_jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def first(data: dict, *keys: str, default: Any = None) -> Any:
    """The first present, non-None value among several candidate field names."""
    for key in keys:
        if isinstance(data, dict) and data.get(key) is not None:
            return data[key]
    return default
