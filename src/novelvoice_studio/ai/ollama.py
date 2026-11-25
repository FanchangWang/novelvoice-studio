"""本机 Ollama LLM Provider：严格 JSON 输出 + 有限修复重试。

安全边界：
- 只允许回环地址（配置层已校验）；
- 不自动下载模型，不调用云端 API；
- 关闭 httpx 环境代理（trust_env=False），避免小说原文经代理离开本机。
"""

from __future__ import annotations

import json
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from novelvoice_studio.ai.base import (
    BibleChapterResult,
    BibleMergeResult,
    BibleTimelineResult,
    ChapterSummaryResult,
    CharacterAnalysisResult,
    LLMProvider,
    ProviderHealth,
    SegmentAnalysisResult,
)
from novelvoice_studio.ai.prompts import (
    BIBLE_CHARACTER_PROMPT_VERSION,
    BIBLE_MERGE_PROMPT_VERSION,
    BIBLE_TIMELINE_PROMPT_VERSION,
    CHARACTER_PROMPT_VERSION,
    SEGMENT_PROMPT_VERSION,
    SUMMARY_PROMPT_VERSION,
    SCHEMAS,
    bible_character_messages,
    bible_merge_messages,
    bible_timeline_messages,
    character_messages,
    repair_messages,
    segment_messages,
    summary_messages,
)
from novelvoice_studio.core.errors import AppError, DependencyUnavailableError

TModel = TypeVar("TModel", bound=BaseModel)


class ProviderResponseError(AppError):
    """LLM 返回内容无法通过结构校验。"""

    status_code = 502
    code = "llm_invalid_response"


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("输出中没有 JSON 对象")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("JSON 顶层必须是对象")
    return payload


