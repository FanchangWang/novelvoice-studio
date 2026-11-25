from __future__ import annotations

from novelvoice_studio import __version__


def test_health(client) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"] == __version__
    assert payload["providers_mode"] == "mock"


def test_system_status(client, settings) -> None:
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mock_mode"] is True
    assert payload["providers_mode"] == "mock"
    assert payload["overall_ok"] is True
    assert payload["data_dir"] == str(settings.data_dir)
    components = {item["id"]: item for item in payload["components"]}
    assert components["database"]["ok"] is True
    assert components["disk"]["ok"] is True
    assert components["gpu"]["label"] == "NVIDIA GPU"
    assert components["ffmpeg"]["label"] == "FFmpeg"
    assert "ollama" in components
    assert "voice_design_worker" in components
    assert "tts_worker" in components


def test_public_settings_read_only(client, settings) -> None:
    response = client.get("/api/v1/system/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["read_only"] is True
    assert payload["providers"] == {"mode": "mock", "is_mock": True}
    assert payload["app"]["data_dir"] == str(settings.data_dir)
    assert "api_key" not in str(payload).lower()


def test_shutdown_requires_control_token(client, settings) -> None:
    path = "/api/v1/system/shutdown"

    assert client.post(path).status_code == 404

    settings.control_token = "test-control-token"
    assert client.post(path).status_code == 403
    assert (
        client.post(path, headers={"x-nvs-control": "wrong-token"}).status_code
        == 403
    )
    # TestClient 没有 uvicorn server 实例；令牌正确时也应拒绝而不是真的停机。
    response = client.post(path, headers={"x-nvs-control": "test-control-token"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "http_409"


def test_root_without_frontend(client) -> None:
    response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "backend-only"
    assert payload["docs"] == "/api/docs"


def test_api_404_shape(client) -> None:
    response = client.get("/api/v1/unknown")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"


def test_security_headers(client) -> None:
    response = client.get("/api/v1/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_cors_allows_local_dev_origin(client) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert response.headers["access-control-allow-origin"] != "*"


def test_cors_rejects_foreign_origin(client) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in response.headers


def test_trusted_host_rejects_foreign_host(client) -> None:
    response = client.get("/api/v1/health", headers={"host": "evil.example"})
    assert response.status_code == 400