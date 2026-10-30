"""Document ingestion: bytes -> pages -> clause-level chunks (page numbers preserved)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

from .textutil import squash

PAGE_RE = re.compile(r"^\s*-{2,}\s*page\s+(\d+)\s*-{2,}\s*$", re.I | re.M)
CLAUSE_RE = re.compile(r"^\s*(\d+(?:\.\d+)+)[.)]?\s+(.*)")


class NoTextError(Exception):
    """The file has no extractable text (probably a scan)."""


@dataclass
class Chunk:
    id: str
    page: int
    text: str


def pages_from_text(text: str) -> list[str]:
    parts = PAGE_RE.split(text)
    if len(parts) == 1:
        return [text]
    # split() with one capture group -> [pre, n1, body1, n2, body2, ...]
    return [parts[i + 1] for i in range(1, len(parts) - 1, 2)]


def pages_from_bytes(filename: str, data: bytes) -> list[str]:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages]
        if sum(len(p.strip()) for p in pages) < 40:
            raise NoTextError(f"{filename}: no extractable text (scanned PDF?)")
        return pages
    if name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        raise NoTextError(f"{filename}: image input needs the vision extractor")
    return pages_from_text(data.decode("utf-8", errors="replace"))


def chunk_pages(pages: list[str]) -> list[Chunk]:
    """Split pages into clause chunks ('4.1 Room rent ...'). Falls back to paragraphs."""
    chunks: list[Chunk] = []
    for pno, page in enumerate(pages, 1):
        cur_id, cur_lines = None, []

        def flush():
            if cur_id and cur_lines:
                chunks.append(Chunk(cur_id, pno, squash(" ".join(cur_lines))))

        for line in page.splitlines():
            m = CLAUSE_RE.match(line)
            if m:
                flush()
                cur_id, cur_lines = m.group(1), [m.group(2)]
            elif cur_id and line.strip():
                cur_lines.append(line.strip())
        flush()

        if not any(c.page == pno for c in chunks):  # no numbered clauses on this page
            paras = [squash(p) for p in re.split(r"\n\s*\n", page) if squash(p)]
            for i, para in enumerate(paras, 1):
                chunks.append(Chunk(f"p{pno}-{i}", pno, para))
    return chunks
