"""On-disk cache for model calls (owner: Anushree).

A cached result is only reused when the whole call identity matches: task,
prompt version, instructions, payload, schema, images, provider and model
configuration. Plan rule 9 requires that a cached result match the current
input and configuration, so the key deliberately includes everything that
could change the answer. Changing a prompt therefore misses the cache rather
than silently returning the previous run's reasoning.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional, Protocol


class CacheBackend(Protocol):
    def get(self, key: str) -> Optional[dict[str, Any]]: ...
    def put(self, key: str, value: dict[str, Any]) -> None: ...


class NullCache:
    """Disabled cache. Used when ``RunConfig.use_cache`` is false."""

    def get(self, key: str) -> Optional[dict[str, Any]]:
        return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        return None


class CallCache:
    """JSON files under one directory, sharded by key prefix.

    Writes are atomic (temp file plus ``os.replace``) so a crashed run cannot
    leave a half-written entry that a later run would read back as valid.
    """

    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str) -> Optional[dict[str, Any]]:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, OSError):
            # A corrupt entry is a cache miss, never a hard failure.
            return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
