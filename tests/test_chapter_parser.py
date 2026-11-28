from __future__ import annotations

import pytest

from novelvoice_studio.core.errors import ValidationError
from novelvoice_studio.services.book_metadata import extract_book_metadata
from novelvoice_studio.services.chapters import (
    StructuralHeading,
    normalize_chapter_title,
    parse_chapters,
    split_paragraphs,
)


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


@pytest.mark.parametrize(
    ("raw", "expected", "book_title"),
    [
        ("### 第一章 雨夜 ###", "第一章 雨夜", None),
        (" \t> ## 第二章 旧照片", "第二章 旧照片", None),
        ("- * 第三章 灯下", "第三章 灯下", None),
        ("# C# 编程", "C# 编程", None),
        ("书名：巷口旧邮筒 第一章 灰漆", "第一章 灰漆", "巷口旧邮筒"),
        ("小说名：巷口旧邮筒 第一章 灰漆", "第一章 灰漆", "巷口旧邮筒"),
        ("作品名：巷口旧邮筒", "巷口旧邮筒", None),
        ("【书名：巷口旧邮筒】第一章 灰漆", "第一章 灰漆", None),
        ("《书名：巷口旧邮筒》第一章 灰漆", "第一章 灰漆", None),
        ("《巷口旧邮筒》第一章 灰漆", "第一章 灰漆", "巷口旧邮筒"),
        ("《巷口旧邮筒》", "《巷口旧邮筒》", "巷口旧邮筒"),
        ("《巷口旧邮筒》灰漆剥落", "《巷口旧邮筒》灰漆剥落", "巷口旧邮筒"),
        ("第一章 雨夜 https://example.com/novel/12", "第一章 雨夜", None),
        ("第一章 雨夜，来源：www.example.com/novel", "第一章 雨夜", None),
        ("第一章 雨夜（笔趣阁 www.biquge.com）", "第一章 雨夜", None),
        ("第一章 雨夜——最新章节，敬请期待", "第一章 雨夜", None),
        ("第一章 雨夜 TXT全集下载", "第一章 雨夜", None),
        ("第一章 雨夜 全集TXT下载", "第一章 雨夜", None),
        ("第一章 雨夜 小说全文阅读", "第一章 雨夜", None),
        ("第一章 雨夜（下载站提供）", "第一章 雨夜", None),
        ("第一章 雨夜 请点击下一页继续阅读", "第一章 雨夜", None),
        ("https://example.com", "", None),
        ("example.com", "", None),
        ("###", "", None),
        ("第一章 第一章 雨夜", "第一章 雨夜", None),
        ("第1章 第一章 雨夜", "第1章 雨夜", None),
        ("第一章第一章雨夜", "第一章雨夜", None),
        ("1. 1. 雨夜", "1. 雨夜", None),
        ("Chapter 1 Chapter 1 Rain", "Chapter 1 Rain", None),
        ("第一卷 第一卷 风起", "第一卷 风起", None),
        ("第一卷 第一章 风起", "第一卷 第一章 风起", None),
        ("第一部 第一章 风起", "第一部 第一章 风起", None),
        ("第十一章 十二章", "第十一章 十二章", None),
        ("第一章 雨夜：重逢（上）", "第一章 雨夜：重逢（上）", None),
        ("1.疯狂年代", "1.疯狂年代", None),
        ("第一章 下载器之谜", "第一章 下载器之谜", None),
        ("第一章 免费阅读券", "第一章 免费阅读券", None),
        ("第一章 广告人", "第一章 广告人", None),
        ("第一章 广告", "第一章 广告", None),
        ("第一章 广告信息", "第一章", None),
        ("第一章 笔趣阁往事", "第一章 笔趣阁往事", None),
        ("普通标题", "普通标题", None),
    ],
)
def test_normalize_chapter_title(raw: str, expected: str, book_title: str | None) -> None:
    normalized = normalize_chapter_title(raw, book_title=book_title)
    assert normalized == expected
    assert normalize_chapter_title(normalized, book_title=book_title) == normalized


def test_parse_normalizes_markdown_rules_and_volumes_without_changing_offsets() -> None:
    text = (
        "## 第一卷 风起\n\n"
        "### 第一章 雨夜 ###\n\n正文一。\n\n"
        "## 第二章 旧照片\n\n正文二。\n"
    )
    outcome = parse_chapters(text)
    assert [item.title for item in outcome.chapters] == ["第一章 雨夜", "第二章 旧照片"]
    assert [item.heading for item in outcome.chapters] == ["第一章 雨夜", "第二章 旧照片"]
    assert [item.volume_title for item in outcome.chapters] == ["第一卷 风起", "第一卷 风起"]
    # 卷标题仍按既有规则归入首章，规范化只改显示标题。
    assert outcome.chapters[0].start == 0
    assert outcome.chapters[1].start == text.index("## 第二章")
    assert outcome.chapters[-1].end == len(text)
    assert outcome.chapters[0].end == outcome.chapters[1].start


def test_parse_normalizes_ai_and_numbered_headings() -> None:
    text = (
        "第一幕 相遇\n正文一。\n\n"
        "第二幕 告别\n正文二。\n\n"
        "第一章第一章 雨夜\n正文三。\n"
    )
    first = text.index("第一幕 相遇")
    second = text.index("第二幕 告别")
    outcome = parse_chapters(text, extra_headings=[
        StructuralHeading(start=first, end=first + len("第一幕 相遇"), title="第一幕 相遇"),
        StructuralHeading(
            start=second,
            end=second + len("第二幕 告别"),
            title="# 第二幕 告别——最新章节",
        ),
    ])
    assert [item.title for item in outcome.chapters] == [
        "第一幕 相遇", "第二幕 告别", "第一章 雨夜",
    ]
    assert all(item.title == item.heading for item in outcome.chapters)


