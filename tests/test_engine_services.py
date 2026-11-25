from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from novelvoice_studio import engine_services
from novelvoice_studio.config import Settings, TTSConfig, VoiceDesignConfig
from novelvoice_studio.engine_services import (
    EngineServiceError,
    load_manifest,
    parse_loopback_url,
    resolve_targets,
    select_services,
)


def test_manifest_lists_only_production_services() -> None:
    specs = load_manifest()
    assert [spec.id for spec in specs] == ["qwen_tts", "cosyvoice"]

    qwen = specs[0]
    assert qwen.project.name == "qwen_tts_worker"
    assert qwen.entrypoint.name == "server.py"
    assert qwen.config_url == "voice_design.worker_url"
    assert qwen.config_model == "voice_design.model_id"
    assert qwen.health_worker == "qwen3_tts_voice_design"
    assert "POST /v1/shutdown" in qwen.endpoints

    cosy = specs[1]
    assert cosy.project.name == "cosyvoice_worker"
    assert cosy.config_url == "tts.worker_url"
    assert cosy.config_model == "tts.model_id"
    assert cosy.health_worker == "cosyvoice3_vc"
    assert "POST /v1/deess" in cosy.endpoints


def test_targets_follow_application_config() -> None:
    specs = load_manifest()
    settings = Settings(
        voice_design=VoiceDesignConfig(worker_url="http://127.0.0.1:18766"),
        tts=TTSConfig(worker_url="http://localhost:18767/"),
    )
    targets = resolve_targets(specs, settings)
    assert [(item.host, item.port) for item in targets] == [
        ("127.0.0.1", 18766),
        ("localhost", 18767),
    ]
    assert targets[0].url == "http://127.0.0.1:18766"
    assert targets[1].url == "http://localhost:18767"
    assert targets[0].model_id == settings.voice_design.model_id
    assert targets[1].model_id == settings.tts.model_id


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:8766",
        "http://192.168.1.20:8766",
        "http://127.0.0.1:8766/api",
        "http://127.0.0.1",
        "http://user:pass@127.0.0.1:8766",
        "http://127.0.0.1:8766/?x=1",
    ],
)
def test_non_loopback_or_ambiguous_urls_are_rejected(value: str) -> None:
    with pytest.raises(EngineServiceError):
        parse_loopback_url(value, "worker_url")


def test_loopback_url_accepts_default_forms() -> None:
    assert parse_loopback_url("http://127.0.0.1:8766/", "worker_url") == (
        "127.0.0.1",
        8766,
        "http://127.0.0.1:8766",
    )
    assert parse_loopback_url("http://[::1]:8767", "worker_url") == (
        "::1",
        8767,
        "http://[::1]:8767",
    )


def test_manifest_rejects_project_outside_workers(tmp_path: Path) -> None:
    manifest = tmp_path / "services.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "services": [
                    {
                        "id": "escape",
                        "label": "Escape",
                        "project": str(tmp_path.parent),
                        "entrypoint": "server.py",
                        "config_url": "voice_design.worker_url",
                        "config_model": "voice_design.model_id",
                        "health_worker": "escape",
                    }
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(EngineServiceError, match="相对路径"):
        load_manifest(manifest)


def test_select_services_preserves_request_order() -> None:
    specs = load_manifest()
    selected = select_services(specs, ["cosyvoice", "qwen_tts", "cosyvoice"])
    assert [spec.id for spec in selected] == ["cosyvoice", "qwen_tts"]
    with pytest.raises(EngineServiceError, match="未知服务"):
        select_services(specs, ["missing"])


def test_state_pid_prefers_live_launcher_and_falls_back_to_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"pid": 101, "worker_pid": 202}
    assert engine_services._state_pid(state) == 101
    assert engine_services._state_worker_pid(state) == 202
    assert engine_services._state_int({"pid": "not-a-pid"}, "pid") == 0

    monkeypatch.setattr(engine_services, "_pid_alive", lambda pid: pid in {101, 202})
    assert engine_services._state_live_pid(state) == 101

    monkeypatch.setattr(engine_services, "_pid_alive", lambda pid: pid == 202)
    assert engine_services._state_live_pid(state) == 202

    monkeypatch.setattr(engine_services, "_pid_alive", lambda pid: False)
    assert engine_services._state_live_pid(state) == 0