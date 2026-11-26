"""本机后台服务控制：启动、优雅停止与状态检查。

由 scripts/start.ps1 与 scripts/stop.ps1 调用。仅使用标准库，不依赖业务包之外
的第三方库；停止通过带随机令牌的本机 HTTP 接口触发，不结束任何无关 Python 进程。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / ".run"
STATE_PATH = RUN_DIR / "server.json"
LOG_PATH = RUN_DIR / "server.log"
HEALTH_TIMEOUT_SECONDS = 45.0
STOP_TIMEOUT_SECONDS = 15.0


def _load_settings():  # type: ignore[no-untyped-def]
    try:
        from novelvoice_studio.config import get_settings
    except ImportError as exc:  # pragma: no cover - 仅在人手直接运行时出现
        raise SystemExit(
            "无法导入 novelvoice_studio。请先运行 scripts/setup.ps1 安装依赖。"
        ) from exc
    return get_settings()


def _service_info() -> dict[str, Any]:
    settings = _load_settings()
    host = settings.app.host
    port = settings.app.port
    return {
        "root": str(ROOT),
        "host": host,
        "port": port,
        "url": f"http://{_url_host(host)}:{port}",
        "data_dir": str(settings.data_dir),
        "frontend_dist": str(settings.frontend_dist),
        "frontend_built": (settings.frontend_dist / "index.html").is_file(),
        "state_file": str(STATE_PATH),
        "log_file": str(LOG_PATH),
    }


def _read_state() -> dict[str, Any] | None:
    if not STATE_PATH.is_file():
        return None
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_state(payload: dict[str, Any]) -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_PATH)


def _remove_state() -> None:
    try:
        STATE_PATH.unlink()
    except FileNotFoundError:
        pass


# ---------- Windows 进程核对 ----------

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_TIMEOUT = 0x00000102
WAIT_OBJECT_0 = 0x00000000


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


def _query_process_windows(pid: int) -> dict[str, Any] | None:
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
    )
    if not handle:
        return None
    try:
        if kernel32.WaitForSingleObject(handle, 0) == WAIT_OBJECT_0:
            return None
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return None
        created = _FILETIME()
        exited = _FILETIME()
        kernel = _FILETIME()
        user = _FILETIME()
        created_time = 0
        if kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            created_time = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return {"exe": buffer.value, "create_time": created_time}
    finally:
        kernel32.CloseHandle(handle)


def _query_process_posix(pid: int) -> dict[str, Any] | None:
    proc = Path(f"/proc/{pid}")
    if not proc.is_dir():
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        return {"exe": "", "create_time": 0}
    try:
        exe = os.readlink(proc / "exe")
    except OSError:
        exe = ""
    return {"exe": exe, "create_time": 0}


def _query_process(pid: int) -> dict[str, Any] | None:
    if os.name == "nt":
        return _query_process_windows(pid)
    return _query_process_posix(pid)


def _same_exe(left: str, right: str) -> bool:
    if not left or not right:
        return False
    try:
        return str(Path(left).resolve()).casefold() == str(Path(right).resolve()).casefold()
    except OSError:
        return left.casefold() == right.casefold()


def _state_matches(state: dict[str, Any]) -> bool:
    try:
        pid = int(state.get("pid", 0))
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    current = _query_process(pid)
    if current is None:
        return False
    if not _same_exe(str(state.get("exe", "")), str(current.get("exe", ""))):
        return False
    expected_created = int(state.get("create_time") or 0)
    if expected_created and int(current.get("create_time") or 0) != expected_created:
        return False
    return True


def _terminate_verified(state: dict[str, Any]) -> bool:
    if not _state_matches(state):
        return False
    pid = int(state["pid"])
    if os.name == "nt":
        # venv 的 python.exe 在 Windows 上会再派生基础解释器；只杀父进程可能留下
        # 仍占着端口的子进程，因此对已核实的本项目进程树执行 taskkill /T。
        taskkill = shutil.which("taskkill")
        if not taskkill:
            return False
        try:
            result = subprocess.run(
                [taskkill, "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


# ---------- HTTP 辅助 ----------


def _urlopen(request: urllib.request.Request | str, timeout: float):  # type: ignore[no-untyped-def]
    # 显式禁用系统代理，避免 localhost 请求被代理转发。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _state_url(state: dict[str, Any]) -> str:
    host = str(state.get("host", "127.0.0.1"))
    return f"http://{_url_host(host)}:{state.get('port')}"


def _health_ok_url(url: str) -> bool:
    try:
        with _urlopen(f"{url}/api/v1/health", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and payload.get("status") == "ok"
    except (OSError, ValueError, urllib.error.URLError):
        return False


def _health_ok(info: dict[str, Any]) -> bool:
    return _health_ok_url(str(info["url"]))


def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.8):
            return True
    except OSError:
        return False


def _request_shutdown(state: dict[str, Any]) -> bool:
    token = str(state.get("token", ""))
    if not token:
        return False
    url = f"{_state_url(state)}/api/v1/system/shutdown"
    request = urllib.request.Request(
        url,
        method="POST",
        headers={"X-NVS-Control": token},
    )
    try:
        with _urlopen(request, timeout=5) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404, 409):
            return False
        return exc.code < 500
    except (OSError, urllib.error.URLError):
        # 服务可能在响应前已经开始关闭。
        return True


def _wait_for_exit(state: dict[str, Any], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _state_matches(state):
            return True
        time.sleep(0.25)
    return not _state_matches(state)


# ---------- 命令 ----------


def cmd_info(_args: argparse.Namespace) -> int:
    print(json.dumps(_service_info(), ensure_ascii=False))
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    info = _service_info()
    state = _read_state()
    if state is None or not _state_matches(state):
        print(json.dumps({**info, "running": False, "healthy": False}, ensure_ascii=False))
        return 1
    healthy = _health_ok_url(_state_url(state))
    print(
        json.dumps(
            {
                **info,
                "running": True,
                "healthy": healthy,
                "pid": state.get("pid"),
                "started_at": state.get("started_at"),
                "url": _state_url(state),
            },
            ensure_ascii=False,
        )
    )
    return 0 if healthy else 1


def _start_workers() -> None:
    """后台拉起 workers/ 引擎服务；失败不影响主服务启动。"""
    if os.environ.get("NOVELVOICE_SKIP_WORKERS") == "1":
        return
    log_path = RUN_DIR / "workers" / "autostart.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log_file:
            log_file.write(
                f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} worker autostart =====\n".encode(
                    "utf-8"
                )
            )
            log_file.flush()
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import sys\n"
                    "from novelvoice_studio.engine_services import main\n"
                    "sys.exit(main(['start']))\n",
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=(
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                    if os.name == "nt"
                    else 0
                ),
            )
    except Exception as exc:  # noqa: BLE001
        print(f"引擎服务自动启动失败（不影响主服务）：{exc}", file=sys.stderr)


def cmd_start(_args: argparse.Namespace) -> int:
    info = _service_info()
    if not info["frontend_built"]:
        print(
            "前端尚未构建：请先运行 scripts/setup.ps1，或在 frontend 目录执行 npm run build。",
            file=sys.stderr,
        )
        return 1

    existing = _read_state()
    if existing is not None:
        if _state_matches(existing):
            existing_url = _state_url(existing)
            if (
                existing.get("host") != info["host"]
                or existing.get("port") != info["port"]
            ):
                print(
                    f"检测到本项目进程仍在旧地址 {existing_url} 运行，当前配置为 {info['url']}。"
                    "请先运行 scripts/stop.ps1，再按新配置启动。",
                    file=sys.stderr,
                )
                return 1
            if _health_ok_url(existing_url):
                print(f"ALREADY_RUNNING pid={existing.get('pid')} url={existing_url}")
                _start_workers()
                return 2
            print(
                f"检测到本项目进程 pid={existing.get('pid')}，但健康检查失败。"
                f"请运行 scripts/stop.ps1 或查看 {LOG_PATH}。",
                file=sys.stderr,
            )
            return 1
        _remove_state()

    if _port_in_use(str(info["host"]), int(info["port"])):
        print(
            f"端口 {info['port']} 已被占用。请先运行 scripts/stop.ps1，"
            "或检查是否有其他程序占用该端口。",
            file=sys.stderr,
        )
        return 1

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env["NOVELVOICE_CONTROL_TOKEN"] = token
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    creationflags = 0
    if os.name == "nt":
        # 脱离启动终端独立运行；停止走受控 HTTP 接口，不依赖控制台信号。
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    with LOG_PATH.open("ab") as log_file:
        log_file.write(
            f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start =====\n".encode("utf-8")
        )
        log_file.flush()
        process = subprocess.Popen(
            [sys.executable, "-m", "novelvoice_studio"],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=creationflags,
        )

    current = _query_process(process.pid) or {}
    state = {
        "pid": process.pid,
        "exe": current.get("exe") or sys.executable,
        "create_time": current.get("create_time") or 0,
        "host": info["host"],
        "port": info["port"],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "token": token,
        "log_file": str(LOG_PATH),
    }
    _write_state(state)

    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            print(f"后端启动失败（退出码 {process.returncode}）。最近日志：", file=sys.stderr)
            _print_log_tail()
            _remove_state()
            return 1
        if _health_ok(info):
            print(f"STARTED pid={process.pid} url={info['url']}")
            _start_workers()
            return 0
        time.sleep(0.5)

    print(f"等待健康检查超时（{HEALTH_TIMEOUT_SECONDS:.0f}s）。最近日志：", file=sys.stderr)
    _print_log_tail()
    _request_shutdown(state)
    if not _wait_for_exit(state, 5):
        _terminate_verified(state)
    _remove_state()
    return 1


def cmd_stop(args: argparse.Namespace) -> int:
    info = _service_info()
    state = _read_state()
    if state is None:
        if _port_in_use(str(info["host"]), int(info["port"])):
            print(
                f"未找到本项目 PID 记录，但端口 {info['port']} 仍被占用；"
                "为避免误杀，请自行确认占用进程。",
                file=sys.stderr,
            )
            return 1
        print("NOT_RUNNING")
        return 0
    if not _state_matches(state):
        _remove_state()
        print("STALE_PID 已清理过期的 PID 记录，未结束任何进程。")
        return 0

    requested = _request_shutdown(state)
    if _wait_for_exit(state, STOP_TIMEOUT_SECONDS):
        _remove_state()
        print(f"STOPPED pid={state.get('pid')}")
        return 0

    if args.force:
        terminated = _terminate_verified(state)
        if terminated and _wait_for_exit(state, 5):
            _remove_state()
            print(f"FORCED_STOP pid={state.get('pid')}")
            return 0
        print("强制停止失败，请检查进程状态。", file=sys.stderr)
        return 1

    reason = "停机接口未接受请求" if not requested else "进程未在超时内退出"
    print(
        f"未能优雅停止 pid={state.get('pid')}（{reason}）。"
        "请查看日志；如确认无误可运行 stop.ps1 -Force。",
        file=sys.stderr,
    )
    return 1


def _print_log_tail(lines: int = 30) -> None:
    if not LOG_PATH.is_file():
        return
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in text.splitlines()[-lines:]:
        print(f"  {line}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NovelVoice Studio 本机服务控制")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("info", help="输出配置与路径")
    subparsers.add_parser("status", help="检查后台服务状态")
    subparsers.add_parser("start", help="启动后台服务")
    stop_parser = subparsers.add_parser("stop", help="优雅停止后台服务")
    stop_parser.add_argument("--force", action="store_true", help="优雅失败后强制结束")
    args = parser.parse_args(argv)
    handlers = {
        "info": cmd_info,
        "status": cmd_status,
        "start": cmd_start,
        "stop": cmd_stop,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())