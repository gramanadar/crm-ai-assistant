"""Structure-aware chunking. Size and overlap come from configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.rag.extract import ExtractedBlock


@dataclass(frozen=True)
class ChunkConfig:
    min_tokens: int = 500
    max_tokens: int = 800
    overlap_ratio: float = 0.12

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> "ChunkConfig":
        settings = settings or get_settings()
        min_tokens = max(1, int(settings.chunk_target_min_tokens))
        max_tokens = max(min_tokens, int(settings.chunk_target_max_tokens))
        overlap = min(0.5, max(0.0, float(settings.chunk_overlap_ratio)))
        return cls(min_tokens=min_tokens, max_tokens=max_tokens, overlap_ratio=overlap)


@dataclass
class ChunkDraft:
    heading_path: str
    page_from: int | None
    page_to: int | None
    text: str
    embed_text: str
    chunk_index: int = 0
    parts: list[ExtractedBlock] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def chunk_blocks(blocks: list[ExtractedBlock], config: ChunkConfig | None = None) -> list[ChunkDraft]:
    config = config or ChunkConfig.from_settings()
    groups: list[dict] = []
    for block in blocks:
        if block.kind == "heading" or not block.text.strip():
            continue
        path = list(block.heading_path)
        if not groups or groups[-1]["path"] != path:
            groups.append({"path": path, "parts": []})
        groups[-1]["parts"].append(block)

    drafts: list[ChunkDraft] = []
    for group in groups:
        drafts.extend(_pack_group(group["path"], group["parts"], config))
    for index, draft in enumerate(drafts):
        draft.chunk_index = index
    return drafts


def _pack_group(path: list[str], parts: list[ExtractedBlock], config: ChunkConfig) -> list[ChunkDraft]:
    units: list[ExtractedBlock] = []
    for part in parts:
        if estimate_tokens(part.text) <= config.max_tokens:
            units.append(part)
        else:
            units.extend(_split_large(part, config.max_tokens))

    grouped: list[list[ExtractedBlock]] = []
    current: list[ExtractedBlock] = []
    current_tokens = 0
    for unit in units:
        unit_tokens = estimate_tokens(unit.text)
        if current and current_tokens + unit_tokens > config.max_tokens:
            grouped.append(current)
            current = [unit]
            current_tokens = unit_tokens
        else:
            current.append(unit)
            current_tokens += unit_tokens
    if current:
        grouped.append(current)

    drafts: list[ChunkDraft] = []
    previous_text = ""
    heading = " > ".join(path)
    for parts_in_chunk in grouped:
        text = "\n\n".join(part.text.strip() for part in parts_in_chunk if part.text.strip())
        pages = [part.page for part in parts_in_chunk if part.page]
        overlap = _tail(previous_text, config.overlap_ratio) if previous_text else ""
        embed = _embed_text(heading, overlap, text)
        drafts.append(
            ChunkDraft(
                heading_path=heading,
                page_from=min(pages) if pages else None,
                page_to=max(pages) if pages else None,
                text=text,
                embed_text=embed,
                parts=parts_in_chunk,
            )
        )
        previous_text = text
    return drafts


def _split_large(part: ExtractedBlock, max_tokens: int) -> list[ExtractedBlock]:
    sentences = re.split(r"(?<=[.!?])\s+", part.text.strip())
    if len(sentences) == 1:
        words = part.text.split()
        sentences = []
        bucket: list[str] = []
        for word in words:
            bucket.append(word)
            if estimate_tokens(" ".join(bucket)) >= max_tokens:
                sentences.append(" ".join(bucket))
                bucket = []
        if bucket:
            sentences.append(" ".join(bucket))
    pieces: list[ExtractedBlock] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and estimate_tokens(candidate) > max_tokens:
            pieces.append(
                ExtractedBlock(kind=part.kind, text=current, heading_path=list(part.heading_path), page=part.page)
            )
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(
            ExtractedBlock(kind=part.kind, text=current, heading_path=list(part.heading_path), page=part.page)
        )
    return pieces or [part]


def _tail(text: str, ratio: float) -> str:
    if not text or ratio <= 0:
        return ""
    keep_tokens = max(1, int(estimate_tokens(text) * ratio))
    chars = keep_tokens * 4
    return text[-chars:]


def _embed_text(heading: str, overlap: str, text: str) -> str:
    parts = []
    if heading:
        parts.append(heading)
    body = f"{overlap}\n{text}".strip() if overlap else text
    if body:
        parts.append(body)
    return "\n\n".join(parts)
