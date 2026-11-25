"""验证 Ollama 本地模型的中文结构化 JSON 输出能力。

前置条件：
  1. Ollama 服务运行于 http://127.0.0.1:11434
  2. 模型已手动拉取（本脚本不会下载模型）

用法：
  uv run python -m novelvoice_studio.poc.ollama_json_check
  NOVELVOICE_LLM_MODEL=modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest uv run python -m novelvoice_studio.poc.ollama_json_check
"""

from __future__ import annotations

import json
import os
import time

import httpx
from pydantic import BaseModel, Field, ValidationError

BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("NOVELVOICE_LLM_MODEL", "modelscope.cn/unsloth/Qwen3.5-4B-GGUF:latest")


class Character(BaseModel):
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    gender: str = "unknown"
    confidence: float = 0.0
    evidence_quote: str = ""


class CharacterAnalysis(BaseModel):
    characters: list[Character]


PROMPT = """你是中文小说分析助手。请从以下文本中抽取角色，严格只输出一个 JSON 对象，
不要输出解释、Markdown 或代码块。JSON 结构：
{"characters":[{"canonical_name":"标准姓名","aliases":["别名"],"gender":"male|female|unknown","confidence":0.0到1.0,"evidence_quote":"原文中的证据片段"}]}
证据不足时 gender 用 unknown，不要根据姓名刻板印象推断。

文本：
\"\"\"
李寻欢放下酒杯，冷冷道："你竟然敢骗我？"
"师兄，我……我只是想帮你。" 林诗音低声道。
小李探花没有再说话，只是望着窗外。一个苍老的声音从门外传来："都进来吧。"
\"\"\"
"""


def check_models(client: httpx.Client) -> list[str]:
    resp = client.get("/api/tags", timeout=5.0)
    resp.raise_for_status()
    return [item["name"] for item in resp.json().get("models", [])]


def run() -> int:
    # 仅访问本机 Ollama，禁用环境代理，避免请求被代理或受 NO_PROXY 格式影响。
    with httpx.Client(base_url=BASE_URL, timeout=120.0, trust_env=False) as client:
        try:
            models = check_models(client)
        except httpx.HTTPError as exc:
            print(f"[FAIL] Ollama 服务不可用: {exc}")
            print(f"请先启动 Ollama 服务并检查 {BASE_URL}/api/tags")
            return 2
        print(f"[INFO] Ollama 已连接，本地模型: {models or '(空)'}")
        if MODEL not in models:
            print(f"[FAIL] 模型 {MODEL!r} 不存在。请先手动执行: ollama pull {MODEL}")
            print("本脚本不会自动下载模型。")
            return 3

        started = time.perf_counter()
        resp = client.post(
            "/api/chat",
            json={
                "model": MODEL,
                "stream": False,
                "format": "json",
                "think": False,
                "messages": [{"role": "user", "content": PROMPT}],
            },
        )
        elapsed = time.perf_counter() - started
        if resp.status_code != 200:
            print(f"[FAIL] Ollama 返回 HTTP {resp.status_code}: {resp.text[:500]}")
            return 4

        payload = resp.json()
        message = payload.get("message", {})
        thinking = message.get("thinking", "")
        content = message.get("content", "")
        print(f"[INFO] 耗时 {elapsed:.2f}s，content 长度 {len(content)}，thinking 长度 {len(thinking)}")
        if not content and thinking:
            print(f"[WARN] 模型只输出了 thinking，前 300 字: {thinking[:300]}")
        try:
            parsed = CharacterAnalysis.model_validate_json(content)
        except (ValidationError, json.JSONDecodeError) as exc:
            print(f"[FAIL] JSON 结构校验失败: {exc}")
            print(f"原始返回: {content[:1000]}")
            return 5

        print("[OK] 中文结构化 JSON 输出校验通过")
        print(parsed.model_dump_json(indent=2, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(run())
