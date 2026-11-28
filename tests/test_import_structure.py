from __future__ import annotations

from types import SimpleNamespace

from novelvoice_studio.ai.base import (
    ImportHeadingDraft,
    ImportMetadataDraft,
    ImportStructureResult,
)
from novelvoice_studio.services.import_structure import analyze_import


class FakeLLM:
    name = "fake-local"
    model = "test-model"
    is_mock = False

    def __init__(self, result: ImportStructureResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def inspect_import(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result or ImportStructureResult()


def _registry(llm: FakeLLM):  # type: ignore[no-untyped-def]
    return SimpleNamespace(llm=llm)


def test_ai_adds_nonstandard_heading_and_metadata() -> None:
    text = (
        "本站作品仅供试阅。\n《雾港来信》\n作者：测试作者\n一封没有寄出的信。\n\n"
        "第一幕 雾\n正文一。\n第二幕 灯\n正文二。\n"
    )
    heading_lines = [
        index for index, line in enumerate(text.splitlines(), 1)
        if line in {"第一幕 雾", "第二幕 灯"}
    ]
    llm = FakeLLM(ImportStructureResult(
        headings=[
            ImportHeadingDraft(
                line_number=line,
                heading_type="extra" if line == heading_lines[-1] else "story",
                confidence=0.9,
            )
            for line in heading_lines
        ],
        metadata=ImportMetadataDraft(
            title="雾港来信",
            author="测试作者",
            description="一封没有寄出的信。",
            confidence=0.9,
        ),
    ))
    analysis = analyze_import(_registry(llm), text, "下载器数字.txt")
    assert analysis.ai_assisted is True
    assert analysis.structure_source == "ai"
    assert [chapter.title for chapter in analysis.parsed.chapters] == ["前言", "第一幕 雾", "第二幕 灯"]
    assert analysis.parsed.chapters[-1].chapter_kind == "extra"
    assert analysis.parsed.chapters[-1].analysis_included is False
    assert analysis.metadata.title == "雾港来信"
    assert analysis.metadata.author == "测试作者"
    assert analysis.metadata.description == "一封没有寄出的信。"
    assert analysis.metadata.title_source == "content_plain"
    assert analysis.metadata.description_source == "ai_content"
    assert llm.calls == 1


def test_ai_titles_are_normalized_and_grounded_in_original_lines() -> None:
    text = (
        "# 书名：雾港来信\n\n"
        "## 《雾港来信》第一章 雨夜——最新章节\n正文一。\n"
        "## 第二幕 灯（笔趣阁 www.biquge.com）\n正文二。\n"
    )
    heading_lines = [
        index for index, line in enumerate(text.splitlines(), 1)
        if line.startswith("## ")
    ]
    llm = FakeLLM(ImportStructureResult(headings=[
        ImportHeadingDraft(line_number=line, heading_type="story", confidence=0.9)
        for line in heading_lines
    ]))
    analysis = analyze_import(_registry(llm), text, "下载器数字.txt")
    assert analysis.ai_assisted is True
    assert [chapter.title for chapter in analysis.parsed.chapters] == [
        "前言", "第一章 雨夜", "第二幕 灯",
    ]
    assert [chapter.heading for chapter in analysis.parsed.chapters[1:]] == [
        "第一章 雨夜", "第二幕 灯",
    ]
    assert analysis.parsed.chapters[1].start == text.index("## 《雾港来信》")
    assert analysis.parsed.chapters[-1].end == len(text)


def test_ai_invalid_line_is_ignored_and_result_is_cached() -> None:
    text = "第一幕 相遇\n正文一。\n第二幕 告别\n正文二。\n"
    llm = FakeLLM(ImportStructureResult(headings=[
        ImportHeadingDraft(line_number=1),
        ImportHeadingDraft(line_number=3),
        ImportHeadingDraft(line_number=999),
    ]))
    first = analyze_import(_registry(llm), text, "唯一缓存测试.txt")
    second = analyze_import(_registry(llm), text, "唯一缓存测试.txt")
    assert first is second
    assert llm.calls == 1
    assert [chapter.title for chapter in first.parsed.chapters] == ["第一幕 相遇", "第二幕 告别"]
    assert any("无效标题位置" in warning for warning in first.warnings)


def test_ai_failure_falls_back_without_blocking_import() -> None:
    text = "第一章 开始\n正文一。\n第二章 继续\n正文二。\n"
    llm = FakeLLM(error=RuntimeError("offline"))
    analysis = analyze_import(_registry(llm), text, "离线回退.txt")
    assert analysis.ai_assisted is False
    assert analysis.structure_source == "rules"
    assert len(analysis.parsed.chapters) == 2
    assert any("规则回退" in warning for warning in analysis.warnings)


def test_custom_regex_does_not_call_ai() -> None:
    text = "=== 第一幕 ===\n正文一。\n=== 第二幕 ===\n正文二。\n"
    llm = FakeLLM()
    analysis = analyze_import(_registry(llm), text, "自定义.txt", custom_regex=r"^=== (.+) ===$")
    assert analysis.structure_source == "custom"
    assert llm.calls == 0
    assert [chapter.title for chapter in analysis.parsed.chapters] == ["第一幕", "第二幕"]
