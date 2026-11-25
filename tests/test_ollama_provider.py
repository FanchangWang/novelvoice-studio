from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from novelvoice_studio.ai.ollama import OllamaLLMProvider, ProviderResponseError
from novelvoice_studio.core.errors import DependencyUnavailableError


def _chat_body(content: str) -> dict:
    return {
        "message": {"role": "assistant", "content": content},
        "prompt_eval_count": 100,
        "eval_count": 50,
        "done_reason": "stop",
        "total_duration": 123456,
    }


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    json_retries: int = 2,
) -> OllamaLLMProvider:
    provider = OllamaLLMProvider(
        base_url="http://127.0.0.1:11434",
        model="test-model",
        context_length=4096,
        keep_alive="1m",
        timeout_seconds=5,
        temperature=0.1,
        json_retries=json_retries,
    )
    transport = httpx.MockTransport(handler)

    def client() -> httpx.Client:
        return httpx.Client(
            base_url=provider.base_url,
            timeout=httpx.Timeout(5.0, connect=1.0),
            transport=transport,
        )

    provider._client = client  # type: ignore[method-assign]
    return provider


def test_structured_characters_request_and_meta() -> None:
    captured: list[dict] = []
    content = json.dumps(
        {
            "characters": [
                {
                    "name": "林见秋",
                    "aliases": [
                        {"name": "小秋", "confidence": 0.8, "basis": "explicit"}
                    ],
                    "gender": "female",
                    "gender_basis": "explicit",
                    "age_range": "young_adult",
                    "age_basis": "inferred",
                    "character_type": "human",
                    "identity_description": "旧书店老板",
                    "personality": ["冷静"],
                    "voice_description": "清冷女声",
                    "evidence": [{"quote": "林见秋把伞靠在门边"}],
                    "confidence": 0.9,
                    "is_provisional": False,
                }
            ],
            "relationships": [
                {
                    "source_name": "林见秋",
                    "target_name": "沈砚",
                    "relationship_type": "朋友",
                    "confidence": 0.7,
                }
            ],
        },
        ensure_ascii=False,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_chat_body(content))

    provider = _provider(handler)
    result = provider.analyze_characters(
        "林见秋把伞靠在门边。",
        [{"name": "林见秋", "aliases": ["小秋"]}],
        {"book_title": "测试书", "chapter_title": "第一章", "chapter_sequence": 1},
    )
    assert result.characters[0].name == "林见秋"
    assert result.characters[0].aliases[0].name == "小秋"
    assert result.relationships[0].source_name == "林见秋"
    assert result.meta["provider"] == "ollama"
    assert result.meta["model"] == "test-model"
    assert result.meta["prompt_version"] == "stage2.v1.characters"
    assert result.meta["attempts"] == 1
    assert result.meta["repaired"] is False
    assert result.meta["call"]["eval_count"] == 50

    payload = captured[0]
    assert payload["model"] == "test-model"
    assert payload["stream"] is False
    assert payload["think"] is False
    assert payload["format"] == "json"
    assert payload["keep_alive"] == "1m"
    assert payload["options"]["num_ctx"] == 4096
    assert payload["options"]["temperature"] == 0.1
    assert "<chapter>" in payload["messages"][1]["content"]
    assert "林见秋" in payload["messages"][1]["content"]


def test_json_repair_retry_and_fenced_json() -> None:
    contents = [
        "抱歉，我无法输出 JSON。",
        "```json\n" + json.dumps({"characters": [], "relationships": []}) + "\n```",
    ]
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_chat_body(contents[len(seen) - 1]))

    provider = _provider(handler)
    result = provider.analyze_characters("测试文本", [])
    assert result.characters == []
    assert result.meta["attempts"] == 2
    assert result.meta["repaired"] is True
    assert len(seen) == 2
    assert "JSON 修复器" in seen[1]["messages"][0]["content"]
    assert "无法输出 JSON" in seen[1]["messages"][1]["content"]
    assert result.meta["call"]["repair"]["eval_count"] == 50


def test_invalid_output_raises_502_after_retries() -> None:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json=_chat_body("not json at all"))

    provider = _provider(handler, json_retries=1)
    with pytest.raises(ProviderResponseError) as excinfo:
        provider.summarize_chapter("测试文本", [])
    error = excinfo.value
    assert error.status_code == 502
    assert error.code == "llm_invalid_response"
    assert error.details["task"] == "summary"
    assert error.details["prompt_version"] == "stage2.v1.summary"
    assert len(calls) == 2


def test_missing_model_raises_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="model not found")

    provider = _provider(handler)
    with pytest.raises(DependencyUnavailableError) as excinfo:
        provider.analyze_segments("文本", [])
    error = excinfo.value
    assert error.status_code == 503
    assert error.code == "dependency_unavailable"
    assert "ollama pull" in error.message
    assert error.details["model"] == "test-model"


def test_connection_error_raises_503() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(handler)
    with pytest.raises(DependencyUnavailableError) as excinfo:
        provider.analyze_characters("文本", [])
    assert "ollama serve" in excinfo.value.message
    assert excinfo.value.details["error"] == "ConnectError"


def test_health_check_reports_missing_model_without_pulling() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"models": [{"name": "other-model:latest"}]},
        )

    provider = _provider(handler)
    health = provider.health_check()
    assert health.ok is False
    assert "ollama pull test-model" in health.detail
    assert calls == ["/api/tags"]
    assert provider.list_models() == ["other-model:latest"]

    def installed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "test-model"}]})

    assert _provider(installed).health_check().ok is True


def test_segment_and_summary_structured_output() -> None:
    responses = [
        json.dumps(
            {
                "segments": [
                    {
                        "paragraph_index": 0,
                        "source_text": "林见秋说：“你好。”",
                        "segment_type": "dialogue",
                        "speaker_type": "character",
                        "speaker_name": "林见秋",
                        "speaker_confidence": 0.9,
                        "emotion": "tender",
                        "emotion_confidence": 0.8,
                        "intensity": 2,
                        "delivery": "normal",
                        "tts_instruction": "温柔地说",
                        "evidence": [{"quote": "林见秋说"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {"summary": "林见秋与来客交谈。", "key_events": ["来客上门"], "characters": ["林见秋"]},
            ensure_ascii=False,
        ),
    ]
    count = {"value": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = responses[min(count["value"], len(responses) - 1)]
        count["value"] += 1
        return httpx.Response(200, json=_chat_body(body))

    provider = _provider(handler)
    segments = provider.analyze_segments(
        "林见秋说：“你好。”",
        [{"name": "林见秋"}],
        {"paragraphs": [{"index": 0, "text": "林见秋说：“你好。”"}]},
    )
    assert segments.segments[0].source_text == "林见秋说：“你好。”"
    assert segments.meta["prompt_version"] == "stage2.v1.segments"

    summary = provider.summarize_chapter("林见秋说：“你好。”", [{"name": "林见秋"}])
    assert summary.summary == "林见秋与来客交谈。"
    assert summary.meta["prompt_version"] == "stage2.v1.summary"