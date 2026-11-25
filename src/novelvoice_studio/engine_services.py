"""`workers/` 引擎服务的本机控制命令。

这里是“引擎服务目录”的控制面，不导入服务代码、不加载 torch。每个服务是
独立 uv project，控制器只做四件事：

1. 读取 ``workers/services.yaml`` 的注册表；
2. 从应用配置解析回环 URL/端口与模型 ID；
3. 用各自 venv 的 Python 启动 ``server.py``，日志/状态写入 ``.run/workers``；
4. 通过带实例令牌的 ``/v1/shutdown`` 停止由本工具启动的进程，外部进程不动。

统一入口：``uv run novelvoice-studio worker <command>``。
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime as dt
import http.client
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKERS_DIR = ROOT / "workers"
MANIFEST_PATH = WORKERS_DIR / "services.yaml"
RUN_DIR = ROOT / ".run" / "workers"

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
HEALTH_TIMEOUT_SECONDS = 1.5
START_TIMEOUT_SECONDS = 120.0
PRELOAD_TIMEOUT_SECONDS = 300.0
STOP_TIMEOUT_SECONDS = 20.0
FORCE_WAIT_SECONDS = 5.0


class EngineServiceError(RuntimeError):
    """引擎服务注册表、配置或进程状态无法处理。"""


@dataclass(frozen=True)
class ServiceSpec:
    """``services.yaml`` 中的一个生产引擎服务。"""

    id: str
    label: str
    description: str
    project: Path
    entrypoint: Path
    config_url: str
    config_model: str
    health_worker: str
    capabilities: tuple[str, ...]
    endpoints: tuple[str, ...]

    @property
    def project_display(self) -> str:
        try:
            return self.project.relative_to(ROOT).as_posix()
        except ValueError:
            return str(self.project)


@dataclass(frozen=True)
class ServiceTarget:
    """服务 + 从应用配置解析出的回环地址。"""

    spec: ServiceSpec
    host: str
    port: int
    url: str
    model_id: str


# ---------------------------------------------------------------------------
# 注册表与配置
# ---------------------------------------------------------------------------


def _text(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EngineServiceError(f"{where}.{key} 必须是非空字符串")
    return value.strip()


def _optional_text(entry: dict[str, Any], key: str, where: str) -> str:
    value = entry.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise EngineServiceError(f"{where}.{key} 必须是字符串")
    return value.strip()


def _string_tuple(entry: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = entry.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EngineServiceError(f"{where}.{key} 必须是字符串列表")
    return tuple(item.strip() for item in value if item.strip())


def _resolve_inside(base: Path, relative: str, where: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise EngineServiceError(f"{where} 必须是相对路径: {relative}")
    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError as exc:
        raise EngineServiceError(f"{where} 不能跳出目录: {relative}") from exc
    return resolved


def _dotted_path(value: str, where: str) -> str:
    parts = value.split(".")
    if len(parts) != 2 or not all(part.isidentifier() for part in parts):
        raise EngineServiceError(
            f"{where} 必须是“配置段.字段”形式，例如 voice_design.worker_url: {value}"
        )
    return value


def load_manifest(path: Path | str = MANIFEST_PATH) -> list[ServiceSpec]:
    """读取并校验引擎服务注册表。项目路径必须位于注册表同级目录内。"""
    manifest_path = Path(path).resolve()
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise EngineServiceError(f"无法读取引擎服务注册表: {manifest_path}") from exc
    except yaml.YAMLError as exc:
        raise EngineServiceError(f"引擎服务注册表 YAML 无效: {exc}") from exc
    if not isinstance(raw, dict):
        raise EngineServiceError("引擎服务注册表必须是 YAML 映射")
    if raw.get("version") != 1:
        raise EngineServiceError("引擎服务注册表 version 必须为 1")
    entries = raw.get("services")
    if not isinstance(entries, list) or not entries:
        raise EngineServiceError("引擎服务注册表 services 不能为空")

    base = manifest_path.parent
    specs: list[ServiceSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        where = f"services[{index}]"
        if not isinstance(entry, dict):
            raise EngineServiceError(f"{where} 必须是映射")
        service_id = _text(entry, "id", where)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", service_id):
            raise EngineServiceError(f"{where}.id 只能使用小写字母、数字和下划线")
        if service_id in seen:
            raise EngineServiceError(f"重复的服务 id: {service_id}")
        seen.add(service_id)

        project = _resolve_inside(base, _text(entry, "project", where), f"{where}.project")
        if not project.is_dir():
            raise EngineServiceError(f"{where}.project 目录不存在: {project}")
        entrypoint = _resolve_inside(
            project, _text(entry, "entrypoint", where), f"{where}.entrypoint"
        )
        if not entrypoint.is_file():
            raise EngineServiceError(f"{where}.entrypoint 文件不存在: {entrypoint}")

        specs.append(
            ServiceSpec(
                id=service_id,
                label=_text(entry, "label", where),
                description=_optional_text(entry, "description", where),
                project=project,
                entrypoint=entrypoint,
                config_url=_dotted_path(
                    _text(entry, "config_url", where), f"{where}.config_url"
                ),
                config_model=_dotted_path(
                    _text(entry, "config_model", where), f"{where}.config_model"
                ),
                health_worker=_text(entry, "health_worker", where),
                capabilities=_string_tuple(entry, "capabilities", where),
                endpoints=_string_tuple(entry, "endpoints", where),
            )
        )
    return specs


def select_services(specs: list[ServiceSpec], requested: list[str]) -> list[ServiceSpec]:
    if not requested:
        return specs
    by_id = {spec.id: spec for spec in specs}
    missing = [item for item in requested if item not in by_id]
    if missing:
        known = ", ".join(by_id)
        raise EngineServiceError(f"未知服务: {', '.join(missing)}；可用: {known}")
    selected: list[ServiceSpec] = []
    for service_id in requested:
        if service_id not in {item.id for item in selected}:
            selected.append(by_id[service_id])
    return selected


def _dotted_get(settings: Any, path: str) -> Any:
    value = settings
    for part in path.split("."):
        if not hasattr(value, part):
            raise EngineServiceError(f"应用配置缺少字段 {path}")
        value = getattr(value, part)
    return value


def parse_loopback_url(value: str, label: str) -> tuple[str, int, str]:
    """只接受 http://<回环主机>:<端口>，返回 (host, port, 规范化 URL)。"""
    parsed = urlparse(str(value).strip())
    if parsed.scheme != "http":
        raise EngineServiceError(f"{label} 必须是 http:// 回环地址: {value}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise EngineServiceError(f"{label} 不能包含认证信息、查询串或片段: {value}")
    if parsed.path not in ("", "/"):
        raise EngineServiceError(f"{label} 不能带路径前缀: {value}")
    host = (parsed.hostname or "").lower()
    if host not in LOOPBACK_HOSTS:
        raise EngineServiceError(f"{label} 只能是本机回环地址: {value}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise EngineServiceError(f"{label} 端口无效: {value}") from exc
    if port is None:
        raise EngineServiceError(f"{label} 必须显式指定端口: {value}")
    url_host = f"[{host}]" if ":" in host else host
    return host, port, f"http://{url_host}:{port}"


def resolve_targets(specs: list[ServiceSpec], settings: Any) -> list[ServiceTarget]:
    targets: list[ServiceTarget] = []
    for spec in specs:
        url_value = _dotted_get(settings, spec.config_url)
        model_id = _dotted_get(settings, spec.config_model)
        host, port, url = parse_loopback_url(str(url_value), spec.config_url)
        targets.append(
            ServiceTarget(
                spec=spec,
                host=host,
                port=port,
                url=url,
                model_id=str(model_id),
            )
        )
    return targets


# ---------------------------------------------------------------------------
# venv / 状态 / HTTP
# ---------------------------------------------------------------------------


def _python_env_name(service_id: str) -> str:
    return "NOVELVOICE_ENGINE_PYTHON_" + re.sub(r"[^A-Za-z0-9]+", "_", service_id).upper()


def engine_python(spec: ServiceSpec) -> Path | None:
    """优先显式环境变量，否则使用服务项目自己的 .venv。"""
    override = os.environ.get(_python_env_name(spec.id), "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise EngineServiceError(
                f"{_python_env_name(spec.id)} 指向的 Python 不存在: {path}"
            )
        return path
    candidates = (
        spec.project / ".venv" / "Scripts" / "python.exe",
        spec.project / ".venv" / "bin" / "python",
        spec.project / ".venv" / "bin" / "python3",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def state_path(spec: ServiceSpec) -> Path:
    return RUN_DIR / f"{spec.id}.json"


def log_paths(spec: ServiceSpec) -> tuple[Path, Path]:
    return RUN_DIR / f"{spec.id}.out.log", RUN_DIR / f"{spec.id}.err.log"


def _read_state(spec: ServiceSpec) -> dict[str, Any] | None:
    path = state_path(spec)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("id") != spec.id:
        return None
    return payload


def _write_state(spec: ServiceSpec, payload: dict[str, Any]) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    path = state_path(spec)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp.replace(path)
    if os.name != "nt":
        with contextlib.suppress(OSError):
            path.chmod(0o600)


def _remove_state(spec: ServiceSpec) -> None:
    with contextlib.suppress(FileNotFoundError):
        state_path(spec).unlink()


def _state_int(state: dict[str, Any] | None, key: str) -> int:
    if not state:
        return 0
    try:
        return int(state.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _state_pid(state: dict[str, Any] | None) -> int:
    """启动器 PID：Windows 上通常是 venv 的 python.exe 包装进程。"""
    return _state_int(state, "pid")


def _state_worker_pid(state: dict[str, Any] | None) -> int:
    """服务进程自身 PID：由就绪后的 /health 回报，可能等于启动器 PID。"""
    return _state_int(state, "worker_pid")


def _state_live_pid(state: dict[str, Any] | None) -> int:
    """返回仍存活的进程 PID，优先启动器（便于整棵进程树处理）。"""
    for pid in (_state_pid(state), _state_worker_pid(state)):
        if pid and _pid_alive(pid):
            return pid
    return 0


def _update_state(spec: ServiceSpec, **fields: Any) -> None:
    state = _read_state(spec)
    if state is None:
        return
    state.update(fields)
    _write_state(spec, state)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid)
            if not handle:
                return False
            try:
                return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
            finally:
                kernel32.CloseHandle(handle)
        except (AttributeError, OSError, ValueError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _http_request(
    host: str,
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = HEALTH_TIMEOUT_SECONDS,
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request(
            method,
            path,
            body=body,
            headers={"Connection": "close", **(headers or {})},
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _fetch_health(
    target: ServiceTarget, timeout: float = HEALTH_TIMEOUT_SECONDS
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        status, raw = _http_request(
            target.host, target.port, "GET", "/health", timeout=timeout
        )
    except (OSError, http.client.HTTPException) as exc:
        return None, f"{exc.__class__.__name__}: {exc}"
    if status != 200:
        return None, f"HTTP {status}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "健康检查响应不是 JSON"
    if not isinstance(payload, dict):
        return None, "健康检查响应不是对象"
    return payload, None


def _worker_matches(health: dict[str, Any] | None, spec: ServiceSpec) -> bool:
    return bool(health and health.get("worker") == spec.health_worker)


def _state_owned(state: dict[str, Any] | None, health: dict[str, Any] | None) -> bool:
    if not state or not health:
        return False
    expected = str(state.get("instance_id") or "")
    actual = str(health.get("instance_id") or "")
    return bool(expected and actual and secrets.compare_digest(expected, actual))


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _health_summary(health: dict[str, Any]) -> str:
    bits: list[str] = []
    if "model_loaded" in health:
        bits.append("模型已加载" if health.get("model_loaded") else "模型按需加载")
    if health.get("load_error"):
        bits.append(f"加载错误: {str(health['load_error'])[:120]}")
    if health.get("torch"):
        bits.append(f"torch {health['torch']}")
    gpu = health.get("gpu")
    if isinstance(gpu, dict) and gpu.get("device"):
        bits.append(str(gpu["device"]))
    return "；".join(bits)


def _tail_lines(path: Path, lines: int = 30) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def _print_tail(spec: ServiceSpec, lines: int = 20) -> None:
    out_path, err_path = log_paths(spec)
    tail = _tail_lines(err_path, lines) or _tail_lines(out_path, lines)
    if not tail:
        return
    print("  最近日志:")
    for line in tail:
        print(f"    {line}")


def _wait_pids_dead(pids: list[int], timeout: float) -> bool:
    targets = [pid for pid in dict.fromkeys(pids) if pid > 0]
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        alive = [pid for pid in targets if _pid_alive(pid)]
        if not alive:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def _wait_pid_dead(pid: int, timeout: float) -> bool:
    return _wait_pids_dead([pid], timeout)


# ---------------------------------------------------------------------------
# 进程身份与强制停止（仅在 --force 且身份可核实时使用）
# ---------------------------------------------------------------------------


def _process_command_line(pid: int) -> str | None:
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            return None
        script = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            f"$p=Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\";"
            "if ($p) { $p.CommandLine }"
        )
        try:
            result = subprocess.run(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        value = result.stdout.strip()
        return value or None

    proc = Path(f"/proc/{pid}/cmdline")
    if proc.is_file():
        try:
            return proc.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            return None
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _normalise_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value))


def _pid_matches(spec: ServiceSpec, state: dict[str, Any], pid: int) -> bool:
    if not pid or not _pid_alive(pid):
        return False
    command = _process_command_line(pid)
    if not command:
        return False
    entrypoint = str(state.get("entrypoint") or spec.entrypoint)
    expected = _normalise_path(entrypoint)
    actual = _normalise_path(command.replace("/", os.sep).replace("\\", os.sep))
    return expected in actual


def _verified_live_pid(spec: ServiceSpec, state: dict[str, Any]) -> int:
    """强制停止前使用：只返回命令行可核实的仍存活进程。"""
    for pid in (_state_pid(state), _state_worker_pid(state)):
        if _pid_matches(spec, state, pid):
            return pid
    return 0


def _kill_tree(pid: int) -> bool:
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0
    try:
        group = os.getpgid(pid)
    except OSError:
        group = 0
    try:
        if group > 0:
            os.killpg(group, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    if _wait_pid_dead(pid, FORCE_WAIT_SECONDS):
        return True
    try:
        if group > 0:
            os.killpg(group, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        return False
    return _wait_pid_dead(pid, FORCE_WAIT_SECONDS)


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------


def start_service(
    target: ServiceTarget, *, preload: bool, timeout: float
) -> str:
    spec = target.spec
    print(f"\n[start] {spec.label} ({spec.id})")
    print(f"  地址: {target.url}")
    print(f"  模型: {target.model_id}")
    print(f"  项目: {spec.project_display}")

    health, health_error = _fetch_health(target)
    if health is not None:
        if _worker_matches(health, spec):
            state = _read_state(spec)
            owned = _state_owned(state, health)
            worker_pid = _state_int(health, "pid")
            if owned and worker_pid and _state_worker_pid(state) != worker_pid:
                _update_state(spec, worker_pid=worker_pid)
            owner = "本工具启动" if owned else "外部进程"
            print(f"  已运行（{owner}），跳过启动。")
            return "already"
        print(
            f"  端口 {target.port} 已被其他服务占用（worker={health.get('worker') or '未知'}），"
            "拒绝启动。"
        )
        return "failed"
    if _port_open(target.host, target.port):
        print(
            f"  端口 {target.port} 已有监听，但 /health 不可用（{health_error}），"
            "拒绝启动。"
        )
        return "failed"

    state = _read_state(spec)
    live_pid = _state_live_pid(state)
    if live_pid:
        print(
            f"  已有启动记录（PID {live_pid}），健康检查尚未就绪；"
            "不重复启动。"
        )
        _print_tail(spec)
        return "pending"
    if state:
        _remove_state(spec)

    python = engine_python(spec)
    if python is None:
        print(
            "  未找到服务 venv。请先准备独立环境：\n"
            f"    cd {spec.project_display}\n"
            "    uv sync"
        )
        return "skipped"
    print(f"  环境: {python}")

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    out_path, err_path = log_paths(spec)
    print(f"  日志: {out_path}")
    instance_id = secrets.token_hex(16)
    control_token = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "NOVELVOICE_ENGINE_ID": spec.id,
        "NOVELVOICE_ENGINE_INSTANCE_ID": instance_id,
        "NOVELVOICE_ENGINE_CONTROL_TOKEN": control_token,
    }
    command = [
        str(python),
        str(spec.entrypoint),
        "--host",
        target.host,
        "--port",
        str(target.port),
    ]
    if preload:
        command.append("--preload")

    creation: dict[str, Any] = {}
    if os.name == "nt":
        creation["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        creation["start_new_session"] = True

    try:
        with out_path.open("ab", buffering=0) as stdout, err_path.open(
            "ab", buffering=0
        ) as stderr:
            process = subprocess.Popen(
                command,
                cwd=spec.project,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                **creation,
            )
    except OSError as exc:
        print(f"  启动失败: {exc}")
        return "failed"

    started_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    _write_state(
        spec,
        {
            "schema": 1,
            "id": spec.id,
            "label": spec.label,
            "pid": process.pid,
            "instance_id": instance_id,
            "token": control_token,
            "host": target.host,
            "port": target.port,
            "url": target.url,
            "model_id": target.model_id,
            "project": str(spec.project),
            "entrypoint": str(spec.entrypoint),
            "python": str(python),
            "preload": preload,
            "started_at": started_at,
            "stdout": str(out_path),
            "stderr": str(err_path),
        },
    )

    print(f"  PID {process.pid}，等待健康检查（最长 {timeout:.0f} 秒）...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            print(f"  进程提前退出，退出码 {code}。")
            _print_tail(spec)
            _remove_state(spec)
            return "failed"
        health, health_error = _fetch_health(target, timeout=1.0)
        if health is not None and _worker_matches(health, spec):
            worker_pid = _state_int(health, "pid")
            if worker_pid:
                _update_state(
                    spec,
                    worker_pid=worker_pid,
                    ready_at=dt.datetime.now(dt.timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                )
            summary = _health_summary(health)
            pid_text = f"，PID {worker_pid}" if worker_pid else ""
            print(
                f"  已就绪：{target.url}{pid_text}"
                + (f"（{summary}）" if summary else "")
            )
            return "started"
        time.sleep(0.5)

    print(f"  健康检查超时（{health_error}）。进程保留运行，请检查日志后重试。")
    _print_tail(spec)
    return "pending"


def _post_shutdown(target: ServiceTarget, token: str) -> tuple[int | None, str]:
    payload = json.dumps({"instance_id": "engine-control"}).encode("utf-8")
    try:
        status, raw = _http_request(
            target.host,
            target.port,
            "POST",
            "/v1/shutdown",
            body=payload,
            headers={
                "Content-Type": "application/json",
                "X-NVS-Engine-Token": token,
            },
            timeout=3.0,
        )
    except (OSError, http.client.HTTPException) as exc:
        return None, f"{exc.__class__.__name__}: {exc}"
    detail = raw.decode("utf-8", errors="replace").strip()
    return status, detail


def stop_service(
    target: ServiceTarget, *, force: bool, timeout: float
) -> str:
    spec = target.spec
    print(f"\n[stop] {spec.label} ({spec.id})")
    state = _read_state(spec)
    health, health_error = _fetch_health(target)

    if state is None:
        if health is not None and _worker_matches(health, spec):
            print("  运行中，但不是由本工具启动；不结束外部进程。")
            return "external"
        print("  未运行。")
        return "stopped"

    launcher_pid = _state_pid(state)
    worker_pid = _state_worker_pid(state)
    live_pid = _state_live_pid(state)
    if not live_pid:
        _remove_state(spec)
        print("  状态文件已过期，已清理；未结束任何进程。")
        return "stopped"

    # 刚启动时 /health 可能还没就绪，短暂等待，避免立刻失败。
    if health is None:
        deadline = time.monotonic() + min(5.0, max(0.0, timeout))
        while time.monotonic() < deadline:
            time.sleep(0.4)
            health, health_error = _fetch_health(target)
            if health is not None:
                break

    owned = _state_owned(state, health)
    if health is not None and _worker_matches(health, spec) and owned:
        token = str(state.get("token") or "")
        if not token:
            print("  状态文件缺少控制令牌，无法请求优雅停止。")
        else:
            status, detail = _post_shutdown(target, token)
            if status == 200:
                print("  已发送优雅停止请求。")
            elif status == 404:
                print("  服务未启用优雅停止接口（可能是旧版服务）。")
            elif status == 403:
                print("  优雅停止被拒绝：控制令牌不匹配。")
            else:
                suffix = f"：{detail[:160]}" if detail else ""
                print(f"  优雅停止请求失败（HTTP {status}）{suffix}")
    elif health is not None and _worker_matches(health, spec):
        print("  健康检查正常，但实例 ID 与启动记录不一致；不自动结束。")
    elif health is not None:
        print(
            f"  健康检查返回了其他服务（worker={health.get('worker') or '未知'}）；"
            "不自动结束。"
        )
    else:
        print(f"  健康检查不可用（{health_error}）。")

    if _wait_pids_dead([launcher_pid, worker_pid], timeout):
        _remove_state(spec)
        print("  已停止。")
        return "stopped"

    verified_pid = _verified_live_pid(spec, state)
    if force and verified_pid:
        print(f"  优雅停止未完成，正在强制结束 PID {verified_pid} ...")
        if _kill_tree(verified_pid) or _wait_pids_dead(
            [launcher_pid, worker_pid], 3.0
        ):
            _remove_state(spec)
            print("  已强制停止。")
            return "stopped"
        print("  强制结束失败，请人工核对进程。")
        return "failed"

    if force:
        print(
            "  拒绝强制结束：无法核实 PID 与命令行身份（实例 ID 匹配也不足以"
            "无条件结束进程）；请人工核对后处理。"
        )
    else:
        print(
            f"  进程仍在运行（PID {live_pid}）。请检查日志；确认无误后可加 --force，"
            "控制器只会在身份可核实时结束进程。"
        )
    return "failed"


def collect_status(targets: list[ServiceTarget]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in targets:
        spec = target.spec
        health, health_error = _fetch_health(target)
        state = _read_state(spec)
        launcher_pid = _state_pid(state)
        worker_pid = _state_worker_pid(state)
        live_pid = _state_live_pid(state)
        alive = bool(live_pid)
        owned = _state_owned(state, health)
        worker_ok = _worker_matches(health, spec)
        if health is not None and worker_ok:
            status = "running" if owned else "external"
            health_pid = _state_int(health, "pid")
            active_pid = health_pid or (live_pid if owned else 0)
        elif alive:
            status = "unhealthy"
            active_pid = live_pid
        elif state:
            status = "stale"
            active_pid = 0
        elif health is not None or _port_open(target.host, target.port):
            status = "conflict"
            active_pid = 0
        else:
            status = "stopped"
            active_pid = 0
        python = engine_python(spec)
        rows.append(
            {
                "id": spec.id,
                "label": spec.label,
                "description": spec.description,
                "url": target.url,
                "host": target.host,
                "port": target.port,
                "model_id": target.model_id,
                "config_url": spec.config_url,
                "config_model": spec.config_model,
                "project": spec.project_display,
                "entrypoint": str(spec.entrypoint),
                "python": str(python) if python else None,
                "python_ready": python is not None,
                "status": status,
                "pid": active_pid or None,
                "launcher_pid": (
                    launcher_pid
                    if launcher_pid and _pid_alive(launcher_pid)
                    else None
                ),
                "worker_pid": (
                    worker_pid if worker_pid and _pid_alive(worker_pid) else None
                ),
                "health_ok": health is not None,
                "health_error": health_error,
                "health": health,
                "state_file": str(state_path(spec)),
                "owned": owned,
                "log_files": [str(item) for item in log_paths(spec)],
            }
        )
    return rows


_STATUS_LABELS = {
    "running": "运行中（本工具启动）",
    "external": "运行中（外部进程）",
    "unhealthy": "进程存在，健康检查未就绪",
    "stale": "状态文件过期",
    "conflict": "端口被其他服务占用",
    "stopped": "未运行",
}


def _print_status_row(row: dict[str, Any]) -> None:
    print(f"\n{row['id']}  {row['label']}  [{_STATUS_LABELS[row['status']]}]")
    print(f"  地址: {row['url']}  ({row['config_url']})")
    print(f"  模型: {row['model_id']}")
    print(f"  项目: {row['project']}")
    if row["python_ready"]:
        print(f"  环境: 就绪 ({row['python']})")
    else:
        print("  环境: 未找到 venv（先在服务目录运行 uv sync）")
    if row["pid"]:
        launcher = row.get("launcher_pid")
        if launcher and launcher != row["pid"]:
            print(f"  PID: {row['pid']}（启动器 {launcher}）")
        else:
            print(f"  PID: {row['pid']}")
    if row["health"]:
        summary = _health_summary(row["health"])
        if summary:
            print(f"  健康: {summary}")
    elif row["health_error"] and row["status"] != "stopped":
        print(f"  健康: 不可用（{row['health_error']}）")
    print(f"  日志: {row['log_files'][0]}")


def command_list(specs: list[ServiceSpec]) -> int:
    print("workers/ 引擎服务目录")
    print(f"注册表: {MANIFEST_PATH}")
    print("\n生产入口：")
    for spec in specs:
        print(f"\n{spec.id}  {spec.label}")
        if spec.description:
            print(f"  {spec.description}")
        print(f"  项目: {spec.project_display}")
        print(f"  配置: {spec.config_url} / {spec.config_model}")
        if spec.capabilities:
            print(f"  能力: {'、'.join(spec.capabilities)}")
        if spec.endpoints:
            print(f"  接口: {'；'.join(spec.endpoints)}")
    return 0


def command_status(targets: list[ServiceTarget], *, as_json: bool) -> int:
    rows = collect_status(targets)
    if as_json:
        print(
            json.dumps(
                {
                    "manifest": str(MANIFEST_PATH),
                    "run_dir": str(RUN_DIR),
                    "services": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print("workers/ 引擎服务目录")
    print(f"注册表: {MANIFEST_PATH}")
    print(f"状态目录: {RUN_DIR}")
    for row in rows:
        _print_status_row(row)
    return 0


def command_start(
    targets: list[ServiceTarget], *, preload: bool, timeout: float
) -> int:
    results = [
        start_service(target, preload=preload, timeout=timeout) for target in targets
    ]
    print("\n启动结果：")
    for target, result in zip(targets, results, strict=True):
        print(f"  {target.spec.id}: {result}")
    return 0 if all(result in {"started", "already"} for result in results) else 1


def command_stop(targets: list[ServiceTarget], *, force: bool, timeout: float) -> int:
    results = [
        stop_service(target, force=force, timeout=timeout) for target in targets
    ]
    print("\n停止结果：")
    for target, result in zip(targets, results, strict=True):
        print(f"  {target.spec.id}: {result}")
    return 0 if all(result in {"stopped", "external"} for result in results) else 1


def command_restart(
    targets: list[ServiceTarget],
    *,
    preload: bool,
    start_timeout: float,
    stop_timeout: float,
    force: bool,
) -> int:
    stopped = command_stop(targets, force=force, timeout=stop_timeout)
    if stopped != 0:
        print("停止阶段未完成，取消重启。", file=sys.stderr)
        return stopped
    return command_start(targets, preload=preload, timeout=start_timeout)


def command_logs(specs: list[ServiceSpec], *, lines: int) -> int:
    for spec in specs:
        out_path, err_path = log_paths(spec)
        print(f"\n== {spec.id} ({spec.label}) ==")
        for label, path in (("stdout", out_path), ("stderr", err_path)):
            tail = _tail_lines(path, lines)
            print(f"-- {label}: {path}")
            if not tail:
                print("   （暂无日志）")
                continue
            for line in tail:
                print(f"   {line}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="novelvoice-studio worker",
        description="管理 workers/ 引擎服务目录中的本机服务。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出注册表中的引擎服务")
    list_parser.set_defaults(handler="list")

    status_parser = subparsers.add_parser("status", help="检查引擎服务状态")
    status_parser.add_argument("services", nargs="*", help="只检查指定服务 id")
    status_parser.add_argument("--json", action="store_true", help="输出 JSON")

    for name, help_text in (
        ("start", "启动引擎服务"),
        ("stop", "停止由本工具启动的引擎服务"),
        ("restart", "重启引擎服务"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("services", nargs="*", help="只操作指定服务 id")
        if name in {"start", "restart"}:
            sub.add_argument(
                "--preload", action="store_true", help="启动时预加载模型（更慢、更占显存）"
            )
        if name in {"stop", "restart"}:
            sub.add_argument(
                "--force",
                action="store_true",
                help="优雅停止失败后，仅在身份可核实时强制结束",
            )
        sub.add_argument(
            "--timeout",
            type=float,
            default=0.0,
            help="启动/停止等待秒数；0 表示使用默认值",
        )

    logs_parser = subparsers.add_parser("logs", help="查看引擎服务日志尾部")
    logs_parser.add_argument("services", nargs="*", help="只查看指定服务 id")
    logs_parser.add_argument("--lines", type=int, default=80, help="每个日志文件显示的行数")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        specs = load_manifest()
        if args.command == "list":
            return command_list(specs)
        selected = select_services(specs, getattr(args, "services", []))
        if args.command == "logs":
            lines = max(1, min(int(args.lines), 10000))
            return command_logs(selected, lines=lines)

        from novelvoice_studio.config import get_settings

        settings = get_settings()
        targets = resolve_targets(selected, settings)
        if args.command == "status":
            return command_status(targets, as_json=bool(args.json))
        if args.command == "start":
            timeout = args.timeout or (
                PRELOAD_TIMEOUT_SECONDS if args.preload else START_TIMEOUT_SECONDS
            )
            return command_start(targets, preload=args.preload, timeout=timeout)
        if args.command == "stop":
            timeout = args.timeout or STOP_TIMEOUT_SECONDS
            return command_stop(targets, force=args.force, timeout=timeout)
        if args.command == "restart":
            timeout = args.timeout or (
                PRELOAD_TIMEOUT_SECONDS if args.preload else START_TIMEOUT_SECONDS
            )
            return command_restart(
                targets,
                preload=args.preload,
                start_timeout=timeout,
                stop_timeout=STOP_TIMEOUT_SECONDS,
                force=args.force,
            )
    except (EngineServiceError, OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    parser.error(f"未知命令: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())