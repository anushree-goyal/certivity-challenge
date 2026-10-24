"""On-demand rendering of exact physical PDF pages, cached by document checksum."""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf

DEFAULT_DPI = 144


def render_page(pdf_path: Path, checksum: str, pdf_page_index: int, cache_dir: Path, dpi: int = DEFAULT_DPI) -> Path:
    target = cache_dir / checksum / f"{pdf_page_index:04d}@{dpi}.png"
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(pdf_path) as doc:
        if not 0 <= pdf_page_index < doc.page_count:
            raise IndexError(f"pdf_page_index {pdf_page_index} outside 0..{doc.page_count - 1}")
        pixmap = doc[pdf_page_index].get_pixmap(dpi=dpi)
        tmp = target.with_suffix(f".tmp{os.getpid()}.png")
        pixmap.save(tmp)
    tmp.replace(target)
    return target
