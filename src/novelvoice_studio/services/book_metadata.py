"""从书籍开头提取书名、作者与简介，并保守清洗文件名。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

_FRONT_MATTER_LIMIT = 16_000
# 简介只保留短篇内容简介；按字符计数（含标点和换行），避免整章正文被误收。
_DESCRIPTION_MAX_CHARS = 180
_TITLE_LABEL = re.compile(r"^(?:书名|作品名|小说名|title)\s*[：:]\s*(.+?)\s*$", re.I)
_AUTHOR_LABEL = re.compile(r"^(?:作者|著者|作者名|author)\s*[：:]\s*(.+?)\s*$", re.I)
_DESCRIPTION_LABEL = re.compile(
    r"^(?:内容简介|作品简介|书籍简介|简介|文案|description)\s*[：:]\s*(.*?)\s*$",
    re.I,
)
_OTHER_LABEL = re.compile(
    r"^(?:书名|作品名|小说名|作者|著者|作者名|标签|分类|状态|字数|更新时间|title|author)\s*[：:]",
    re.I,
)
_CHAPTER_LIKE = re.compile(
    r"^(?:第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章节回部篇集]|"
    r"chapter\s+[\divxlcdm]+|序章|楔子|番外)",
    re.I,
)
_DESCRIPTION_SEPARATOR = re.compile(
    r"(?:[=＝]{4,}|[-－—–_]{4,}|[═─]{4,}|\*{4,})"
)
_DESCRIPTION_NOISE_BOUNDARY = re.compile(
    r"(?:^|[。！？!?；;]\s*)(?P<marker>[【\[(（]?\s*(?:"
    r"版权(?:信息|声明)?|版权所有|著作权(?:信息|声明)?|侵权必究|"
    r"(?:出版社|出版时间|出版日期|发行方|授权单位)(?:\s*[：:]|\s+|$)|"
    r"(?:(?:简体|繁体|网络)?出版已签约|出版签约|签约出版|签约宣传)|"
    r"正文开始|正文如下|以下正文|正文从这里开始|"
    r"第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章节回部篇集]|"
    r"chapter\s+[\divxlcdm]+|序章|楔子|番外"
    r")\s*[】\])）]?)",
    re.I,
)
_DESCRIPTION_PROMOTION_PREFIX = re.compile(
    r"^\s*[【\[(（]?\s*(?:(?:简体|繁体|网络)?出版已签约|"
    r"出版签约|签约出版|版权合作|出版招商)\s*[】\])）]?\s*",
    re.I,
)
_SENTENCE_END = re.compile(r"[。！？!?][”’\"'」』】）)]*")
_SITE_TOKEN = re.compile(
    r"(?:https?://|www\.)\S+|\b(?:txt|TXT|下载|全文阅读|最新章节|免费阅读)\b",
    re.I,
)
_CHAPTER_SUFFIX = re.compile(
    r"(?:[（(\[]?\s*第\s*[0-9零〇一二三四五六七八九十百千万两\-—–至到]+\s*[章节回]\s*[）)\]]?.*$)",
    re.I,
)
_BRACKETS = re.compile(r"[【\[(（][^】\])）]{0,40}[】\])）]")


@dataclass(frozen=True)
class BookMetadata:
    title: str | None
    author: str | None
    description: str | None
    title_source: str
    author_source: str | None
    description_source: str | None
    confidence: float
    warnings: list[str] = field(default_factory=list)


def _clean_inline(value: str, max_length: int) -> str | None:
    cleaned = " ".join(value.replace("\x00", " ").split()).strip(" \t\"'《》<>")
    if not cleaned:
        return None
    return cleaned[:max_length]


def _front_matter(text: str, first_chapter_start: int | None) -> str:
    limit = _FRONT_MATTER_LIMIT
    if first_chapter_start is not None and first_chapter_start > 0:
        limit = min(limit, first_chapter_start)
    return text[:limit]


def _description_fragment(line: str) -> tuple[str, bool, bool]:
    """返回（原文片段, 是否仅为宣传头, 是否在此停止）；不整理正常标点。"""
    if not line.strip():
        return "", False, True

    # “【简体出版已签约】”常单独占一行，其后才是真正的简介。
    promotion = _DESCRIPTION_PROMOTION_PREFIX.match(line)
    if promotion is not None:
        line = line[promotion.end():]
        if not line.strip():
            return "", True, False

    boundaries: list[int] = []
    separator = _DESCRIPTION_SEPARATOR.search(line)
    if separator is not None:
        boundaries.append(separator.start())
    noise = _DESCRIPTION_NOISE_BOUNDARY.search(line)
    if noise is not None:
        boundaries.append(noise.start("marker"))
    if boundaries:
        return line[:min(boundaries)], False, True

    stripped = line.strip()
    if _OTHER_LABEL.match(stripped) or _CHAPTER_LIKE.match(stripped):
        return "", False, True
    return line, False, False


def _truncate_description(description: str) -> str | None:
    description = description.strip()
    if not description:
        return None
    if len(description) <= _DESCRIPTION_MAX_CHARS:
        return description

    prefix = description[:_DESCRIPTION_MAX_CHARS]
    sentence_ends = [
        match.end()
        for match in _SENTENCE_END.finditer(prefix)
        if match.end() <= _DESCRIPTION_MAX_CHARS
    ]
    # 上限内没有完整句时宁缺毋滥，避免返回半句话。
    if not sentence_ends:
        return None
    return prefix[:sentence_ends[-1]] or None


def _extract_description(lines: list[str], start: int) -> tuple[str | None, int]:
    first = _DESCRIPTION_LABEL.match(lines[start].strip())
    if first is None:
        return None, start
    parts: list[str] = []
    stop = False
    if first.group(1):
        fragment, _, stop = _description_fragment(first.group(1))
        if fragment:
            parts.append(fragment)
    cursor = start + 1
    while cursor < len(lines) and not stop:
        fragment, promotion_only, stop = _description_fragment(lines[cursor])
        cursor += 1
        if fragment:
            parts.append(fragment)
        if stop:
            break
        if promotion_only:
            if parts:
                break
            continue
        if len("\n".join(parts)) >= _DESCRIPTION_MAX_CHARS:
            break
    description = _truncate_description("\n".join(parts))
    return description, cursor


def clean_filename_title(filename: str) -> tuple[str | None, float]:
    """把下载器文件名转成可用书名；无法确认时返回较低置信度。"""

    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if not name:
        return None, 0.0
    name = re.sub(r"\.(?:txt|text)$", "", name, flags=re.I).strip()
    name = _SITE_TOKEN.sub(" ", name)
    name = _BRACKETS.sub(" ", name)
    name = _CHAPTER_SUFFIX.sub("", name)
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -_—–:：")
    if not name:
        return None, 0.0
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", name))
    if not has_cjk:
        return None, 0.0
    if len(name) > 80:
        return None, 0.0
    return name[:255], 0.55


def extract_book_metadata(
    text: str,
    filename: str,
    *,
    first_chapter_start: int | None = None,
) -> BookMetadata:
    front = _front_matter(text, first_chapter_start)
    # 提取简介依赖空行作为边界，不能预先过滤。
    lines = front.splitlines()
    warnings: list[str] = []

    title: str | None = None
    title_source = "filename"
    author: str | None = None
    author_source: str | None = None
    description: str | None = None
    description_source: str | None = None

    for index, line in enumerate(lines):
        stripped = line.strip()
        title_match = _TITLE_LABEL.match(stripped)
        if title_match and title is None:
            title = _clean_inline(title_match.group(1), 255)
            if title:
                title_source = "content_label"
        author_match = _AUTHOR_LABEL.match(stripped)
        if author_match and author is None:
            author = _clean_inline(author_match.group(1), 255)
            if author:
                author_source = "content_label"
        description_match = _DESCRIPTION_LABEL.match(stripped)
        if description_match and description is None:
            description, _ = _extract_description(lines, index)
            if description:
                description_source = "content_label"

    if title is None:
        nonempty_lines = [line for line in lines if line.strip()]
        for line in nonempty_lines[:12]:
            stripped = line.strip()
            if _OTHER_LABEL.match(stripped) or _CHAPTER_LIKE.match(stripped):
                continue
            # 下载站声明与分隔线不是书名；《书名》是可靠的正文书名行。
            if _DESCRIPTION_SEPARATOR.fullmatch(stripped) or _SITE_TOKEN.search(stripped):
                continue
            if re.search(r"(?:下载站|全文阅读|最新章节|免费阅读|本书来自|本站作品|txt小说)", stripped, re.I):
                continue
            candidate = _clean_inline(stripped, 255)
            if not candidate or len(candidate) > 60:
                continue
            if re.search(r"[。！？!?；;]", candidate):
                continue
            if candidate.startswith(("作者", "简介", "书名")):
                continue
            title = candidate
            title_source = "content_plain"
            break

    filename_title, filename_confidence = clean_filename_title(filename)
    if title is None:
        title = filename_title
        title_source = "filename"
        if title is None:
            warnings.append("未能从正文开头或文件名可靠识别书名，请手动填写。")

    confidence = 0.0
    if title_source == "content_label":
        confidence = 0.95
    elif title_source == "content_plain":
        confidence = 0.78
    elif title_source == "filename":
        confidence = filename_confidence

    return BookMetadata(
        title=title,
        author=author,
        description=description,
        title_source=title_source,
        author_source=author_source,
        description_source=description_source,
        confidence=confidence,
        warnings=warnings,
    )
