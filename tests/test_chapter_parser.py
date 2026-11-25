from __future__ import annotations

import pytest

from novelvoice_studio.core.errors import ValidationError
from novelvoice_studio.services.chapters import parse_chapters, split_paragraphs


def test_parse_sample_titles(sample_text: str) -> None:
    outcome = parse_chapters(sample_text)
    assert [item.title for item in outcome.chapters] == [
        "前言",
        "第一章 雨夜来客",
        "第二章 旧照片",
        "第三章 灯下",
    ]
    assert outcome.chapters[0].start == 0
    assert outcome.chapters[-1].end == len(sample_text)
    for previous, current in zip(outcome.chapters, outcome.chapters[1:]):
        assert previous.end == current.start


def test_heading_false_positive_rejected() -> None:
    text = "前言内容并不是标题\n\n这里只是普通正文。\n"
    outcome = parse_chapters(text)
    assert len(outcome.chapters) == 1
    assert outcome.chapters[0].title == "全文"
    assert any("未识别到章节标题" in warning for warning in outcome.warnings)


def test_preface_before_first_heading() -> None:
    text = "这是一段卷首语。\n\n第一章 开始\n\n正文。\n"
    outcome = parse_chapters(text)
    assert [item.title for item in outcome.chapters] == ["前言", "第一章 开始"]
    assert outcome.chapters[0].start == 0
    assert text[outcome.chapters[0].start : outcome.chapters[0].end].strip() == "这是一段卷首语。"


def test_fallback_single_chapter() -> None:
    text = "没有标题的第一段。\n\n没有标题的第二段。\n"
    outcome = parse_chapters(text)
    assert len(outcome.chapters) == 1
    assert outcome.chapters[0].start == 0
    assert outcome.chapters[0].end == len(text)


def test_custom_regex_uses_group() -> None:
    text = "=== 第一幕 ===\n\n甲说话。\n\n=== 第二幕 ===\n\n乙说话。\n"
    outcome = parse_chapters(text, custom_regex=r"^=== (.+) ===$")
    assert [item.title for item in outcome.chapters] == ["第一幕", "第二幕"]
    assert outcome.used_custom_regex is True
    assert any("自定义章节正则" in warning for warning in outcome.warnings)


def test_invalid_custom_regex() -> None:
    with pytest.raises(ValidationError):
        parse_chapters("正文", custom_regex="(")


def test_paragraphs_blank_line_mode(sample_text: str) -> None:
    outcome = parse_chapters(sample_text)
    chapter = outcome.chapters[1]
    paragraphs = split_paragraphs(sample_text, chapter.start, chapter.end)
    assert len(paragraphs) >= 3
    for index, paragraph in enumerate(paragraphs):
        assert paragraph.index == index
        assert sample_text[paragraph.start : paragraph.end] == paragraph.text
        assert paragraph.text.strip() == paragraph.text


def test_paragraphs_line_mode() -> None:
    text = "第一章 开始\n第一行\n第二行\n第三行\n"
    outcome = parse_chapters(text)
    chapter = outcome.chapters[0]
    paragraphs = split_paragraphs(text, chapter.start, chapter.end)
    assert [item.text for item in paragraphs] == ["第一章 开始", "第一行", "第二行", "第三行"]


def test_paragraphs_long_line_single_block() -> None:
    long_line = "很长的一行" * 80
    text = f"第一章 开始\n{long_line}\n"
    outcome = parse_chapters(text)
    chapter = outcome.chapters[0]
    paragraphs = split_paragraphs(text, chapter.start, chapter.end)
    assert len(paragraphs) == 2
    assert paragraphs[0].text == "第一章 开始"
    assert paragraphs[1].text == long_line