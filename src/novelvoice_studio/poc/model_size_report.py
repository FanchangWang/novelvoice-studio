"""查询候选模型的实际下载体积（只查询，不下载模型）。

用法：
  uv run python -m novelvoice_studio.poc.model_size_report
"""

from __future__ import annotations

import os

import httpx

MODELS = [
    "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    "FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
]


def main() -> None:
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    kwargs: dict[str, object] = {"timeout": 30.0}
    if proxy:
        kwargs["proxy"] = proxy

    with httpx.Client(**kwargs) as client:  # type: ignore[arg-type]
        for model in MODELS:
            url = f"https://huggingface.co/api/models/{model}?blobs=true"
            try:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
                siblings = data.get("siblings", [])
                total = 0
                large: list[tuple[str, int]] = []
                for item in siblings:
                    lfs = item.get("lfs") or {}
                    size = lfs.get("size") or item.get("size") or 0
                    total += size
                    if size > 100 * 1024**2:
                        large.append((item["rfilename"], size))
                print(f"[MODEL] {model}")
                print(f"  文件数: {len(siblings)}  总体积: {total / 1024**3:.2f} GiB")
                for name, size in sorted(large, key=lambda x: -x[1])[:10]:
                    print(f"    {name}: {size / 1024**3:.2f} GiB")
            except Exception as exc:
                print(f"[FAIL] {model}: {exc}")
            print()


if __name__ == "__main__":
    main()
