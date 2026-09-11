"""Recursive text splitter.

Paragraph -> sentence -> hard character split, with a configurable overlap so
retrieval keeps cross-chunk context.
"""

import re

from app.config.settings import settings

_PARAGRAPH_RE = re.compile(r"\n\s*\n+")
_SENTENCE_RE = re.compile(r"(?<=[。！？!?；;\.])\s*")


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_long_paragraph(paragraph: str, chunk_size: int) -> list[str]:
    if len(paragraph) <= chunk_size:
        return [paragraph]

    units = [unit for unit in _SENTENCE_RE.split(paragraph) if unit.strip()]
    if len(units) <= 1:
        return [paragraph[i : i + chunk_size] for i in range(0, len(paragraph), chunk_size)]

    result: list[str] = []
    buffer = ""
    for unit in units:
        if len(unit) > chunk_size:
            if buffer:
                result.append(buffer)
                buffer = ""
            for i in range(0, len(unit), chunk_size):
                result.append(unit[i : i + chunk_size])
            continue
        if buffer and len(buffer) + len(unit) > chunk_size:
            result.append(buffer)
            buffer = unit
        else:
            buffer = buffer + unit
    if buffer:
        result.append(buffer)
    return [item.strip() for item in result if item.strip()]


def split_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    chunk_size = chunk_size or settings.chunk_size
    overlap = settings.chunk_overlap if overlap is None else overlap
    overlap = max(0, min(overlap, max(0, chunk_size // 2)))

    normalized = _normalize(text)
    if not normalized:
        return []

    pieces: list[str] = []
    for paragraph in _PARAGRAPH_RE.split(normalized):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        pieces.extend(_split_long_paragraph(paragraph, chunk_size))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if not piece:
            continue
        if current and len(current) + len(piece) + 1 > chunk_size:
            chunks.append(current.strip())
            tail = current[-overlap:] if overlap > 0 else ""
            current = (tail + "\n" + piece).strip() if tail else piece
        elif current:
            current = current + "\n" + piece
        else:
            current = piece

        while len(current) > chunk_size:
            chunks.append(current[:chunk_size].strip())
            start = chunk_size - overlap if overlap > 0 else chunk_size
            current = current[start:]

    if current.strip():
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk]
