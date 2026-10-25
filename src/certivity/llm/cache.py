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


class LayeredCache:
    """The run's own cache in front of a cache shared by every run.

    The run folder keeps its record of what was asked and answered (audit);
    the shared folder means a rerun, a restart or a second run of the same
    case does not pay again for an identical call. The key is the same in
    both, and it covers everything that can change an answer, so sharing it
    across runs cannot return a different input's answer.
    """

    def __init__(self, primary: CacheBackend, shared: CacheBackend) -> None:
        self.primary = primary
        self.shared = shared

    def get(self, key: str) -> Optional[dict[str, Any]]:
        value = self.primary.get(key)
        if value is not None:
            return value
        value = self.shared.get(key)
        if value is not None:
            self.primary.put(key, value)
        return value

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.primary.put(key, value)
        self.shared.put(key, value)