def test_parse_uses_book_title_for_prefixed_markdown_heading() -> None:
    text = "《雾港来信》第一章 雨夜\n正文。\n"
    outcome = parse_chapters(text, book_title="雾港来信")
    assert [item.title for item in outcome.chapters] == ["第一章 雨夜"]
    assert outcome.chapters[0].start == 0
    assert outcome.chapters[0].end == len(text)


def test_parse_skips_titles_that_normalize_to_empty() -> None:
    text = "# 第一章 正常\n正文一。\n#\n噪声行。\n# 第二章 继续\n正文二。\n"
    outcome = parse_chapters(text, custom_regex=r"^#\s*(.*)$")
    assert [item.title for item in outcome.chapters] == ["第一章 正常", "第二章 继续"]
    assert outcome.chapters[0].end == text.index("# 第二章")
    assert "噪声行。" in text[outcome.chapters[0].start:outcome.chapters[0].end]
    assert any("规范化后为空" in warning for warning in outcome.warnings)

    fallback = parse_chapters("###\n只有正文。\n")
    assert [item.title for item in fallback.chapters] == ["全文"]
    assert fallback.chapters[0].start == 0
    assert fallback.chapters[0].end == len("###\n只有正文。\n")


def test_numbered_chapters_do_not_swallow_body_lists() -> None:
    text = (
        "本站作品仅供试阅。\n==================================\n《三体》\n作者：刘慈欣\n\n"
        "前言\n前言正文。\n\n1.疯狂年代\n正文一。\n"
        "1、物理学：【略】\n2、生物学：【略】\n"
        "2.寂静的春天\n正文二。\n3.红岸之一\n正文三。\n"
        "1、总纲【提要】\n后记\n后记正文。\n"
    )
    outcome = parse_chapters(text)
    assert [item.title for item in outcome.chapters] == [
        "前言", "1.疯狂年代", "2.寂静的春天", "3.红岸之一", "后记",
    ]
    assert sum(item.title == "前言" for item in outcome.chapters) == 1
    assert "1、物理学" in text[outcome.chapters[1].start:outcome.chapters[1].end]
    assert all(previous.end == current.start for previous, current in zip(outcome.chapters, outcome.chapters[1:]))


def test_ai_heading_must_match_original_line() -> None:
    text = "开头正文。\n\n第一幕 相遇\n\n甲说话。\n\n第二幕 告别\n\n乙说话。\n"
    start = text.index("第一幕 相遇")
    second = text.index("第二幕 告别")
    outcome = parse_chapters(text, extra_headings=[
        StructuralHeading(start=start, end=start + len("第一幕 相遇"), title="第一幕 相遇"),
        StructuralHeading(start=second, end=second + len("第二幕 告别"), title="第二幕 告别"),
        StructuralHeading(start=0, end=2, title="伪造标题"),
    ])
    assert [item.title for item in outcome.chapters] == ["前言", "第一幕 相遇", "第二幕 告别"]
    assert outcome.chapters[0].start == 0


def test_metadata_ignores_site_separator_and_reads_book_brackets() -> None:
    text = "本站作品仅供试阅。\n==================================\n《三体》\n作者：刘慈欣\n\n第一章 开始\n正文。\n"
    metadata = extract_book_metadata(text, "下载器abc.txt")
    assert metadata.title == "三体"
    assert metadata.author == "刘慈欣"
    assert metadata.title_source == "content_plain"


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


def test_volume_structure_and_special_chapters() -> None:
    text = (
        "第一卷 风起\n\n第一章 开始\n\n正文一。\n\n"
        "第二章 继续\n\n正文二。\n\n"
        "第二卷 云涌\n\n第一章 新篇\n\n正文三。\n\n"
        "番外 旧事\n\n番外正文。\n\n请假\n\n休息一天。\n"
    )
    outcome = parse_chapters(text)
    assert [item.title for item in outcome.chapters] == [
        "第一章 开始", "第二章 继续", "第一章 新篇", "番外 旧事", "请假",
    ]
    assert [item.volume_number for item in outcome.chapters] == [1, 1, 2, 2, 2]
    assert [item.volume_title for item in outcome.chapters[:3]] == [
        "第一卷 风起", "第一卷 风起", "第二卷 云涌",
    ]
    assert outcome.chapters[0].start == 0
    assert outcome.chapters[-1].end == len(text)
    assert all(previous.end == current.start for previous, current in zip(
        outcome.chapters, outcome.chapters[1:]
    ))
    assert outcome.chapters[-2].chapter_kind == "extra"
    assert outcome.chapters[-2].analysis_included is False
    assert outcome.chapters[-1].chapter_kind == "author_note"
    assert outcome.chapters[-1].analysis_included is False


def test_volume_preface_inherits_volume_and_keeps_offsets() -> None:
    text = "第一卷 风起\n\n卷首语内容。\n\n第一章 开始\n\n正文。\n"
    outcome = parse_chapters(text)
    assert [item.title for item in outcome.chapters] == ["卷首语", "第一章 开始"]
    assert outcome.chapters[0].volume_number == 1
    assert outcome.chapters[0].volume_title == "第一卷 风起"
    assert outcome.chapters[0].analysis_included is False
    assert outcome.chapters[0].start == 0
    assert outcome.chapters[0].end == outcome.chapters[1].start
    assert outcome.chapters[-1].end == len(text)