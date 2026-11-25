"""文本文件编码检测与解码。"""

from __future__ import annotations

import codecs
import hashlib
from dataclasses import dataclass

from charset_normalizer import from_bytes

from novelvoice_studio.core.errors import UnsupportedFileError, ValidationError

SUPPORTED_ENCODINGS: dict[str, str] = {
    "utf-8": "UTF-8",
    "utf-8-sig": "UTF-8 (BOM)",
    "gb18030": "GB18030",
    "gbk": "GBK",
    "gb2312": "GB2312",
    "big5": "Big5",
    "utf-16": "UTF-16",
    "utf-16-le": "UTF-16 LE",
    "utf-16-be": "UTF-16 BE",
}

_BOMS: tuple[tuple[bytes, str, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig", "UTF-8 BOM"),
    (codecs.BOM_UTF16_LE, "utf-16", "UTF-16 LE BOM"),
    (codecs.BOM_UTF16_BE, "utf-16", "UTF-16 BE BOM"),
)


@dataclass(frozen=True)
class EncodingCandidate:
    encoding: str
    label: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class DetectionResult:
    text: str
    encoding: str
    label: str
    confidence: float
    candidates: list[EncodingCandidate]
    needs_confirmation: bool
    has_bom: bool


def display_encoding(encoding: str) -> str:
    return SUPPORTED_ENCODINGS.get(encoding.lower(), encoding)


def normalize_encoding_name(encoding: str) -> str:
    candidate = encoding.strip().lower().replace("_", "-")
    if candidate not in SUPPORTED_ENCODINGS:
        raise ValidationError(
            f"不支持的编码：{encoding}",
            details={"supported": sorted(SUPPORTED_ENCODINGS)},
        )
    return candidate


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def looks_binary(data: bytes) -> bool:
    sample = data[:4096]
    if not sample:
        return False
    if b"\x00" in sample:
        # UTF-16 文本的 NUL 集中在奇/偶一个字节位；两端都有大量 NUL 才更像二进制
        even_nuls = sample[0::2].count(0)
        odd_nuls = sample[1::2].count(0)
        half = max(1, len(sample) // 2)
        if max(even_nuls, odd_nuls) / half > 0.3:
            return False
        return True
    control = sum(1 for byte in sample if byte < 9 or (13 < byte < 32))
    return control / len(sample) > 0.05


def _try_decode(data: bytes, encoding: str) -> str | None:
    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None


def _candidate(data: bytes, encoding: str, confidence: float, reason: str) -> EncodingCandidate | None:
    if _try_decode(data, encoding) is None:
        return None
    return EncodingCandidate(
        encoding=encoding,
        label=display_encoding(encoding),
        confidence=round(confidence, 3),
        reason=reason,
    )


def _dedupe(candidates: list[EncodingCandidate]) -> list[EncodingCandidate]:
    seen: set[str] = set()
    result: list[EncodingCandidate] = []
    for candidate in candidates:
        key = candidate.encoding
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def detect_and_decode(data: bytes) -> DetectionResult:
    if not data:
        raise ValidationError("文件为空")

    for bom, encoding, reason in _BOMS:
        if data.startswith(bom):
            text = _try_decode(data, encoding)
            if text is None:
                break
            text = normalize_newlines(text.lstrip("\ufeff"))
            candidate = EncodingCandidate(
                encoding=encoding,
                label=display_encoding(encoding),
                confidence=1.0,
                reason=reason,
            )
            return DetectionResult(
                text=text,
                encoding=encoding,
                label=candidate.label,
                confidence=1.0,
                candidates=[candidate],
                needs_confirmation=False,
                has_bom=True,
            )

    candidates: list[EncodingCandidate] = []

    if looks_binary(data):
        raise UnsupportedFileError("文件疑似二进制内容，仅支持纯文本 TXT")
    if b"\x00" in data[:4096]:
        raise UnsupportedFileError(
            "检测到 NUL 字节，疑似二进制或无 BOM 的 UTF-16；"
            "请转换为 UTF-8/GBK，或使用带 BOM 的 UTF-16，也可在预览时手动指定编码"
        )

    utf8 = _candidate(data, "utf-8", 0.99, "严格 UTF-8 解码成功")
    if utf8:
        candidates.append(utf8)
    else:
        detected = from_bytes(data[: 1024 * 1024]).best()
        if detected is not None and detected.encoding:
            normalized = detected.encoding.lower().replace("_", "-")
            alias = {
                "gb2312": "gb18030",
                "gbk": "gb18030",
                "utf-8-sig": "utf-8-sig",
                "utf16": "utf-16",
            }.get(normalized, normalized)
            if alias in SUPPORTED_ENCODINGS:
                chaos = float(detected.chaos or 0.0)
                confidence = max(0.35, min(0.92, 1.0 - chaos))
                candidate = _candidate(data, alias, confidence, "charset-normalizer 统计检测")
                if candidate:
                    candidates.append(candidate)

        for encoding, confidence in (("gb18030", 0.8), ("big5", 0.6)):
            candidate = _candidate(data, encoding, confidence, f"{display_encoding(encoding)} 严格解码成功")
            if candidate:
                candidates.append(candidate)

    candidates = _dedupe(candidates)
    if not candidates:
        raise ValidationError(
            "无法识别文本编码，请手动选择编码",
            code="encoding_undetermined",
            details={"supported": sorted(SUPPORTED_ENCODINGS)},
        )
    candidates.sort(key=lambda item: item.confidence, reverse=True)
    chosen = candidates[0]
    text = _try_decode(data, chosen.encoding)
    if text is None:
        raise ValidationError("编码解码失败，请手动选择编码", code="encoding_undetermined")
    text = normalize_newlines(text.lstrip("\ufeff"))
    return DetectionResult(
        text=text,
        encoding=chosen.encoding,
        label=chosen.label,
        confidence=chosen.confidence,
        candidates=candidates[:4],
        needs_confirmation=chosen.confidence < 0.75,
        has_bom=False,
    )


def decode_with(data: bytes, encoding: str) -> tuple[str, DetectionResult]:
    """按用户指定编码解码，同时给出检测候选用于展示。"""
    if not data:
        raise ValidationError("文件为空")
    normalized = normalize_encoding_name(encoding)
    text = _try_decode(data, normalized)
    if text is None:
        raise ValidationError(
            f"使用 {display_encoding(normalized)} 解码失败，请换一种编码",
            code="encoding_decode_failed",
        )
    text = normalize_newlines(text.lstrip("\ufeff"))
    try:
        detected = detect_and_decode(data)
        candidates = detected.candidates
        needs_confirmation = False
    except (ValidationError, UnsupportedFileError):
        candidates = []
        needs_confirmation = False
    chosen = EncodingCandidate(
        encoding=normalized,
        label=display_encoding(normalized),
        confidence=1.0,
        reason="用户手动选择",
    )
    rest = [c for c in candidates if c.encoding != normalized]
    return text, DetectionResult(
        text=text,
        encoding=normalized,
        label=chosen.label,
        confidence=1.0,
        candidates=[chosen, *rest][:4],
        needs_confirmation=needs_confirmation,
        has_bom=data.startswith(codecs.BOM_UTF8),
    )


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_utf8(path) -> str:  # type: ignore[no-untyped-def]
    return path.read_text(encoding="utf-8")