class OllamaLLMProvider(LLMProvider):
    name = "ollama"
    is_mock = False

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        context_length: int = 8192,
        keep_alive: str = "5m",
        timeout_seconds: float = 120.0,
        temperature: float = 0.2,
        json_retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.context_length = context_length
        self.keep_alive = keep_alive
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.json_retries = json_retries

    # ------------------------------------------------------------------ HTTP

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_seconds, connect=5.0),
            trust_env=False,
        )

    def _unavailable(self, message: str, exc: Exception | None = None) -> DependencyUnavailableError:
        details = {"base_url": self.base_url, "model": self.model}
        if exc is not None:
            details["error"] = type(exc).__name__
        return DependencyUnavailableError(message, details=details)

    def list_models(self) -> list[str]:
        try:
            with self._client() as client:
                response = client.get("/api/tags")
        except httpx.HTTPError as exc:
            raise self._unavailable(
                "无法连接本机 Ollama，请确认服务已在运行（ollama serve）", exc
            ) from exc
        if response.status_code >= 400:
            raise self._unavailable(f"Ollama 返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Ollama /api/tags 返回了非 JSON 内容") from exc
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            return []
        names: list[str] = []
        for item in models:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
        return names

    def health_check(self) -> ProviderHealth:
        try:
            models = self.list_models()
        except AppError as exc:
            return ProviderHealth(ok=False, detail=exc.message, meta=exc.details)
        if self.model in models:
            return ProviderHealth(
                ok=True,
                detail=f"Ollama 可用，已安装模型 {self.model}",
                meta={"model": self.model, "models": models},
            )
        return ProviderHealth(
            ok=False,
            detail=(
                f"Ollama 可用，但未安装模型 {self.model}；"
                f"请执行 `ollama pull {self.model}`（应用不会自动下载）"
            ),
            meta={"model": self.model, "models": models},
        )

    # ------------------------------------------------------------------ chat

    def _chat(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "format": "json",
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.context_length,
            },
        }
        started = time.perf_counter()
        try:
            with self._client() as client:
                response = client.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise self._unavailable(
                f"Ollama 请求超时（{self.timeout_seconds:.0f}s），可尝试拆分章节或调大 llm.timeout_seconds",
                exc,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._unavailable(
                "无法连接本机 Ollama，请确认服务已在运行（ollama serve）", exc
            ) from exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code == 404:
            raise self._unavailable(
                f"Ollama 中没有模型 {self.model}；请先执行 `ollama pull {self.model}`",
            )
        if response.status_code >= 400:
            snippet = response.text[:400]
            raise ProviderResponseError(
                f"Ollama 请求失败：HTTP {response.status_code}（{snippet}）",
                details={"response": snippet, "model": self.model},
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderResponseError("Ollama 返回了非 JSON 响应") from exc
        message = body.get("message") if isinstance(body, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ProviderResponseError(
                "Ollama 返回内容为空",
                details={"done_reason": body.get("done_reason") if isinstance(body, dict) else None},
            )
        meta = {
            "duration_ms": elapsed_ms,
            "prompt_eval_count": body.get("prompt_eval_count"),
            "eval_count": body.get("eval_count"),
            "done_reason": body.get("done_reason"),
            "total_duration_ns": body.get("total_duration"),
        }
        return content, meta

    def _structured(
        self,
        messages: list[dict[str, str]],
        result_type: type[TModel],
        *,
        task: str,
        prompt_version: str,
    ) -> TModel:
        raw, call_meta = self._chat(messages)
        attempts = 0
        last_error = ""
        while True:
            attempts += 1
            try:
                payload = _extract_json_object(raw)
                result = result_type.model_validate(payload)
                base_meta = {
                    "provider": self.name,
                    "model": self.model,
                    "prompt_version": prompt_version,
                    "attempts": attempts,
                    "repaired": attempts > 1,
                    "call": call_meta,
                }
                merged = dict(getattr(result, "meta", {}) or {})
                merged.update(base_meta)
                result.meta = merged  # type: ignore[attr-defined]
                return result
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)
                if attempts > self.json_retries:
                    raise ProviderResponseError(
                        f"模型输出未通过 {task} 结构校验（已尝试 {attempts} 次）",
                        details={
                            "task": task,
                            "model": self.model,
                            "error": last_error[:1500],
                            "raw_excerpt": raw[:1500],
                            "prompt_version": prompt_version,
                        },
                    ) from exc
                raw, repair_meta = self._chat(
                    repair_messages(
                        raw,
                        last_error,
                        SCHEMAS[task],
                        task,
                    )
                )
                call_meta = dict(call_meta)
                call_meta["repair"] = repair_meta

    # -------------------------------------------------------------- analysis

    def analyze_characters(
        self,
        text: str,
        known_characters: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> CharacterAnalysisResult:
        return self._structured(
            character_messages(text, known_characters, context),
            CharacterAnalysisResult,
            task="characters",
            prompt_version=CHARACTER_PROMPT_VERSION,
        )

    def analyze_segments(
        self,
        text: str,
        known_characters: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> SegmentAnalysisResult:
        return self._structured(
            segment_messages(text, known_characters, context),
            SegmentAnalysisResult,
            task="segments",
            prompt_version=SEGMENT_PROMPT_VERSION,
        )

    def summarize_chapter(
        self, text: str, known_characters: list[dict[str, Any]]
    ) -> ChapterSummaryResult:
        return self._structured(
            summary_messages(text, known_characters),
            ChapterSummaryResult,
            task="summary",
            prompt_version=SUMMARY_PROMPT_VERSION,
        )

    # ---------------------------------------------------------- v0.5.0 P2

    def extract_bible_characters(
        self,
        text: str,
        known_characters: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> BibleChapterResult:
        return self._structured(
            bible_character_messages(text, known_characters, context),
            BibleChapterResult,
            task="bible_characters",
            prompt_version=BIBLE_CHARACTER_PROMPT_VERSION,
        )

    def merge_bible_candidates(
        self,
        candidates: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> BibleMergeResult:
        return self._structured(
            bible_merge_messages(candidates, context),
            BibleMergeResult,
            task="bible_merge",
            prompt_version=BIBLE_MERGE_PROMPT_VERSION,
        )

    def synthesize_bible_timeline(
        self,
        chapters: list[dict[str, Any]],
        context: dict[str, Any] | None = None,
    ) -> BibleTimelineResult:
        return self._structured(
            bible_timeline_messages(chapters, context),
            BibleTimelineResult,
            task="bible_timeline",
            prompt_version=BIBLE_TIMELINE_PROMPT_VERSION,
        )