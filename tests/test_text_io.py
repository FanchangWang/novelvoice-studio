from __future__ import annotations

import codecs

import pytest

from novelvoice_studio.core.errors import UnsupportedFileError, ValidationError
from novelvoice_studio.services.text_io import (
    decode_with,
    detect_and_decode,
    normalize_newlines,
)


def test_utf8_detection(sample_text: str) -> None:
    result = detect_and_decode(sample_text.encode("utf-8"))
    assert result.encoding == "utf-8"
    assert result.confidence >= 0.9
    assert result.needs_confirmation is False
    assert result.text == sample_text


def test_utf8_bom_removed(sample_text: str) -> None:
    result = detect_and_decode(codecs.BOM_UTF8 + sample_text.encode("utf-8"))
    assert result.encoding == "utf-8-sig"
    assert result.has_bom is True
    assert result.text.startswith("前言")
    assert "\ufeff" not in result.text


@pytest.mark.parametrize(
    ("bom", "codec"),
    [
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ],
)
def test_utf16_bom_detection(sample_text: str, bom: bytes, codec: str) -> None:
    result = detect_and_decode(bom + sample_text.encode(codec))
    assert result.encoding == "utf-16"
    assert result.has_bom is True
    assert result.text == sample_text


def test_gb18030_detection(sample_text: str) -> None:
    result = detect_and_decode(sample_text.encode("gb18030"))
    assert result.encoding in {"gb18030", "gbk", "gb2312"}
    assert result.text == sample_text
    assert result.needs_confirmation is False


def test_crlf_normalized(sample_text: str) -> None:
    data = sample_text.replace("\n", "\r\n").encode("utf-8")
    result = detect_and_decode(data)
    assert "\r" not in result.text
    assert result.text == normalize_newlines(sample_text)


def test_manual_encoding_override(sample_text: str) -> None:
    data = sample_text.encode("gb18030")
    text, result = decode_with(data, "gb18030")
    assert text == sample_text
    assert result.candidates[0].encoding == "gb18030"
    assert result.needs_confirmation is False


def test_manual_encoding_failure() -> None:
    with pytest.raises(ValidationError) as excinfo:
        decode_with(b"abc", "utf-16-le")
    assert excinfo.value.code in {"encoding_decode_failed", "validation_error"}


def test_binary_rejected() -> None:
    with pytest.raises(UnsupportedFileError):
        detect_and_decode(b"\x00\x01\x02\x03" * 1024)


def test_empty_rejected() -> None:
    with pytest.raises(ValidationError):
        detect_and_decode(b"")


def test_unsupported_encoding_rejected() -> None:
    with pytest.raises(ValidationError):
        decode_with("测试".encode("utf-8"), "rot13")