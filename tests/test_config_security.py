from __future__ import annotations

import pytest
from pydantic import ValidationError

from novelvoice_studio.config import (
    AnalysisConfig,
    AppConfig,
    LLMConfig,
    ProvidersConfig,
    Settings,
)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://192.168.1.10:11434",
        "http://10.0.0.2:11434",
        "https://api.example.com/v1",
    ],
)
def test_llm_base_url_must_stay_loopback(base_url: str) -> None:
    with pytest.raises(ValidationError):
        LLMConfig(base_url=base_url)


def test_llm_loopback_accepts_hosts_and_strips_slash() -> None:
    for value in ("http://127.0.0.1:11434/", "http://localhost:11434", "http://[::1]:11434"):
        config = LLMConfig(base_url=value)
        assert not config.base_url.endswith("/")


def test_app_host_must_be_loopback() -> None:
    with pytest.raises(ValidationError):
        AppConfig(host="0.0.0.0")
    with pytest.raises(ValidationError):
        AppConfig(host="192.168.1.10")


def test_provider_mode_is_restricted() -> None:
    assert ProvidersConfig(mode="mock").mode == "mock"
    assert ProvidersConfig(mode="real").mode == "real"
    with pytest.raises(ValidationError):
        ProvidersConfig(mode="cloud")


def test_analysis_limits_are_bounded() -> None:
    with pytest.raises(ValidationError):
        AnalysisConfig(max_chapter_chars=10)
    with pytest.raises(ValidationError):
        AnalysisConfig(speaker_confidence_threshold=1.5)
    config = AnalysisConfig(max_chapter_chars=5000)
    assert config.max_chapter_chars == 5000


def test_settings_defaults_keep_loopback() -> None:
    settings = Settings()
    assert settings.app.host == "127.0.0.1"
    assert settings.llm.base_url.startswith("http://127.0.0.1")
    assert settings.providers.mode in {"mock", "real"}