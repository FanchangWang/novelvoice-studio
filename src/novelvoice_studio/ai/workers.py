"""本机语音引擎服务的 HTTP 客户端。

引擎服务必须运行在回环地址；这里不做任何模型加载或文件路径参数传递，
音频只通过请求体传输，避免后端成为任意文件读取通道。
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from novelvoice_studio.ai.base import AudioResult
from novelvoice_studio.core.errors import DependencyUnavailableError

WORKER_HINT = "请先启动语音引擎服务（uv run novelvoice-studio worker start）并确认模型目录已就绪。"


class WorkerClient:
    label = "语音引擎服务"

    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _client(self) -> httpx.Client:
        # 本机引擎服务不经过系统代理（ALL_PROXY 等在 Windows 上经常为空值）。
        return httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_seconds, connect=3.0),
            trust_env=False,
        )

    def _error(self, message: str, **details: Any) -> DependencyUnavailableError:
        return DependencyUnavailableError(
            message,
            details={"worker": self.label, "url": self.base_url, **details},
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            with self._client() as client:
                response = client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise self._error(
                f"{self.label}未启动（{self.base_url}）。{WORKER_HINT}",
                error_type=exc.__class__.__name__,
            ) from exc
        except httpx.TimeoutException as exc:
            raise self._error(
                f"{self.label}响应超时（{self.timeout_seconds:.0f}s）。{WORKER_HINT}",
                error_type=exc.__class__.__name__,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._error(
                f"无法连接{self.label}：{exc}", error_type=exc.__class__.__name__
            ) from exc

        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                detail = str(
                    payload.get("detail")
                    or payload.get("error", {}).get("message")
                    or ""
                )
            except Exception:  # noqa: BLE001
                detail = response.text[:300]
            raise self._error(
                f"{self.label}返回 HTTP {response.status_code}：{detail or '未知错误'}",
                status_code=response.status_code,
            )
        return response

    def health(self) -> dict[str, Any]:
        response = self._request("GET", "/health")
        try:
            payload = response.json()
        except ValueError:
            payload = {"ok": False, "detail": "引擎服务返回了非 JSON 健康信息"}
        return payload if isinstance(payload, dict) else {"ok": False}

    def unload(self) -> dict[str, Any]:
        response = self._request("POST", "/v1/unload")
        try:
            payload = response.json()
        except ValueError:
            payload = {"ok": True}
        return payload if isinstance(payload, dict) else {"ok": True}


class QwenWorkerClient(WorkerClient):
    label = "Qwen3-TTS VoiceDesign 引擎服务"

    def generate(
        self,
        *,
        text: str,
        instruct: str,
        language: str = "Chinese",
        seed: int,
    ) -> AudioResult:
        response = self._request(
            "POST",
            "/v1/generate",
            json={"text": text, "instruct": instruct, "language": language, "seed": seed},
        )
        content = response.content
        if not content or content[:4] != b"RIFF":
            raise self._error("Qwen 引擎服务返回的不是 WAV 音频", status_code=response.status_code)
        return AudioResult(
            audio_bytes=content,
            sample_rate=int(response.headers.get("X-NVS-Sample-Rate") or 0),
            channels=1,
            duration_ms=int(response.headers.get("X-NVS-Duration-Ms") or 0),
            format="wav",
            provider="qwen3_tts",
            model="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
            is_mock=False,
            metadata={
                "seed": seed,
                "language": language,
                "elapsed_seconds": _float_header(response, "X-NVS-Elapsed-Seconds"),
                "model_id": response.headers.get("X-NVS-Model-Id") or "",
            },
        )


class CosyVoiceWorkerClient(WorkerClient):
    label = "CosyVoice3 引擎服务"

    def vc(
        self,
        *,
        source_bytes: bytes,
        reference_bytes: bytes,
        speed: float,
        seed: int,
    ) -> AudioResult:
        response = self._request(
            "POST",
            "/v1/vc",
            json={
                "source_b64": base64.b64encode(source_bytes).decode("ascii"),
                "reference_b64": base64.b64encode(reference_bytes).decode("ascii"),
                "speed": speed,
                "seed": seed,
            },
        )
        content = response.content
        if not content or content[:4] != b"RIFF":
            raise self._error("CosyVoice 引擎服务返回的不是 WAV 音频", status_code=response.status_code)
        return AudioResult(
            audio_bytes=content,
            sample_rate=int(response.headers.get("X-NVS-Sample-Rate") or 0),
            channels=1,
            duration_ms=int(response.headers.get("X-NVS-Duration-Ms") or 0),
            format="wav",
            provider="cosyvoice3",
            model="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            is_mock=False,
            metadata={
                "speed": speed,
                "seed": seed,
                "elapsed_seconds": _float_header(response, "X-NVS-Elapsed-Seconds"),
                "model_id": response.headers.get("X-NVS-Model-Id") or "",
            },
        )

    def deess(
        self,
        *,
        audio_bytes: bytes,
        threshold_ratio: float,
        full_ratio: float,
        max_reduction_db: float,
        band_low: float,
        band_high: float,
    ) -> AudioResult:
        response = self._request(
            "POST",
            "/v1/deess",
            json={
                "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
                "threshold_ratio": threshold_ratio,
                "full_ratio": full_ratio,
                "max_reduction_db": max_reduction_db,
                "band_low": band_low,
                "band_high": band_high,
            },
        )
        content = response.content
        if not content or content[:4] != b"RIFF":
            raise self._error("CosyVoice 引擎服务返回的不是 WAV 音频", status_code=response.status_code)
        stats: dict[str, Any] = {}
        raw_stats = response.headers.get("X-NVS-Deess-Stats")
        if raw_stats:
            try:
                parsed = json.loads(raw_stats)
                if isinstance(parsed, dict):
                    stats = parsed
            except ValueError:
                stats = {}
        return AudioResult(
            audio_bytes=content,
            sample_rate=int(response.headers.get("X-NVS-Sample-Rate") or 0),
            channels=1,
            duration_ms=int(response.headers.get("X-NVS-Duration-Ms") or 0),
            format="wav",
            provider="cosyvoice3_deess",
            model="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
            is_mock=False,
            metadata={"deess": stats},
        )


def _float_header(response: httpx.Response, name: str) -> float | None:
    try:
        return round(float(response.headers.get(name) or 0.0), 3)
    except (TypeError, ValueError):
        return None