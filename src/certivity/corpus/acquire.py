"""Download source PDFs with the participant package's own tools/fetch_regulations.py (checksums verified there)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

from .config import CorpusPaths

_LINE = re.compile(r"^\[(\d+)/(\d+)\]\s+(?:(FAILED)\s+(?P<err>.*)|(?P<label>.+?):\s+(?P<state>ok|cached))$")


@dataclass
class AcquireReport:
    returncode: int
    downloaded: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    output: str = ""


def fetch(paths: CorpusPaths, role: str | None = "base", only: list[str] | None = None, timeout: int = 3600) -> AcquireReport:
    tool = paths.package_root / "tools" / "fetch_regulations.py"
    if not tool.is_file():
        raise FileNotFoundError(
            f"participant package not found at {paths.package_root}; clone https://github.com/Certivity/industrial-ai-hackathon there "
            "or set CERTIVITY_PARTICIPANT_PACKAGE"
        )
    cmd = [sys.executable, "-I", str(tool)]
    if role:
        cmd += ["--role", role]
    for name in only or []:
        cmd += ["--only", name]
    env = dict(os.environ)
    if "SSL_CERT_FILE" not in env:  # python.org builds on macOS ship without CA certificates
        try:
            import certifi

            env["SSL_CERT_FILE"] = certifi.where()
        except ImportError:
            pass
    proc = subprocess.run(cmd, cwd=paths.package_root, capture_output=True, text=True, env=env, timeout=timeout)
    report = AcquireReport(proc.returncode, output=proc.stdout + proc.stderr)
    for line in report.output.splitlines():
        match = _LINE.match(line.strip())
        if not match:
            continue
        if match.group(3):
            report.failed.append(match.group("err"))
        elif match.group("state") == "ok":
            report.downloaded.append(match.group("label"))
        else:
            report.cached.append(match.group("label"))
    return report
