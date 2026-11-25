"""章节标题识别、章节切分与段落切分。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from novelvoice_studio.core.errors import ValidationError

_CN_NUM = "0-9零〇一二三四五六七八九十百千万两"
_DEFAULT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"^第\s*[{_CN_NUM}]+\s*[章节回卷部篇集]"
        rf"(?:(?:\s+|[、:：.．\-—]).{{0,30}})?$"
    ),
    re.compile(
        r"^(?:序章|序言|楔子|引子|前言|尾声|后记|终章|番外(?:篇)?)"
        r"(?:(?:\s+|[、:：.．\-—]).{0,30})?$"
    ),
    re.compile(
        r"^(?:chapter|prologue|epilogue|part)\s+[\divxlcdm]+"
        r"(?:(?:\s+|[:.\-—]\s*).{0,50})?$",
        re.IGNORECASE,
    ),
)
_FORBIDDEN_TAIL = ("。", "！", "？", "!", "?", "；", ";")
_MAX_HEADING_LENGTH = 60


@dataclass(frozen=True)
class ChapterSlice:
    title: str
    start: int
    end: int
    heading: str | None


@dataclass(frozen=True)
class ParseOutcome:
    chapters: list[ChapterSlice]
    warnings: list[str]
    used_custom_regex: bool


@dataclass(frozen=True)
class ParagraphSlice:
    index: int
    start: int
    end: int
    text: str


def compile_custom_regex(pattern: str | None) -> re.Pattern[str] | None:
    if not pattern or not pattern.strip():
        return None
    try:
        return re.compile(pattern, re.MULTILINE)
    except re.error as exc:
        raise ValidationError(f"自定义章节正则无效：{exc}") from exc


def _is_heading(line: str, custom: re.Pattern[str] | None) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADING_LENGTH:
        return False
    if stripped.endswith(_FORBIDDEN_TAIL):
        return False
    if custom is not None:
        return custom.match(stripped) is not None
    return any(pattern.match(stripped) for pattern in _DEFAULT_PATTERNS)


def parse_chapters(text: str, custom_regex: str | None = None) -> ParseOutcome:
    custom = compile_custom_regex(custom_regex)
    warnings: list[str] = []
    headings: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        if _is_heading(line, custom):
            stripped = line.strip()
            title = stripped
            if custom is not None:
                match = custom.match(stripped)
                if match is not None and match.lastindex:
                    grouped = (match.group(1) or "").strip()
                    if grouped:
                        title = grouped
            headings.append((offset, title[:255]))
        offset += len(line)
    # splitlines 对末尾没有换行的最后一行也会返回，offset 已覆盖全文
    if offset < len(text):
        tail = text[offset:]
        if _is_heading(tail, custom):
            headings.append((offset, tail.strip()[:255]))

    if not headings:
        warnings.append("未识别到章节标题，已作为单章导入；可在工作区手动拆分。")
        return ParseOutcome(
            chapters=[ChapterSlice(title="全文", start=0, end=len(text), heading=None)],
            warnings=warnings,
            used_custom_regex=custom is not None,
        )

    chapters: list[ChapterSlice] = []
    if headings[0][0] > 0 and text[: headings[0][0]].strip():
        chapters.append(
            ChapterSlice(title="前言", start=0, end=headings[0][0], heading=None)
        )
        warnings.append("首个章节标题前存在正文，已归入“前言”。")

    for index, (start, title) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        if not text[start:end].strip():
            warnings.append(f"章节「{title}」没有正文内容。")
        chapters.append(ChapterSlice(title=title or "未命名章节", start=start, end=end, heading=title))

    if custom is not None:
        warnings.append("已使用自定义章节正则。")
    return ParseOutcome(chapters=chapters, warnings=warnings, used_custom_regex=custom is not None)


def _line_spans(text: str, start: int, end: int) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    cursor = start
    for line in text[start:end].splitlines(keepends=True):
        spans.append((cursor, cursor + len(line), line))
        cursor += len(line)
    if cursor < end:
        spans.append((cursor, end, text[cursor:end]))
    return spans


def split_paragraphs(text: str, start: int, end: int) -> list[ParagraphSlice]:
    """把章节正文切成段落，返回相对全文的绝对偏移。"""
    if start < 0 or end > len(text) or start >= end:
        return []
    spans = _line_spans(text, start, end)
    non_empty = [(s, e, line) for s, e, line in spans if line.strip()]
    if not non_empty:
        return []

    blank_exists = any(not line.strip() for _, _, line in spans)

    blocks: list[tuple[int, int]] = []
    if blank_exists:
        current: list[int] | None = None
        for line_start, line_end, line in spans:
            if line.strip():
                if current is None:
                    current = [line_start, line_end]
                else:
                    current[1] = line_end
            elif current is not None:
                blocks.append((current[0], current[1]))
                current = None
        if current is not None:
            blocks.append((current[0], current[1]))
    else:
        blocks = [(line_start, line_end) for line_start, line_end, _ in non_empty]

    paragraphs: list[ParagraphSlice] = []
    for block_start, block_end in blocks:
        raw = text[block_start:block_end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        content_start = block_start + leading
        content_end = block_end - trailing
        if content_end <= content_start:
            continue
        paragraphs.append(
            ParagraphSlice(
                index=len(paragraphs),
                start=content_start,
                end=content_end,
                text=text[content_start:content_end],
            )
        )
    return paragraphs