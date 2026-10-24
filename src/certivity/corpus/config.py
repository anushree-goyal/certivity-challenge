"""Locations of the participant package and generated corpus data. Override with environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class CorpusPaths:
    package_root: Path
    cache_root: Path

    @property
    def snapshots(self) -> Path:
        return self.cache_root / "snapshots"

    @property
    def parsed(self) -> Path:
        return self.cache_root / "parsed"

    @property
    def renders(self) -> Path:
        return self.cache_root / "renders"

    @property
    def current_pointer(self) -> Path:
        return self.cache_root / "CURRENT"


def default_paths(package_root: str | Path | None = None, cache_root: str | Path | None = None) -> CorpusPaths:
    package = package_root or os.environ.get("CERTIVITY_PARTICIPANT_PACKAGE") or REPO_ROOT / "data" / "participant-package"
    cache = cache_root or os.environ.get("CERTIVITY_CORPUS_CACHE") or REPO_ROOT / "data" / "cache" / "corpus"
    return CorpusPaths(Path(package).resolve(), Path(cache).resolve())
