"""Load the participant index into document specs. One spec per file, so multi-file versions are supported."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DocumentSpec:
    document_id: str
    regulation: str
    regulation_title: str
    role: str
    version_label: str
    version_title: str
    filename: str
    sha256: str
    size_bytes: int
    local_path: Path  # server-only; never exposed through public records

    @property
    def is_present(self) -> bool:
        return self.local_path.is_file()


def regulation_slug(regulation: str) -> str:
    """Same folder naming as tools/fetch_regulations.py."""
    return "_".join(regulation.replace("/", " ").split())


def make_document_id(regulation: str, version_label: str, filename: str, sha256: str) -> str:
    """Readable and unique: regulation, version, file stem and checksum prefix, e.g. ``UN_R48@9.3.0~UN_R48_base~1f1ddd74``."""
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(filename).stem)
    return f"{regulation_slug(regulation)}@{version_label}~{stem}~{sha256[:8]}"


def load_index(package_root: Path) -> list[DocumentSpec]:
    index_path = package_root / "regulations" / "index.json"
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    pdf_root = package_root / "regulations" / "pdf"
    specs: list[DocumentSpec] = []
    for entry in entries:
        slug = regulation_slug(entry["regulation"])
        for version in entry.get("versions", []):
            for file in version.get("files", []):
                specs.append(
                    DocumentSpec(
                        document_id=make_document_id(entry["regulation"], version["version_label"], file["name"], file["sha256"]),
                        regulation=entry["regulation"],
                        regulation_title=entry.get("title", ""),
                        role=version["role"],
                        version_label=version["version_label"],
                        version_title=version.get("title", ""),
                        filename=file["name"],
                        sha256=file["sha256"].lower(),
                        size_bytes=int(file.get("size_bytes", 0)),
                        local_path=pdf_root / slug / version["role"] / file["name"],
                    )
                )
    return specs


def index_sha256(package_root: Path) -> str:
    return hashlib.sha256((package_root / "regulations" / "index.json").read_bytes()).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_REG_NUMBER = re.compile(r"^UN\s+R(\d+)([A-Z]*)$")


def regulation_number_map(specs: list[DocumentSpec]) -> dict[str, str]:
    """'48' -> 'UN R48', '13H' -> 'UN R13H'; used to resolve 'Regulation No. 48' style references."""
    out: dict[str, str] = {}
    for spec in specs:
        match = _REG_NUMBER.match(spec.regulation)
        if match:
            out[match.group(1) + match.group(2)] = spec.regulation
    return out
