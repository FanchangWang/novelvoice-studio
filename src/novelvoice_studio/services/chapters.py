"""章节标题识别、章节切分与段落切分。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from novelvoice_studio.core.errors import ValidationError

_CN_NUM = "0-9零〇一二三四五六七八九十百千万两"
_VOLUME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"^第\s*[{_CN_NUM}]+\s*卷"
        rf"(?:(?:\s+|[、:：.．\-—]).{{0,30}})?$"
    ),
    re.compile(
        r"^(?:volume|part)\s+[\divxlcdm]+"
        r"(?:(?:\s+|[:.\-—]\s*).{0,50})?$",
        re.IGNORECASE,
    ),
)
_DEFAULT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        rf"^第\s*[{_CN_NUM}]+\s*[章节回部篇集]"
        rf"(?:(?:\s+|[、:：.．\-—]).{{0,30}})?$"
    ),
    re.compile(
        r"^(?:序章|序言|楔子|引子|前言|尾声|后记|终章|番外(?:篇)?)"
        r"(?:(?:\s+|[、:：.．\-—]).{0,30})?$"
    ),
    re.compile(
        r"^(?:请假(?:条|单)?|停更(?:说明)?|更新说明|作者有话|单章说明|上架感言)"
        r"(?:(?:\s+|[、:：.．\-—]).{0,30})?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:chapter|prologue|epilogue)\s+[\divxlcdm]+"
        r"(?:(?:\s+|[:.\-—]\s*).{0,50})?$",
        re.IGNORECASE,
    ),
)
_FORBIDDEN_TAIL = ("。", "！", "？", "!", "?", "；", ";")
_MAX_HEADING_LENGTH = 60
_EXTRA_TITLE_PATTERN = re.compile(
    r"(?:番外|请假|停更|更新说明|作者有话|单章说明|后记|前言|序言|卷首语|上架感言|请假条)",
    re.IGNORECASE,
)
_AUTHOR_NOTE_TITLE_PATTERN = re.compile(
    r"(?:作者有话|更新说明|单章说明|上架感言|请假|停更|请假条)",
    re.IGNORECASE,
)
_ATX_PREFIX = re.compile(r"^\s{0,3}#{1,6}(?:[ \t]+|(?=$))")
_ATX_SUFFIX = re.compile(r"[ \t]+#+[ \t]*$")
_LEADING_TITLE_SYMBOLS = re.compile(r"^[\s>*+\-–—_=~·•●▪◦]+")
_TITLE_LABEL = re.compile(r"^(?:书名|小说名|作品名)\s*[：:]\s*", re.IGNORECASE)
_WRAPPED_TITLE_LABEL = re.compile(
    r"^[【\[《〈]\s*(?:书名|小说名|作品名)\s*[：:]\s*"
    r"(?P<book>[^】\]》〉]+?)\s*[】\]》〉]\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_NUMBER_PREFIX = re.compile(
    rf"^(?:"
    rf"第\s*(?P<cn_number>[{_CN_NUM}]+)\s*(?P<cn_unit>[章节回部篇集卷])"
    rf"|(?P<en_kind>chapter|part|volume)\s+(?P<en_number>[\divxlcdm]+)"
    rf"|(?P<plain_number>[{_CN_NUM}]+)\s*[.、．]\s*"
    rf")",
    re.IGNORECASE,
)
_SPECIAL_TITLE_PREFIX = re.compile(
    r"^(?:序章|序言|楔子|引子|前言|尾声|后记|终章|番外(?:篇)?|"
    r"请假(?:条|单)?|停更(?:说明)?|更新说明|作者有话|单章说明|上架感言)",
    re.IGNORECASE,
)
_TITLE_NOISE_TOKEN = (
    r"(?:https?://|www\.)\S+"
    r"|(?:本文|内容)?(?:来自|来源)[：:]?\s*(?:https?://|www\.)\S+"
    r"|(?:[a-z0-9-]+\.)+(?:com|cn|net|org|top|xyz|info|me)(?=$|[\s/:#?）)])(?:[/:]\S*)?"
    r"|(?:本(?:站|书))(?:作品)?(?:仅供试阅|来自)"
    r"|(?:小说|本书)?(?:全文|免费|在线)(?:阅读|下载)(?=$|[\s:：，,）)\]】])"
    r"|最新章节(?:更新)?(?=$|[\s:：，,])"
    r"|(?:小说|本书|全集|全本)?(?:txt|text)(?:全集|全本|电子书)?下载(?=$|[\s:：，,）)\]】])"
    r"|(?:全集|全本)下载(?=$|[\s:：，,）)\]】])"
    r"|(?:下载|阅读)地址(?=$|[\s:：，,）)\]】])"
    r"|(?:点击|立即)(?:阅读|下载)(?=$|[\s:：，,）)\]】])"
    r"|(?:笔趣阁|顶点小说|书旗小说|起点中文网)(?:无弹窗)?(?=$|[\s（(\[【:：]|https?://|www\.)"
    r"|(?:下载器|下载站)(?:推荐|提供|收录)(?=$|[\s:：）)】])"
    r"|(?:版权所有|著作权(?:所有|信息)|侵权必究)"
    r"|(?:推广|广告)(?:信息|合作)(?=$|[\s:：）)\]】])"
    r"|(?:推广|广告)[：:][^\n]*"
    r"|(?:本章)?(?:未完|未结)[，,]?\s*请(?:点击|移步|前往)[^\n]*"
    r"|请(?:点击|移步|前往)[^\n]*(?:下一页|下一章|继续阅读)"
    r"|(?:手机|电脑)(?:用户)?(?:访问|阅读)(?:地址)?(?=$|[\s:：])"
)
_TITLE_NOISE_SUFFIX = re.compile(
    rf"(?:^(?:{_TITLE_NOISE_TOKEN})"
    rf"|(?<=\S)(?:https?://|www\.)\S+"
    rf"|(?:\s+|[（(\[【<|｜:：,，;；]|[-—–|·]+)\s*(?:[-—–|·]\s*)?(?:{_TITLE_NOISE_TOKEN}))[^\n]*$",
    re.IGNORECASE,
)
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


@dataclass(frozen=True)
class StructuralHeading:
    """由 AI 或确定性回退提供、必须在原文中校验过的标题位置。"""

    start: int
    end: int
    title: str
    heading_type: str = "story"
    volume_number: int = 0


@dataclass(frozen=True)
class ChapterSlice:
    title: str
    start: int
    end: int
    heading: str | None
    volume_number: int = 0
    volume_title: str = "默认卷"
    chapter_kind: str = "story"
    analysis_included: bool = True


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


def _is_heading(
    line: str,
    custom: re.Pattern[str] | None,
    book_title: str | None = None,
) -> bool:
    stripped = line.strip()
    candidate = stripped if custom is not None else normalize_chapter_title(stripped, book_title=book_title)
    if not candidate or len(candidate) > _MAX_HEADING_LENGTH:
        return False
    if candidate.endswith(_FORBIDDEN_TAIL):
        return False
    if custom is not None:
        return custom.match(stripped) is not None
    return any(pattern.match(candidate) for pattern in _DEFAULT_PATTERNS)


def _is_volume_heading(
    line: str,
    custom: re.Pattern[str] | None,
    book_title: str | None = None,
) -> bool:
    if custom is not None:
        return False
    candidate = normalize_chapter_title(line.strip(), book_title=book_title)
    if not candidate or len(candidate) > _MAX_HEADING_LENGTH:
        return False
    if candidate.endswith(_FORBIDDEN_TAIL):
        return False
    return any(pattern.match(candidate) for pattern in _VOLUME_PATTERNS)


def _cn_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    total = 0
    current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in units:
            unit = units[char]
            total += (current or 1) * unit
            current = 0
    return total + current


def _roman_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    total = 0
    previous = 0
    for char in reversed(value.lower()):
        current = _ROMAN_VALUES[char]
        total += -current if current < previous else current
        previous = max(previous, current)
    return total


def _number_prefix(match: re.Match[str]) -> tuple[str, int]:
    if match.group("cn_number") is not None:
        return f"cn:{match.group('cn_unit')}", _cn_number(match.group("cn_number"))
    if match.group("en_number") is not None:
        return f"en:{match.group('en_kind').lower()}", _roman_number(match.group("en_number"))
    return "numbered", _cn_number(match.group("plain_number"))


def _looks_like_heading(value: str) -> bool:
    return bool(_NUMBER_PREFIX.match(value) or _SPECIAL_TITLE_PREFIX.match(value))


def _strip_book_title_prefix(value: str, book_title: str | None) -> str:
    if not book_title:
        return value
    cleaned_book = book_title.replace("\x00", " ").strip()
    cleaned_book = _TITLE_LABEL.sub("", cleaned_book).strip()
    cleaned_book = cleaned_book.strip(" \t\"'《》〈〉【】[]")
    if not cleaned_book:
        return value
    prefixes = (
        f"《{cleaned_book}》", f"〈{cleaned_book}〉", f"【{cleaned_book}】",
        f"[{cleaned_book}]", cleaned_book,
    )
    for prefix in prefixes:
        if not value.startswith(prefix) or value == prefix:
            continue
        remainder = value[len(prefix):]
        remainder = re.sub(r"^[\s:：|｜\-—–]+", "", remainder)
        if remainder and _looks_like_heading(remainder):
            return remainder
    return value


def _collapse_duplicate_number_prefix(value: str) -> str:
    first = _NUMBER_PREFIX.match(value)
    if first is None:
        return value
    first_kind, first_number = _number_prefix(first)
    prefix = value[:first.end()]
    remainder = value[first.end():]
    while remainder:
        separator = re.match(r"^[ \t:：、.．\-—–|｜]*", remainder)
        candidate = remainder[separator.end():]
        second = _NUMBER_PREFIX.match(candidate)
        if second is None:
            break
        second_kind, second_number = _number_prefix(second)
        compatible = first_kind == second_kind or "numbered" in {first_kind, second_kind}
        if not compatible or first_number != second_number:
            break
        remainder = candidate[second.end():]
        following_separator = re.match(r"^(?:[ \t]+|[、:：.．\-—–]\s*)", remainder)
        if following_separator is not None:
            remainder = following_separator.group(0) + remainder[following_separator.end():]
    return prefix + remainder


def normalize_chapter_title(title: str, *, book_title: str | None = None) -> str:
    """清洗导入标题，不改写原文；无法确认是噪声时保留语义。"""

    value = (title or "").replace("\x00", " ").strip()
    for _ in range(3):
        previous = value
        value = _ATX_PREFIX.sub("", value).strip()
        value = _LEADING_TITLE_SYMBOLS.sub("", value).strip()
        if value == previous:
            break

    wrapped_label = _WRAPPED_TITLE_LABEL.match(value)
    if wrapped_label is not None:
        remainder = wrapped_label.group("rest").strip()
        value = remainder if remainder and _looks_like_heading(remainder) else wrapped_label.group("book").strip()
    else:
        value = _TITLE_LABEL.sub("", value).strip()

    noise = _TITLE_NOISE_SUFFIX.search(value)
    if noise is not None:
        value = value[:noise.start()].rstrip()
    value = _ATX_SUFFIX.sub("", value).rstrip()
    value = _strip_book_title_prefix(value, book_title)
    value = _collapse_duplicate_number_prefix(value)
    return value.strip()


def _volume_number(title: str) -> int:
    match = re.search(rf"第\s*([{_CN_NUM}]+)\s*卷", title)
    if match:
        return _cn_number(match.group(1))
    match = re.search(r"(?:volume|part)\s+([\divxlcdm]+)", title, re.IGNORECASE)
    return _cn_number(match.group(1)) if match else 0


def classify_chapter(title: str) -> tuple[str, bool]:
    """返回章节分类与是否默认纳入分析。特殊章节保留，但默认排除。"""
    if _AUTHOR_NOTE_TITLE_PATTERN.search(title):
        return "author_note", False
    if _EXTRA_TITLE_PATTERN.search(title):
        return "extra", False
    return "story", True


_NUMBERED_HEADING = re.compile(
    rf"^(?P<number>[{_CN_NUM}]+)\s*[.、．]\s*(?P<title>\S.*)$"
)
_PREFACE_TITLES = {"前言", "序言", "序章", "楔子", "引子", "卷首语"}


def numbered_heading_fallback(text: str) -> list[StructuralHeading]:
    """识别“1.标题 / 二、标题”式章节，避免把正文内编号清单当成章节。

    只接受从 1 开始的最长严格递增编号链；这样正文中的“1、2、3”清单
    不会覆盖真正贯穿全书的编号章节。规则无法确认时返回空列表。
    """

    candidates: list[tuple[int, int, str, int, int]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        match = _NUMBERED_HEADING.match(stripped)
        if (
            match
            and len(stripped) <= _MAX_HEADING_LENGTH
            and not stripped.endswith(_FORBIDDEN_TAIL)
            and not match.group("title")[:1].isdigit()
        ):
            number = _cn_number(match.group("number"))
            if number > 0:
                candidates.append(
                    (offset, offset + len(line), stripped, number, len(candidates))
                )
        offset += len(line)
    if len(candidates) < 3:
        return []

    def chain_score(chain: list[int]) -> tuple[int, int, int]:
        dot_count = sum(
            bool(re.match(rf"^[{_CN_NUM}]+\.", candidates[index][2]))
            for index in chain
        )
        return len(chain), dot_count, -candidates[chain[0]][0]

    best_from: dict[int, list[int]] = {}
    for index in range(len(candidates) - 1, -1, -1):
        best = [index]
        for following in range(index + 1, len(candidates)):
            if candidates[following][3] <= candidates[index][3]:
                continue
            chain = [index, *best_from[following]]
            if chain_score(chain) > chain_score(best):
                best = chain
        best_from[index] = best

    chains = [best_from[index] for index, item in enumerate(candidates) if item[3] == 1]
    if not chains:
        return []
    chain = max(chains, key=chain_score)
    if len(chain) < 3:
        return []
    return [
        StructuralHeading(
            start=candidates[index][0],
            end=candidates[index][1],
            title=candidates[index][2],
            heading_type="story",
        )
        for index in chain
    ]


def _heading_identity(title: str) -> str:
    return re.sub(r"[\s《》〈〉\[\]【】:：-]+", "", title).lower()


def parse_chapters(
    text: str,
    custom_regex: str | None = None,
    *,
    book_title: str | None = None,
    extra_headings: list[StructuralHeading] | None = None,
) -> ParseOutcome:
    """解析章节；AI 只提供经过调用方校验的额外标题，不能改写原文。"""
    custom = compile_custom_regex(custom_regex)
    warnings: list[str] = []
    headings: list[tuple[int, int, str, str, int]] = []

    def add_heading(
        start: int,
        end: int,
        raw_title: str,
        heading_type: str,
        volume_number: int,
    ) -> None:
        title = normalize_chapter_title(raw_title, book_title=book_title)[:255]
        if not title:
            warnings.append(f"标题「{raw_title.strip()[:60]}」规范化后为空，已忽略。")
            return
        headings.append((start, end, title, heading_type, volume_number))

    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if _is_volume_heading(line, custom, book_title):
            add_heading(offset, offset + len(line), stripped, "volume", 0)
        elif _is_heading(line, custom, book_title):
            title = stripped
            if custom is not None:
                match = custom.match(stripped)
                if match is not None and match.lastindex:
                    grouped = (match.group(1) or "").strip()
                    if grouped:
                        title = grouped
            add_heading(offset, offset + len(line), title, "chapter", 0)
        offset += len(line)
    if offset < len(text):
        tail = text[offset:]
        is_volume = _is_volume_heading(tail, custom, book_title)
        if is_volume or _is_heading(tail, custom, book_title):
            add_heading(offset, len(text), tail.strip(), "volume" if is_volume else "chapter", 0)

    # 编号式标题用最长递增序列兜底；自定义正则优先，避免用户规则被覆盖。
    if custom is None and not any(
        item[3] == "chapter" and classify_chapter(item[2])[0] == "story"
        for item in headings
    ):
        for item in numbered_heading_fallback(text):
            add_heading(item.start, item.end, item.title, item.heading_type, item.volume_number)
    # AI 结果必须定位到原文行首，并且不能与规则标题重复；自定义正则优先。
    for item in ([] if custom is not None else extra_headings or []):
        if not (0 <= item.start < item.end <= len(text)):
            continue
        source_title = text[item.start:item.end].strip()
        if source_title != item.title.strip():
            source_normalized = normalize_chapter_title(source_title, book_title=book_title)
            item_normalized = normalize_chapter_title(item.title, book_title=book_title)
            if not source_normalized or source_normalized != item_normalized:
                continue
        if any(existing[0] == item.start for existing in headings):
            continue
        add_heading(item.start, item.end, item.title, item.heading_type, item.volume_number)
    headings.sort(key=lambda item: item[0])

    chapter_headings = [item for item in headings if item[3] != "volume"]
    if not chapter_headings:
        warnings.append("未识别到章节标题，已作为单章导入；可在工作区手动拆分。")
        kind, included = classify_chapter("全文")
        return ParseOutcome(
            chapters=[ChapterSlice(
                title="全文", start=0, end=len(text), heading=None,
                chapter_kind=kind, analysis_included=included,
            )],
            warnings=warnings,
            used_custom_regex=custom is not None,
        )

    chapters: list[ChapterSlice] = []
    first_chapter_start = chapter_headings[0][0]
    volume_number = 0
    volume_title = "默认卷"
    prefix_chapters: list[ChapterSlice] = []
    prefix_cursor = 0
    prefix_volume_line_end = 0
    prefix_has_volume = False
    first_title = _heading_identity(chapter_headings[0][2])
    if (
        first_chapter_start > 0
        and not any(item[3] == "volume" and item[0] < first_chapter_start for item in headings)
        and first_title in _PREFACE_TITLES
    ):
        # 下载站声明、书名和作者位于首个“前言”之前时并入该章，避免重复前言。
        first_chapter_start = 0
    for item in headings:
        if item[0] >= first_chapter_start:
            break
        if item[3] != "volume":
            continue
        prefix_end = item[0]
        if text[prefix_cursor:prefix_end].strip():
            kind, included = classify_chapter("卷首语" if prefix_has_volume else "前言")
            prefix_chapters.append(ChapterSlice(
                title="卷首语" if prefix_has_volume else "前言",
                start=prefix_cursor,
                end=prefix_end,
                heading=None,
                volume_number=volume_number,
                volume_title=volume_title,
                chapter_kind=kind,
                analysis_included=included,
            ))
        volume_number = item[4] or _volume_number(item[2])
        volume_title = item[2]
        prefix_has_volume = True
        # 卷标题归入紧随其后的卷首语/首章，保证与原文偏移无缝衔接。
        prefix_cursor = item[0]
        prefix_volume_line_end = item[1]
    prefix_content_start = prefix_volume_line_end if prefix_has_volume else prefix_cursor
    if text[prefix_content_start:first_chapter_start].strip():
        kind, included = classify_chapter("卷首语" if prefix_has_volume else "前言")
        prefix_chapters.append(ChapterSlice(
            title="卷首语" if prefix_has_volume else "前言",
            start=prefix_cursor,
            end=first_chapter_start,
            heading=None,
            volume_number=volume_number,
            volume_title=volume_title,
            chapter_kind=kind,
            analysis_included=included,
        ))
    chapters.extend(prefix_chapters)
    if prefix_chapters:
        warnings.append("首个章节标题前存在正文，已归入“前言”或“卷首语”。")
    prefix_covered_end = prefix_chapters[-1].end if prefix_chapters else 0

    for index, item in enumerate(headings):
        start, _line_end, title, heading_type, explicit_volume_number = item
        if heading_type == "volume":
            volume_number = explicit_volume_number or _volume_number(title)
            volume_title = title
            continue
        if index > 0 and headings[index - 1][3] == "volume":
            start = max(headings[index - 1][0], prefix_covered_end)
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        if index + 1 < len(headings) and headings[index + 1][3] == "volume":
            end = headings[index + 1][0]
        if not text[start:end].strip():
            warnings.append(f"章节「{title}」没有正文内容。")
        kind, included = classify_chapter(title)
        if heading_type in {"extra", "author_note"}:
            kind, included = heading_type, False
        chapters.append(ChapterSlice(
            title=title or "未命名章节", start=start, end=end, heading=title,
            volume_number=volume_number, volume_title=volume_title,
            chapter_kind=kind, analysis_included=included,
        ))

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