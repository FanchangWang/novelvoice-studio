"""小说导入的混合结构识别：规则候选、本机 AI 确认、代码校验。"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from novelvoice_studio.ai.registry import ProviderRegistry
from novelvoice_studio.services.book_metadata import BookMetadata, extract_book_metadata
from novelvoice_studio.services.chapters import (
    ParseOutcome,
    StructuralHeading,
    normalize_chapter_title,
    parse_chapters,
)

logger = logging.getLogger(__name__)

_CN_NUM = "0-9零〇一二三四五六七八九十百千万两"
_NUMBERED = re.compile(rf"^[{_CN_NUM}]+[.、．]\s*\S+$")
_SEPARATOR = re.compile(r"^[=＝\-－—–_═─*]{4,}$")
_CACHE_TTL_SECONDS = 600
_CACHE_SIZE = 8
_CACHE_LOCK = threading.Lock()
_CACHE: OrderedDict[str, tuple[float, "ImportAnalysis"]] = OrderedDict()


@dataclass(frozen=True)
class ImportAnalysis:
    parsed: ParseOutcome
    metadata: BookMetadata
    structure_source: str
    ai_assisted: bool
    warnings: list[str]


def _compact(value: str) -> str:
    return re.sub(r"[\s《》〈〉【】\[\]()（）:：,，。.!！?？;；\"'“”‘’]", "", value).lower()


def _line_spans(text: str) -> list[tuple[int, int, int, str]]:
    spans: list[tuple[int, int, int, str]] = []
    offset = 0
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        spans.append((line_number, offset, offset + len(line), line.strip()))
        offset += len(line)
    if offset < len(text):
        spans.append((len(spans) + 1, offset, len(text), text[offset:].strip()))
    return spans


def _bounded_candidates(candidates: list[dict[str, object]], limit: int = 320) -> list[dict[str, object]]:
    if len(candidates) <= limit:
        return candidates
    # 长篇小说不能把全部候选塞入上下文；保留首尾并均匀采样，规则仍解析全书。
    head = limit // 3
    tail = limit // 3
    middle_count = limit - head - tail
    middle = candidates[head:len(candidates) - tail]
    if middle_count == 1:
        sampled = [middle[len(middle) // 2]]
    else:
        sampled = [middle[round(index * (len(middle) - 1) / (middle_count - 1))] for index in range(middle_count)]
    return [*candidates[:head], *sampled, *candidates[-tail:]]


def _candidate_lines(
    text: str,
    book_title: str | None = None,
) -> tuple[list[dict[str, object]], dict[int, tuple[int, int, str]]]:
    candidates: list[dict[str, object]] = []
    by_line: dict[int, tuple[int, int, str]] = {}
    for line_number, start, end, stripped in _line_spans(text):
        if not stripped or _SEPARATOR.fullmatch(stripped) or len(stripped) > 80:
            continue
        normalized = normalize_chapter_title(stripped, book_title=book_title)
        numbered = bool(_NUMBERED.fullmatch(normalized))
        short_heading = (
            2 <= len(normalized) <= 42
            and not normalized.endswith(("。", "！", "？", "!", "?", "；", ";", "，", ","))
        )
        if not (numbered or short_heading):
            continue
        by_line[line_number] = (start, end, stripped)
        candidates.append({
            "line_number": line_number,
            "text": stripped,
            "hint": "numbered" if numbered else "short",
        })
    bounded = _bounded_candidates(candidates)
    allowed_lines = {int(item["line_number"]) for item in bounded}
    return bounded, {line: span for line, span in by_line.items() if line in allowed_lines}


def _grounded_in_front_matter(value: str | None, front_matter: str) -> str | None:
    cleaned = " ".join((value or "").replace("\x00", " ").split()).strip(" \t\"'《》<>")
    if not cleaned:
        return None
    compact = _compact(cleaned)
    front = _compact(front_matter)
    if compact and compact in front:
        return cleaned[:255]
    return None


def _merge_ai_metadata(
    metadata: BookMetadata,
    ai_metadata,
    front_matter: str,
) -> tuple[BookMetadata, list[str]]:
    if ai_metadata is None:
        return metadata, []
    warnings: list[str] = []
    title = metadata.title
    title_source = metadata.title_source
    author = metadata.author
    author_source = metadata.author_source
    description = metadata.description
    description_source = metadata.description_source

    if metadata.title_source in {"filename", "content_plain"}:
        ai_title = _grounded_in_front_matter(ai_metadata.title, front_matter)
        if ai_title and _compact(ai_title) != _compact(metadata.title or ""):
            title = ai_title
            title_source = "ai_content"
    if author is None:
        ai_author = _grounded_in_front_matter(ai_metadata.author, front_matter)
        if ai_author:
            author = ai_author
            author_source = "ai_content"
    if description is None:
        ai_description = " ".join((ai_metadata.description or "").split()).strip()
        # 简介必须与 front matter 有实质原文重合，阻止模型编造内容。
        compact_description = _compact(ai_description)
        if 6 <= len(compact_description) <= 220:
            chunks = [compact_description[index:index + 8] for index in range(0, len(compact_description) - 7, 8)]
            matched = sum(chunk in _compact(front_matter) for chunk in chunks)
            if chunks and matched / len(chunks) >= 0.25:
                description = ai_description[:5000]
                description_source = "ai_content"
            else:
                warnings.append("本地 AI 提供的简介缺少原文依据，已忽略。")

    confidence = max(metadata.confidence, 0.86) if any(
        source == "ai_content" for source in (title_source, author_source, description_source)
    ) else metadata.confidence
    return BookMetadata(
        title=title,
        author=author,
        description=description,
        title_source=title_source,
        author_source=author_source,
        description_source=description_source,
        confidence=confidence,
        warnings=[*metadata.warnings, *warnings],
    ), warnings


def _cache_key(
    registry: ProviderRegistry,
    text: str,
    custom_regex: str | None,
    book_title: str | None,
) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    identity = f"{registry.llm.name}:{registry.llm.model}:{custom_regex or ''}:{book_title or ''}"
    return f"{identity}:{digest}"


def analyze_import(
    registry: ProviderRegistry | None,
    text: str,
    filename: str,
    *,
    custom_regex: str | None = None,
    book_title: str | None = None,
) -> ImportAnalysis:
    explicit_book_title = " ".join((book_title or "").split()) or None
    rule_parsed = parse_chapters(text, custom_regex, book_title=explicit_book_title)
    metadata = extract_book_metadata(
        text,
        filename,
        first_chapter_start=(
            rule_parsed.chapters[0].start
            if rule_parsed.chapters and rule_parsed.chapters[0].start > 0
            else None
        ),
    )
    metadata_book_title = normalize_chapter_title(metadata.title) if metadata.title else None
    effective_book_title = explicit_book_title or metadata_book_title
    if effective_book_title != explicit_book_title:
        rule_parsed = parse_chapters(text, custom_regex, book_title=effective_book_title)
    if registry is None or registry.llm.is_mock or custom_regex is not None:
        return ImportAnalysis(
            parsed=rule_parsed,
            metadata=metadata,
            structure_source="custom" if custom_regex else "rules",
            ai_assisted=False,
            warnings=[],
        )

    cache_key = _cache_key(registry, text, custom_regex, effective_book_title)
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
            _CACHE.move_to_end(cache_key)
            return cached[1]

    candidates, by_line = _candidate_lines(text, effective_book_title)
    first_story = next(
        (chapter.start for chapter in rule_parsed.chapters if chapter.chapter_kind == "story" and chapter.heading),
        None,
    )
    front_matter = text[:min(8_000, first_story) if first_story else 8_000]
    heading_starts = {chapter.start for chapter in rule_parsed.chapters if chapter.heading}
    rule_headings = [
        {"line_number": number, "title": stripped}
        for number, start, _end, stripped in _line_spans(text)
        if start in heading_starts
    ]
    try:
        result = registry.llm.inspect_import(
            front_matter=front_matter,
            candidates=candidates,
            rule_headings=rule_headings,
            include_metadata=True,
        )
    except Exception as exc:  # 本地模型离线或输出无效不能阻断导入。
        logger.warning("导入结构 AI 识别失败，回退到规则: %s", type(exc).__name__)
        return ImportAnalysis(
            parsed=rule_parsed,
            metadata=metadata,
            structure_source="rules",
            ai_assisted=False,
            warnings=["本地 AI 未能完成导入识别，已使用章节规则回退。"],
        )

    extra_headings: list[StructuralHeading] = []
    invalid_count = 0
    for heading in result.headings:
        span = by_line.get(heading.line_number)
        if span is None:
            invalid_count += 1
            continue
        start, end, stripped = span
        extra_headings.append(StructuralHeading(
            start=start,
            end=end,
            title=normalize_chapter_title(stripped, book_title=effective_book_title),
            heading_type=heading.heading_type,
            volume_number=heading.volume_number if heading.heading_type == "volume" else 0,
        ))
    metadata, metadata_warnings = _merge_ai_metadata(metadata, result.metadata, front_matter)
    merged_book_title = normalize_chapter_title(metadata.title) if metadata.title else None
    parsed = parse_chapters(
        text,
        custom_regex,
        book_title=explicit_book_title or merged_book_title,
        extra_headings=extra_headings,
    )
    warnings = list(metadata_warnings)
    if invalid_count:
        warnings.append(f"本地 AI 返回了 {invalid_count} 个无效标题位置，已忽略。")
    analysis = ImportAnalysis(
        parsed=parsed,
        metadata=metadata,
        structure_source="ai",
        ai_assisted=True,
        warnings=warnings,
    )
    with _CACHE_LOCK:
        _CACHE[cache_key] = (time.monotonic(), analysis)
        _CACHE.move_to_end(cache_key)
        while len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
    return analysis
