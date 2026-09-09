#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
WEB_DIR = APP_DIR / "web"
CORE_DIR = ROOT / "core"
PLATFORM_DIR = ROOT / "platform" / "windows"
APP_VERSION = "0.2.0"
DEFAULT_UPDATE_MANIFEST_URL = (
    "https://raw.githubusercontent.com/golgol2/boneco-run-windows/main/update/latest.json"
)


def default_state_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / "BonecoRunWindows" / "state"
        return Path.home() / "AppData" / "Local" / "BonecoRunWindows" / "state"

    return ROOT / "runtime" / "state"


STATE_DIR = Path(
    os.environ.get(
        "BONECO_WINDOWS_STATE_DIR",
        os.environ.get(
            "BONECO_STATE_DIR",
            str(default_state_dir()),
        ),
    )
)

CONFIG_FILE = STATE_DIR / "windows-webview-config.json"
ACTIVITY_FILE = STATE_DIR / "activity_status"
PAUSE_FILE = STATE_DIR / "paused"
TOKEN_FILE = STATE_DIR / "mobile-boneco-token"
JOB_PID_FILE = STATE_DIR / "active-job.pid"
AGENT_PID_FILE = STATE_DIR / "device-agent.pid"
LOCAL_API_PID_FILE = STATE_DIR / "local-api.pid"
OVERLAY_BRIDGE_PID_FILE = STATE_DIR / "overlay-bridge.pid"
DAEMON_PID_FILE = STATE_DIR / "windows-daemon.pid"

BROWSER_MANAGER = PLATFORM_DIR / "chatgpt_browser.py"
JOB_CONTROLLER = CORE_DIR / "boneco-job-controller.py"
DEVICE_AGENT = CORE_DIR / "boneco-device-agent.py"
LOCAL_API = CORE_DIR / "boneco-local-api.py"
BROWSER_OVERLAY = CORE_DIR / "chatgpt-browser-overlay.py"
BROWSER_OVERLAY_BRIDGE = CORE_DIR / "chatgpt-browser-overlay-bridge.py"

DEFAULT_CONFIG: dict[str, Any] = {
    "project_dir": "",
    "project_target_url": "",
    "gateway_url": "https://run.oboneco.com.br",
    "local_api_endpoint": "http://127.0.0.1:8765/chat",
    "chatgpt_project_url": "",
    "chatgpt_api_url": "",
    "access_scope": "computer",
    "update_manifest_url": DEFAULT_UPDATE_MANIFEST_URL,
}

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml; charset=utf-8",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def ensure_stdio() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if sys.stdout is None:
        sys.stdout = (STATE_DIR / "daemon.stdout.log").open(
            "a",
            encoding="utf-8",
            buffering=1,
        )

    if sys.stderr is None:
        sys.stderr = (STATE_DIR / "daemon.stderr.log").open(
            "a",
            encoding="utf-8",
            buffering=1,
        )


ensure_stdio()

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def clean_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if limit is not None and len(text) > limit:
        return text[:limit]
    return text


def now_ms() -> int:
    return int(time.time() * 1000)


def python_executable() -> str:
    bundled = ROOT / "runtime" / "python" / "python.exe"

    if bundled.is_file():
        return str(bundled)

    return sys.executable


def python_background_executable() -> str:
    bundled = ROOT / "runtime" / "python" / "pythonw.exe"

    if bundled.is_file():
        return str(bundled)

    return python_executable()


def default_project_dir() -> str:
    if os.name == "nt":
        candidates: list[Path] = []
        user_profile = Path(os.environ.get("USERPROFILE", "") or Path.home())
        candidates.extend(
            [
                user_profile / "Desktop" / "Nova pasta" / "BONECO_RUN_WINDOWS",
                user_profile / "OneDrive" / "Desktop" / "Nova pasta" / "BONECO_RUN_WINDOWS",
            ]
        )

        for candidate in candidates:
            if candidate.is_dir():
                return str(candidate)

        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                if candidate.is_dir():
                    return str(candidate)
            except OSError:
                continue

        desktop = user_profile / "Desktop"
        if desktop.is_dir():
            return str(desktop)

    return str(ROOT)


def current_windows_desktop_paths() -> list[str]:
    if os.name != "nt":
        return []

    paths: list[str] = []
    user_profile = Path(os.environ.get("USERPROFILE", "") or Path.home())

    for candidate in (
        user_profile / "Desktop",
        user_profile / "OneDrive" / "Desktop",
    ):
        try:
            if candidate.is_dir():
                paths.append(str(candidate))
        except OSError:
            continue

    return list(dict.fromkeys(paths))


def current_windows_user_locations() -> list[str]:
    if os.name != "nt":
        return []

    paths: list[str] = []
    user_profile = Path(os.environ.get("USERPROFILE", "") or Path.home())

    for candidate in (
        user_profile,
        user_profile / "Desktop",
        user_profile / "OneDrive" / "Desktop",
        user_profile / "Downloads",
        user_profile / "Documents",
        user_profile / "Pictures",
        user_profile / "Videos",
        user_profile / "Music",
    ):
        try:
            if candidate.is_dir():
                paths.append(str(candidate))
        except OSError:
            continue

    return list(dict.fromkeys(paths))


def drive_type_name(value: int) -> str:
    return {
        0: "unknown",
        1: "no_root",
        2: "removable",
        3: "fixed",
        4: "network",
        5: "cdrom",
        6: "ramdisk",
    }.get(value, "unknown")


def windows_logical_drives() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        bitmask = int(kernel32.GetLogicalDrives())
        drives: list[dict[str, Any]] = []

        for index in range(26):
            if not bitmask & (1 << index):
                continue

            root = f"{chr(65 + index)}:\\"
            drive_type = int(kernel32.GetDriveTypeW(root))
            if drive_type == 1:
                continue

            drives.append({
                "path": root,
                "type": drive_type_name(drive_type),
            })

        return drives
    except Exception:
        drives = []
        for index in range(26):
            root = Path(f"{chr(65 + index)}:/")
            try:
                if root.exists():
                    drives.append({"path": str(root), "type": "unknown"})
            except OSError:
                continue
        return drives


def machine_context(access_scope: str) -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "os_family": "windows" if os.name == "nt" else os.name,
        "os_name": platform.platform(),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "user_profile": os.environ.get("USERPROFILE", ""),
        "access_scope": access_scope,
        "drives": windows_logical_drives(),
        "user_locations": current_windows_user_locations(),
    }


def normalize_access_scope(value: object) -> str:
    scope = str(value or "").strip().lower()
    if scope in {"project", "user", "computer", "full"}:
        return scope
    return "computer"


def access_scope_label(scope: str) -> str:
    return {
        "project": "somente_projeto",
        "user": "perfil_do_usuario",
        "computer": "este_computador",
        "full": "full_acesso",
    }.get(scope, "este_computador")


def prompt_access_context(project_dir: str, scope: str) -> str:
    context = machine_context(scope)
    drives = context.get("drives") or []
    locations = context.get("user_locations") or []

    drive_text = "; ".join(
        f"{drive.get('path')} ({drive.get('type')})"
        for drive in drives
    ) or "nenhuma unidade detectada"
    location_text = "; ".join(str(item) for item in locations) or "nenhuma pasta conhecida"
    desktop_paths = current_windows_desktop_paths()
    desktop_text = (
        f"Area de Trabalho do usuario atual: {desktop_paths[0]}. "
        if desktop_paths
        else ""
    )

    if scope == "project":
        scope_text = (
            f"Escopo de acesso configurado: {access_scope_label(scope)}. "
            f"Pasta autorizada do projeto: {project_dir}. "
            f"{desktop_text}"
            "Trabalhe apenas na pasta do projeto, exceto se o usuario pedir outro caminho explicitamente e autorizar. "
        )
    elif scope == "user":
        scope_text = (
            f"Escopo de acesso configurado: {access_scope_label(scope)}. "
            f"Pasta autorizada do projeto: {project_dir}. "
            f"Pastas conhecidas do usuario atual: {location_text}. "
            f"{desktop_text}"
            "Pode trabalhar no perfil do usuario atual quando o pedido indicar Desktop, Downloads, Documentos, Imagens, Videos, Musicas ou outro caminho dentro do perfil. "
        )
    elif scope == "full":
        scope_text = (
            f"Escopo de acesso configurado: {access_scope_label(scope)}. "
            f"Pasta preferencial do projeto: {project_dir}. "
            f"Unidades detectadas neste computador: {drive_text}. "
            f"Pastas conhecidas do usuario atual: {location_text}. "
            f"{desktop_text}"
            "O usuario ativou Full acesso para este computador Windows. Pode acessar discos locais, removiveis, unidades mapeadas, processos, servicos, dependencias e arquivos quando necessario para cumprir o pedido. "
            "Nao peca confirmacao para criar, editar, instalar, iniciar, parar ou depurar quando isso fizer parte direta do objetivo. "
            "Ainda proteja segredos e nunca mostre senhas, tokens, cookies ou chaves. "
        )
    else:
        scope_text = (
            f"Escopo de acesso configurado: {access_scope_label(scope)}. "
            f"Pasta preferencial do projeto: {project_dir}. "
            f"Unidades detectadas neste computador: {drive_text}. "
            f"Pastas conhecidas do usuario atual: {location_text}. "
            f"{desktop_text}"
            "Pode acessar discos locais, removiveis e unidades mapeadas quando o usuario pedir uma unidade, pasta ou caminho neste computador. "
        )

    return (
        scope_text
        + "Se o usuario disser minha area de trabalho ou desktop, use somente a Area de Trabalho do usuario atual e nao pastas de outro usuario. "
        + "Nao peca autorizacao para criar, editar ou validar arquivos simples dentro do escopo solicitado pelo usuario. "
        + "Peca confirmacao antes de apagar, sobrescrever em massa, formatar, mexer em C:\\Windows, Program Files, AppData sensivel, chaves, tokens, senhas ou credenciais. "
    )


def windows_user_path_mismatch(project_dir: str) -> bool:
    if os.name != "nt":
        return False

    current_profile = os.environ.get("USERPROFILE", "").strip()
    if not current_profile:
        return False

    value = str(project_dir or "").strip().replace("/", "\\").rstrip("\\").lower()
    current = current_profile.replace("/", "\\").rstrip("\\").lower()

    return value.startswith("c:\\users\\") and not (
        value == current or value.startswith(current + "\\")
    )


def should_reset_project_dir(project_dir: str) -> bool:
    value = clean_text(project_dir)

    if not value:
        return True

    if windows_user_path_mismatch(value):
        return True

    if value.startswith("/tmp/") or value.startswith("/media/"):
        return True

    try:
        path = Path(value)
        root = ROOT.resolve()
        resolved = path.resolve()
    except (OSError, ValueError):
        return True

    if resolved == root or root in resolved.parents:
        return True

    return not path.is_dir()


def read_json_file(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(fallback)

    if not isinstance(value, dict):
        return dict(fallback)

    data = dict(fallback)
    data.update(value)
    return data


def write_json_file(path: Path, value: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def config() -> dict[str, Any]:
    data = read_json_file(CONFIG_FILE, DEFAULT_CONFIG)

    for key in (
        "project_dir",
        "project_target_url",
        "gateway_url",
        "local_api_endpoint",
        "chatgpt_project_url",
        "chatgpt_api_url",
        "access_scope",
        "update_manifest_url",
    ):
        if key in data:
            data[key] = clean_text(data.get(key))

    project_dir = clean_text(data.get("project_dir"))
    if should_reset_project_dir(project_dir):
        data["project_dir"] = default_project_dir()
        write_json_file(CONFIG_FILE, data)

    gateway_url = clean_text(data.get("gateway_url")).rstrip("/")
    data["gateway_url"] = gateway_url or DEFAULT_CONFIG["gateway_url"]
    data["access_scope"] = normalize_access_scope(data.get("access_scope"))
    data["update_manifest_url"] = clean_text(
        data.get("update_manifest_url") or DEFAULT_UPDATE_MANIFEST_URL
    )
    return data


def update_config(payload: dict[str, Any]) -> dict[str, Any]:
    data = config()

    for key in ("project_dir", "project_target_url", "gateway_url", "access_scope", "update_manifest_url"):
        if key in payload:
            data[key] = clean_text(payload.get(key))

    if not data["gateway_url"]:
        data["gateway_url"] = DEFAULT_CONFIG["gateway_url"]
    data["access_scope"] = normalize_access_scope(data.get("access_scope"))

    for channel, key in (("project", "chatgpt_project_url"), ("api", "chatgpt_api_url")):
        value = clean_text(payload.get(key))
        if value:
            result = run_python(
                BROWSER_MANAGER,
                ["--channel", channel, "--set-url", value],
                timeout=10,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                raise ApiError(detail or "URL ChatGPT invalida.", status=400)
            data[key] = value

    write_json_file(CONFIG_FILE, data)
    return data


def parse_key_values(text: str) -> dict[str, str]:
    result: dict[str, str] = {}

    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            result[clean_text(key)] = clean_text(value)

    return result


def no_window_run_kwargs() -> dict[str, Any]:
    if os.name != "nt":
        return {}

    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def run_python(
    script: Path,
    args: list[str],
    *,
    timeout: float = 20.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [clean_text(python_executable()), clean_text(script), *[clean_text(arg) for arg in args]]
    merged_env = {clean_text(key): clean_text(value) for key, value in os.environ.items()}
    merged_env["BONECO_STATE_DIR"] = str(STATE_DIR)

    if env:
        merged_env.update({clean_text(key): clean_text(value) for key, value in env.items()})

    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
        cwd=str(ROOT),
        env=merged_env,
        **no_window_run_kwargs(),
    )


def run_status_command(script: Path, args: list[str], timeout: float = 5.0) -> dict[str, Any]:
    try:
        result = run_python(script, args, timeout=timeout)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    values = parse_key_values(result.stdout)
    values["ok"] = result.returncode == 0
    values["exit_code"] = result.returncode

    if result.stderr.strip():
        values["stderr"] = result.stderr.strip()

    return values


def browser_channel_state(channel: str) -> dict[str, Any]:
    if channel == "api":
        return {
            "url_file": STATE_DIR / "chatgpt_api_url",
            "pid_file": STATE_DIR / "chatgpt_api_browser_pid",
            "profile_dir": default_state_dir().parent / "browser-profiles" / "chatgpt-api",
            "debug_port": 9228,
            "window_mode_file": STATE_DIR / "chatgpt_api_window_mode",
        }

    return {
        "url_file": STATE_DIR / "chatgpt_project_url",
        "pid_file": STATE_DIR / "chatgpt_project_browser_pid",
        "profile_dir": default_state_dir().parent / "browser-profiles" / "chatgpt-project",
        "debug_port": 9227,
        "window_mode_file": STATE_DIR / "chatgpt_project_window_mode",
    }


def read_optional_text(path: Path) -> str:
    try:
        if path.is_file():
            return clean_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""

    return ""


def browser_debug_status(port: int) -> dict[str, Any]:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.08):
            pass
    except OSError:
        return {}

    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.8) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}


def browser_session_saved(profile_dir: Path) -> bool:
    preferences = profile_dir / "Default" / "Preferences"
    cookie_paths = [
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Default" / "Cookies",
    ]

    if not preferences.is_file():
        return False

    for path in cookie_paths:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return True
        except OSError:
            continue

    return False


def browser_status_light(channel: str) -> dict[str, Any]:
    data = browser_channel_state(channel)
    pid = pid_from_file(data["pid_file"])
    port = int(data["debug_port"])
    profile_dir = data["profile_dir"]
    debug = browser_debug_status(port)
    url = read_optional_text(data["url_file"]) or "https://chatgpt.com/"
    window_mode = read_optional_text(data["window_mode_file"])

    payload: dict[str, Any] = {
        "ok": True,
        "running": bool(debug),
        "channel": channel,
        "port": port,
        "debug_port": port,
        "url": url,
        "profile_dir": str(profile_dir),
        "cdp_ready": bool(debug),
        "session_saved": browser_session_saved(profile_dir),
        "window_mode": window_mode,
    }

    if pid:
        payload["pid"] = pid
    if debug.get("Browser"):
        payload["browser"] = debug["Browser"]
    if debug.get("webSocketDebuggerUrl"):
        payload["websocket"] = debug["webSocketDebuggerUrl"]

    return payload


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False

    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True,
                capture_output=True,
                timeout=3,
                check=False,
                **no_window_run_kwargs(),
            )
            return str(pid) in result.stdout
        except Exception:
            return False

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_from_file(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def write_pid(path: Path, pid: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid) + "\n", encoding="utf-8")


def terminate_pid(pid: int | None) -> bool:
    if not process_alive(pid):
        return True

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
            **no_window_run_kwargs(),
        )
        return not process_alive(pid)

    assert pid is not None
    os.kill(pid, 15)
    deadline = time.monotonic() + 5

    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.1)

    return not process_alive(pid)


def popen_flags() -> dict[str, Any]:
    if os.name != "nt":
        return {"start_new_session": True}

    flags = 0
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags}


def start_background(
    script: Path,
    args: list[str],
    *,
    pid_file: Path,
    stdout_log: Path,
    stderr_log: Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> int:
    pid = pid_from_file(pid_file)

    if process_alive(pid):
        assert pid is not None
        return pid

    if not script.is_file():
        raise ApiError(f"arquivo nao encontrado: {script}", status=500)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    merged_env = {clean_text(key): clean_text(value) for key, value in os.environ.items()}
    merged_env["BONECO_STATE_DIR"] = str(STATE_DIR)
    merged_env["PYTHONUTF8"] = "1"
    merged_env["PYTHONIOENCODING"] = "utf-8"

    if env:
        merged_env.update({clean_text(key): clean_text(value) for key, value in env.items()})

    out = stdout_log.open("a", encoding="utf-8")
    err = stderr_log.open("a", encoding="utf-8")
    command = [
        clean_text(python_background_executable()),
        clean_text(script),
        *[clean_text(arg) for arg in args],
    ]
    working_dir = clean_text(cwd or ROOT)
    try:
        process = subprocess.Popen(
            command,
            cwd=working_dir,
            stdout=out,
            stderr=err,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=merged_env,
            **popen_flags(),
        )
    finally:
        out.close()
        err.close()

    write_pid(pid_file, process.pid)
    return process.pid


def read_activity() -> dict[str, str]:
    values: dict[str, str] = {}

    try:
        text = ACTIVITY_FILE.read_text(encoding="utf-8")
    except OSError:
        return values

    return parse_key_values(text)


def safe_status_text(value: str, limit: int = 240) -> str:
    text = re.sub(r"```[\s\S]*?```", "[bloco tecnico omitido]", str(value or ""))
    lines = [
        line
        for line in text.splitlines()
        if not re.match(r"^\s*#?\s*BONECO_", line.strip())
        and not re.match(r"^\s*(editor_result|send_result)=", line.strip())
        and not re.match(r"^\s*-{3,}", line.strip())
    ]
    cleaned = " ".join("\n".join(lines).split()).strip()

    if len(cleaned) > limit:
        return cleaned[:limit].rstrip() + "..."

    return cleaned


def write_activity(
    state: str,
    *,
    request_id: str = "",
    step: str = "",
    exit_code: str | int = "",
    started_ms: int | None = None,
    finished_ms: int = 0,
) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    if started_ms is None:
        started_ms = now_ms()

    duration = max(0, finished_ms - started_ms) if finished_ms else 0
    payload = (
        f"state={state}\n"
        f"started_ms={started_ms}\n"
        f"finished_ms={finished_ms}\n"
        f"exit_code={exit_code}\n"
        f"duration_ms={duration}\n"
        f"event_id={request_id}\n"
        f"request_id={request_id}\n"
        f"step={safe_status_text(step)}\n"
        "mode=auto\n"
        "return_mode=status\n"
    )
    tmp = ACTIVITY_FILE.with_name(ACTIVITY_FILE.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(ACTIVITY_FILE)


def build_pilot_prompt(goal: str, job_id: str, data: dict[str, Any] | None = None) -> str:
    data = data or config()
    project_dir = clean_text(data.get("project_dir") or ROOT)
    access_scope = normalize_access_scope(data.get("access_scope"))
    target_url = clean_text(data.get("project_target_url"))
    clean_goal = " ".join(clean_text(goal, 20_000).split())
    access_text = prompt_access_context(project_dir, access_scope)

    target_text = f"URL alvo do projeto: {target_url}. " if target_url else ""

    return (
        f"BONECO RUN Windows - trabalho {job_id}. "
        f"Objetivo do usuario: {clean_goal}. "
        f"{access_text}"
        f"{target_text}"
        "Transporte atual: ChatGPT Browser via Chrome/CDP no Windows. "
        "Voce e o cerebro; o Boneco Run Windows apenas executa e observa o computador. "
        "Nao altere Linux, servidores Linux ou arquivos de outro computador sem pedido explicito do usuario. "
        "Preserve alteracoes nao relacionadas, inspecione antes de modificar e prefira mudancas pequenas e testaveis. "
        "Nunca inclua senhas, tokens ou segredos. "
        "Ao criar ou editar HTML, texto, CSS, JS, JSON ou arquivos com conteudo em portugues, preserve acentos e c cedilha. "
        "Grave arquivos em UTF-8; em HTML inclua <meta charset=\"utf-8\">. "
        "No PowerShell, prefira Set-Content -Encoding UTF8 ou [System.IO.File]::WriteAllText com [System.Text.UTF8Encoding]::new($false). "
        "Quando precisar executar uma etapa, responda com # BONECO_JOB_ACTION e "
        f"# BONECO_JOB_ID: {job_id}, seguidos de exatamente um bloco PowerShell autocontido. "
        "Use bloco ```powershell. A primeira linha do bloco deve ser exatamente # BONECO_RUN. "
        "Use # BONECO_REQUEST_ID: <id-da-etapa>, # BONECO_STEP: <descricao>, "
        "# BONECO_RETURN: output e # BONECO_MODE: auto. "
        "Nao use Bash, apt, systemctl, sudo, caminhos Linux ou comandos destrutivos. "
        "Nao declare sucesso sem analisar o retorno real. "
        "Se faltar informacao ou autorizacao, responda com # BONECO_JOB_NEEDS_USER e o mesmo # BONECO_JOB_ID. "
        "Se receber o mesmo conteudo sem progresso por 4 ciclos, responda com # BONECO_JOB_BLOCKED para parar o loop. "
        "Somente quando todo o objetivo estiver concluido e validado, responda com # BONECO_JOB_DONE, "
        f"# BONECO_JOB_ID: {job_id}, # BONECO_SUMMARY e # BONECO_VALIDATION em linguagem curta para o usuario. "
        "Comece agora pela proxima acao necessaria."
    )


def start_job(goal: str) -> dict[str, Any]:
    goal = clean_text(goal, 20_000)

    if not goal:
        raise ApiError("Digite um objetivo antes de enviar.", status=400)

    active_pid = pid_from_file(JOB_PID_FILE)
    if process_alive(active_pid):
        raise ApiError("Ja existe uma tarefa em execucao.", status=409)

    if not JOB_CONTROLLER.is_file():
        raise ApiError("Controlador de jobs nao encontrado.", status=500)

    data = config()
    project_dir = clean_text(data.get("project_dir") or ROOT)
    browser_ready = ensure_browser("project", auto_overlay=True)
    if not browser_ready.get("ok"):
        raise ApiError(
            "ChatGPT Projeto nao ficou pronto: "
            + str(browser_ready.get("stderr") or browser_ready.get("stdout") or "erro desconhecido"),
            status=500,
        )

    job_id = "job-" + str(time.time_ns())
    prompt = build_pilot_prompt(goal, job_id, data)
    prompt_dir = STATE_DIR / "webview-prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompt_dir / f"{job_id}.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    try:
        PAUSE_FILE.unlink()
    except OSError:
        pass

    write_activity(
        "running",
        request_id=job_id,
        step="Enviando objetivo para a IA do projeto no Windows",
    )

    env = {
        "BONECO_JOB_PROJECT_DIR": project_dir,
        "BONECO_JOB_SOURCE": "windows-webview",
    }
    try:
        cwd_path = Path(project_dir)
        job_cwd = cwd_path if cwd_path.is_dir() else ROOT
    except (OSError, ValueError):
        job_cwd = ROOT

    pid = start_background(
        JOB_CONTROLLER,
        ["--transport", "browser", "--prompt-file", str(prompt_file)],
        pid_file=JOB_PID_FILE,
        stdout_log=STATE_DIR / f"{job_id}.stdout.log",
        stderr_log=STATE_DIR / f"{job_id}.stderr.log",
        cwd=job_cwd,
        env=env,
    )
    return {"ok": True, "job_id": job_id, "pid": pid}


def cancel_job() -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PAUSE_FILE.write_text("paused_by_user\n", encoding="utf-8")
    pid = pid_from_file(JOB_PID_FILE)
    stopped = terminate_pid(pid)
    write_activity(
        "blocked",
        request_id="job-cancelled",
        step="Tarefa cancelada pelo usuario",
        exit_code=82,
        finished_ms=now_ms(),
    )
    return {"ok": stopped, "pid": pid}


def local_api_health() -> dict[str, Any]:
    try:
        with urlopen("http://127.0.0.1:8765/health", timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
            payload["reachable"] = True
            return payload
    except Exception as exc:
        return {"ok": False, "reachable": False, "error": str(exc)}


def browser_status(channel: str) -> dict[str, Any]:
    return run_status_command(BROWSER_MANAGER, ["--channel", channel, "--status"])


def boolish(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "sim", "ok"}


def wait_for_browser_port(port: int, timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if browser_debug_status(port):
            return True
        time.sleep(0.25)

    return False


def stop_overlay_bridge_processes() -> int:
    stopped = 0
    existing = pid_from_file(OVERLAY_BRIDGE_PID_FILE)

    if existing and terminate_pid(existing):
        stopped += 1

    if os.name != "nt":
        return stopped

    root_b64 = base64.b64encode(str(ROOT).encode("utf-8")).decode("ascii")
    script = r"""
$ErrorActionPreference = "SilentlyContinue"
$root = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("__ROOT_B64__"))
$count = 0
Get-CimInstance Win32_Process |
    Where-Object {
        $_.CommandLine -like "*chatgpt-browser-overlay-bridge.py*" -and
        $_.CommandLine -like ("*" + $root + "*")
    } |
    ForEach-Object {
        try {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop
            $count += 1
        }
        catch {
        }
    }
Write-Output $count
""".replace("__ROOT_B64__", root_b64)

    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")

    try:
        result = subprocess.run(
            [
                powershell_executable(),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=8,
            check=False,
            **no_window_run_kwargs(),
        )
    except Exception:
        return stopped

    match = re.search(r"\d+", result.stdout or "")
    if match:
        stopped += int(match.group(0))

    return stopped


def start_overlay_bridge(port: int, *, force_restart: bool = False) -> dict[str, Any]:
    if not BROWSER_OVERLAY_BRIDGE.is_file():
        raise ApiError("Ponte do comando sobre ChatGPT nao encontrada.", status=500)

    existing = pid_from_file(OVERLAY_BRIDGE_PID_FILE)
    if existing and process_alive(existing) and not force_restart:
        return {
            "ok": True,
            "pid": existing,
            "status": {"status": "already_running", "port": str(port)},
        }

    stopped = stop_overlay_bridge_processes() if force_restart else 0

    if existing and not force_restart:
        terminate_pid(existing)

    if not wait_for_browser_port(port, timeout=12.0):
        return {
            "ok": False,
            "error": f"CDP do ChatGPT ainda nao esta pronto na porta {port}.",
            "status": {"status": "cdp_not_ready", "port": str(port)},
        }

    stdout_log = STATE_DIR / "overlay-bridge.stdout.log"
    stderr_log = STATE_DIR / "overlay-bridge.stderr.log"
    for log_path in (stdout_log, stderr_log):
        try:
            log_path.unlink()
        except OSError:
            pass

    pid = start_background(
        BROWSER_OVERLAY_BRIDGE,
        ["--port", str(port)],
        pid_file=OVERLAY_BRIDGE_PID_FILE,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
    )

    deadline = time.monotonic() + 7
    stdout_text = ""

    while time.monotonic() < deadline:
        stdout_text = tail_log("overlay-bridge.stdout.log", limit=8000).get("text", "")
        if "status=overlay_bridge_running" in stdout_text:
            break
        if not process_alive(pid):
            break
        time.sleep(0.25)

    return {
        "ok": process_alive(pid) and "status=overlay_bridge_running" in stdout_text,
        "pid": pid,
        "stdout": stdout_text,
        "stderr": tail_log("overlay-bridge.stderr.log", limit=8000).get("text", ""),
        "status": {
            **parse_key_values(stdout_text),
            "stopped_previous_bridges": str(stopped),
        },
    }


def ensure_overlay_for_authenticated_project(status: dict[str, str]) -> dict[str, Any]:
    if not boolish(status.get("session_saved")):
        return {
            "ok": False,
            "skipped": "session_not_saved",
        }

    port = int(status.get("debug_port") or status.get("port") or 9227)
    return start_overlay_bridge(port, force_restart=True)


def ensure_browser(channel: str, *, auto_overlay: bool = True) -> dict[str, Any]:
    result = run_python(BROWSER_MANAGER, ["--channel", channel], timeout=20)
    payload = {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "status": parse_key_values(result.stdout),
    }

    if auto_overlay and channel == "project" and payload["ok"]:
        payload["overlay"] = ensure_overlay_for_authenticated_project(payload["status"])

    return payload


def inject_browser_overlay(channel: str) -> dict[str, Any]:
    if channel not in {"project", "api"}:
        raise ApiError("Canal invalido.", status=400)

    ready = ensure_browser(channel, auto_overlay=False)
    if not ready.get("ok"):
        return ready

    browser = ready.get("status") or {}
    port = int(browser.get("debug_port") or browser.get("port") or ("9228" if channel == "api" else "9227"))
    return start_overlay_bridge(port, force_restart=True)


def open_browser_login(channel: str, mode: str) -> dict[str, Any]:
    if mode not in {"chatgpt", "google-profile"}:
        raise ApiError("Modo de login invalido.", status=400)

    result = run_python(
        BROWSER_MANAGER,
        [
            "--channel",
            channel,
            "--login",
            mode,
            "--width",
            "1100",
            "--height",
            "850",
        ],
        timeout=20,
    )
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "status": parse_key_values(result.stdout),
    }


def stop_browser(channel: str) -> dict[str, Any]:
    result = run_python(BROWSER_MANAGER, ["--channel", channel, "--stop"], timeout=12)
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def start_local_api() -> dict[str, Any]:
    pid = start_background(
        LOCAL_API,
        ["--host", "127.0.0.1", "--port", "8765"],
        pid_file=LOCAL_API_PID_FILE,
        stdout_log=STATE_DIR / "local-api.stdout.log",
        stderr_log=STATE_DIR / "local-api.stderr.log",
    )
    deadline = time.monotonic() + 8

    while time.monotonic() < deadline:
        health = local_api_health()
        if health.get("ok"):
            return {"ok": True, "pid": pid, "health": health}
        time.sleep(0.4)

    return {"ok": False, "pid": pid, "health": local_api_health()}


def token_value() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_token(value: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(value.strip() + "\n", encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def http_json(method: str, url: str, token: str = "", body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Content-Type": "application/json; charset=utf-8"}

    if token:
        headers["Authorization"] = "Bearer " + token

    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = Request(url, data=data, headers=headers, method=method)

    try:
        with urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except URLError as exc:
        raise ApiError("Gateway indisponivel: " + str(exc.reason), status=502) from exc

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise ApiError("Gateway retornou JSON invalido.", status=502) from exc

    if status >= 400:
        raise ApiError(str(payload.get("error") or f"HTTP {status}"), status=status)

    return payload


def stable_boneco_id() -> str:
    name = socket.gethostname() or platform.node() or "windows"
    safe = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")
    return "boneco-windows-" + (safe or "pc")


def register_mobile_gateway(payload: dict[str, Any]) -> dict[str, Any]:
    email = clean_text(payload.get("email"))
    password = clean_text(payload.get("password"))

    if not email or not password:
        raise ApiError("Informe e-mail e senha.", status=400)

    gateway = config()["gateway_url"].rstrip("/")
    login = http_json(
        "POST",
        gateway + "/v1/users/login",
        body={"email": email, "password": password},
    )
    user_token = str(login.get("token") or "")

    if not user_token:
        raise ApiError("Gateway nao retornou token de usuario.", status=502)

    registration = http_json(
        "POST",
        gateway + "/v1/bonecos/register",
        token=user_token,
        body={
            "boneco_id": stable_boneco_id(),
            "name": "BONECO RUN Windows - " + (platform.node() or socket.gethostname()),
            "os_family": "windows",
            "os_name": platform.platform(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "hostname": socket.gethostname(),
            "agent_version": f"windows-webview-{APP_VERSION}",
        },
    )
    boneco_token = str(registration.get("token") or "")

    if not boneco_token:
        raise ApiError("Gateway nao retornou token do Boneco.", status=502)

    save_token(boneco_token)
    public = dict(registration)
    public.pop("token", None)
    public["ok"] = True
    public["registered"] = True
    public["agent"] = start_mobile_agent()
    return public


def create_pairing() -> dict[str, Any]:
    token = token_value()

    if not token:
        raise ApiError("Registre este PC antes de gerar pareamento.", status=400)

    gateway = config()["gateway_url"].rstrip("/")
    result = http_json("POST", gateway + "/v1/pairing/create", token=token, body={})
    result["agent"] = start_mobile_agent()
    return result


def start_mobile_agent() -> dict[str, Any]:
    if not token_value():
        raise ApiError("Registre este PC antes de iniciar o agente mobile.", status=400)

    gateway = config()["gateway_url"].rstrip("/")
    pid = start_background(
        DEVICE_AGENT,
        ["--transport", "browser"],
        pid_file=AGENT_PID_FILE,
        stdout_log=STATE_DIR / "device-agent.stdout.log",
        stderr_log=STATE_DIR / "device-agent.stderr.log",
        env={"BONECO_AGENT_SERVER_URL": gateway},
    )
    return {"ok": True, "pid": pid, "gateway_url": gateway}


def stop_mobile_agent() -> dict[str, Any]:
    pid = pid_from_file(AGENT_PID_FILE)
    return {"ok": terminate_pid(pid), "pid": pid}


def powershell_executable() -> str:
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if candidate.is_file():
            return str(candidate)

    return "powershell.exe"


def select_folder_windows() -> dict[str, Any]:
    current = str(config().get("project_dir") or ROOT)
    if not Path(current).is_dir():
        current = str(Path(os.environ.get("USERPROFILE", "") or Path.home()))

    initial_b64 = base64.b64encode(current.encode("utf-8")).decode("ascii")
    script = r"""
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$initial = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String("__INITIAL_DIR_B64__"))
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Selecione a pasta do projeto"
$dialog.ShowNewFolderButton = $true

if (-not [string]::IsNullOrWhiteSpace($initial) -and (Test-Path -LiteralPath $initial -PathType Container)) {
    $dialog.SelectedPath = $initial
}

$owner = New-Object System.Windows.Forms.Form
$owner.Text = "BONECO RUN"
$owner.StartPosition = "CenterScreen"
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.Opacity = 0
$owner.Show()
$owner.Activate()

try {
    $result = $dialog.ShowDialog($owner)
    if ($result -eq [System.Windows.Forms.DialogResult]::OK -and -not [string]::IsNullOrWhiteSpace($dialog.SelectedPath)) {
        Write-Output $dialog.SelectedPath
        exit 0
    }

    exit 2
}
finally {
    $owner.Close()
    $owner.Dispose()
    $dialog.Dispose()
}
""".replace("__INITIAL_DIR_B64__", initial_b64)

    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")

    try:
        result = subprocess.run(
            [
                powershell_executable(),
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=300,
            check=False,
            cwd=str(ROOT),
            **no_window_run_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ApiError("Selecao de pasta expirou sem resposta do usuario.", status=408) from exc
    except Exception as exc:
        raise ApiError(f"Seletor de pasta indisponivel: {exc}", status=500) from exc

    if result.returncode == 2:
        return {"ok": True, "cancelled": True}

    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}")
        raise ApiError("Seletor de pasta indisponivel: " + detail, status=500)

    chosen = result.stdout.strip().splitlines()[-1].strip() if result.stdout.strip() else ""
    if not chosen:
        return {"ok": True, "cancelled": True}

    if not Path(chosen).is_dir():
        raise ApiError("Pasta selecionada nao existe ou nao esta acessivel.", status=400)

    data = update_config({"project_dir": chosen})
    return {"ok": True, "project_dir": data["project_dir"]}


def select_folder() -> dict[str, Any]:
    if os.name == "nt":
        return select_folder_windows()

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise ApiError(f"Seletor de pasta indisponivel: {exc}", status=500) from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    current = str(config().get("project_dir") or ROOT)
    chosen = filedialog.askdirectory(
        title="Selecione a pasta do projeto",
        initialdir=current if Path(current).is_dir() else str(ROOT),
    )
    root.destroy()

    if not chosen:
        return {"ok": True, "cancelled": True}

    data = update_config({"project_dir": chosen})
    return {"ok": True, "project_dir": data["project_dir"]}


def latest_job_log(kind: str) -> Path | None:
    activity = read_activity()
    request_id = str(activity.get("request_id") or "").strip()

    if request_id.startswith("job-"):
        candidate = STATE_DIR / f"{request_id}.{kind}.log"
        if candidate.is_file():
            return candidate

    candidates = list(STATE_DIR.glob(f"job-*.{kind}.log"))
    if not candidates:
        return None

    return max(candidates, key=lambda item: item.stat().st_mtime)


def decode_log_bytes(raw: bytes) -> str:
    if not raw:
        return ""

    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    for encoding in ("cp1252", "cp850", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace")


def read_tail_text(path: Path, limit: int = 160_000) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            raw = handle.read()
    except OSError:
        return ""

    return decode_log_bytes(raw)


def normalize_newlines(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def latest_final_response(log_text: str) -> str:
    text = normalize_newlines(log_text)
    start = "----- FINAL RESPONSE -----"
    end = "----- END FINAL RESPONSE -----"
    index = text.rfind(start)

    if index < 0:
        return ""

    index += len(start)
    end_index = text.find(end, index)
    if end_index < 0:
        end_index = len(text)

    return text[index:end_index].strip()


def final_marker_present(text: str, marker: str) -> bool:
    pattern = re.compile(rf"(?im)^\s*#?\s*{re.escape(marker)}\b")
    return bool(pattern.search(text))


def final_marker_value(text: str, marker: str, limit: int = 1800) -> str:
    lines = normalize_newlines(text).splitlines()
    collecting = False
    parts: list[str] = []
    marker_pattern = re.compile(rf"^#?\s*{re.escape(marker)}\b\s*:?\s*(.*)$", re.IGNORECASE)
    any_marker_pattern = re.compile(r"^#?\s*BONECO_[A-Z0-9_]+\b", re.IGNORECASE)

    for line in lines:
        stripped = line.strip()
        match = marker_pattern.match(stripped)
        if match:
            collecting = True
            first = match.group(1).strip()
            if first:
                parts.append(first)
            continue

        if collecting:
            if any_marker_pattern.match(stripped):
                break
            parts.append(stripped)

    return safe_status_text("\n".join(parts), limit)


def parse_final_job_result(log_text: str, default_job_id: str = "") -> dict[str, Any]:
    final = latest_final_response(log_text)

    if not final:
        return {"available": False}

    state = "unknown"
    for marker, value in (
        ("BONECO_JOB_DONE", "completed"),
        ("BONECO_JOB_NEEDS_USER", "needs_user"),
        ("BONECO_JOB_BLOCKED", "blocked"),
        ("BONECO_JOB_FAILED", "failed"),
    ):
        if final_marker_present(final, marker):
            state = value
            break

    job_id = final_marker_value(final, "BONECO_JOB_ID", 120) or default_job_id
    summary = final_marker_value(final, "BONECO_SUMMARY", 2200)
    validation = final_marker_value(final, "BONECO_VALIDATION", 2200)
    feedback = " ".join(part for part in (summary, validation) if part).strip()

    return {
        "available": True,
        "job_id": job_id,
        "state": state,
        "summary": summary,
        "validation": validation,
        "feedback": feedback or safe_status_text(final, 3000),
    }


def latest_job_result() -> dict[str, Any]:
    path = latest_job_log("stdout")

    if not path:
        return {"available": False}

    result = parse_final_job_result(read_tail_text(path), path.name.split(".")[0])
    result["stdout_log"] = path.name
    return result


def version_key(value: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", clean_text(value))]
    return tuple(parts or [0])


def is_newer_version(candidate: str, current: str) -> bool:
    left = list(version_key(candidate))
    right = list(version_key(current))
    length = max(len(left), len(right))
    left.extend([0] * (length - len(left)))
    right.extend([0] * (length - len(right)))
    return tuple(left) > tuple(right)


def update_payload_from_manifest(manifest: dict[str, Any], manifest_url: str) -> dict[str, Any]:
    latest_version = clean_text(
        manifest.get("version")
        or manifest.get("tag_name")
        or manifest.get("name")
        or ""
    ).lstrip("vV")
    download_url = clean_text(manifest.get("download_url"))
    sha256 = clean_text(manifest.get("sha256"))
    notes_value = manifest.get("notes") or manifest.get("body") or ""

    assets = manifest.get("assets")
    if not download_url and isinstance(assets, list):
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = clean_text(asset.get("name")).lower()
            candidate = clean_text(asset.get("browser_download_url") or asset.get("download_url"))
            if candidate and ("installer" in name or name.endswith(".exe")):
                download_url = candidate
                break

    if isinstance(notes_value, list):
        notes = "\n".join(clean_text(item) for item in notes_value if clean_text(item))
    else:
        notes = clean_text(notes_value, 2000)

    available = bool(latest_version) and is_newer_version(latest_version, APP_VERSION)
    return {
        "ok": True,
        "current_version": APP_VERSION,
        "latest_version": latest_version or APP_VERSION,
        "available": available,
        "download_url": download_url,
        "sha256": sha256,
        "notes": notes,
        "manifest_url": clean_text(manifest_url),
        "message": (
            f"Atualizacao disponivel: {latest_version}"
            if available
            else f"BONECO RUN Windows esta atualizado: {APP_VERSION}"
        ),
    }


def check_updates() -> dict[str, Any]:
    data = config()
    manifest_url = clean_text(data.get("update_manifest_url") or DEFAULT_UPDATE_MANIFEST_URL)

    if not manifest_url:
        return {
            "ok": True,
            "current_version": APP_VERSION,
            "available": False,
            "disabled": True,
            "message": "Verificacao de atualizacao sem URL configurada.",
        }

    try:
        request = Request(
            manifest_url,
            headers={
                "Accept": "application/json",
                "User-Agent": f"BonecoRunWindows/{APP_VERSION}",
            },
        )
        with urlopen(request, timeout=5) as response:
            manifest = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        return {
            "ok": False,
            "current_version": APP_VERSION,
            "available": False,
            "manifest_url": manifest_url,
            "error": f"{type(exc).__name__}: {exc}",
            "message": "Nao foi possivel verificar atualizacoes agora.",
        }

    if not isinstance(manifest, dict):
        return {
            "ok": False,
            "current_version": APP_VERSION,
            "available": False,
            "manifest_url": manifest_url,
            "error": "manifesto invalido",
            "message": "Manifesto de atualizacao invalido.",
        }

    return update_payload_from_manifest(manifest, manifest_url)


def resolve_log_path(name: str) -> tuple[str, Path | None]:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", name)

    if not safe:
        raise ApiError("Nome de log invalido.", status=400)

    aliases = {
        "daemon": "daemon-http.log",
        "daemon-http": "daemon-http.log",
        "launcher": "launcher.log",
        "tray": "tray.log",
        "agent": "device-agent.stdout.log",
        "agent-error": "device-agent.stderr.log",
        "api": "local-api.stdout.log",
        "api-error": "local-api.stderr.log",
    }

    if safe in {"current-job", "current-job.stdout", "current-job.stdout.log"}:
        return "current-job.stdout.log", latest_job_log("stdout")

    if safe in {"current-job-error", "current-job.stderr", "current-job.stderr.log"}:
        return "current-job.stderr.log", latest_job_log("stderr")

    resolved = aliases.get(safe, safe)
    return resolved, STATE_DIR / resolved


def combined_terminal_log(limit: int = 40_000) -> str:
    sections: list[tuple[str, Path | None]] = [
        ("atividade", ACTIVITY_FILE),
        ("job-atual-saida", latest_job_log("stdout")),
        ("job-atual-erro", latest_job_log("stderr")),
        ("daemon", STATE_DIR / "daemon-http.log"),
        ("launcher", STATE_DIR / "launcher.log"),
        ("bandeja", STATE_DIR / "tray.log"),
        ("agente-mobile-saida", STATE_DIR / "device-agent.stdout.log"),
        ("agente-mobile-erro", STATE_DIR / "device-agent.stderr.log"),
        ("overlay-chatgpt-saida", STATE_DIR / "overlay-bridge.stdout.log"),
        ("overlay-chatgpt-erro", STATE_DIR / "overlay-bridge.stderr.log"),
        ("api-local-saida", STATE_DIR / "local-api.stdout.log"),
        ("api-local-erro", STATE_DIR / "local-api.stderr.log"),
    ]
    parts: list[str] = []

    for title, path in sections:
        if not path or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.strip():
            continue
        clipped = text[-8000:]
        parts.append(f"===== {title}: {path.name} =====\n{clipped.rstrip()}")

    value = "\n\n".join(parts)
    return value[-limit:] if len(value) > limit else value


def tail_log(name: str, limit: int = 20_000) -> dict[str, Any]:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", name)

    if safe in {"terminal", "terminal.log", "all"}:
        return {
            "ok": True,
            "name": "terminal.log",
            "text": combined_terminal_log(max(limit, 40_000)) or "Nenhum log tecnico encontrado.",
        }

    resolved_name, path = resolve_log_path(safe)

    if not path or not path.is_file():
        return {"ok": True, "name": resolved_name, "text": ""}

    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > limit:
        text = text[-limit:]

    return {"ok": True, "name": resolved_name, "text": text}


def system_status() -> dict[str, Any]:
    data = config()
    job_pid = pid_from_file(JOB_PID_FILE)
    agent_pid = pid_from_file(AGENT_PID_FILE)
    api_pid = pid_from_file(LOCAL_API_PID_FILE)
    overlay_pid = pid_from_file(OVERLAY_BRIDGE_PID_FILE)
    activity = read_activity()
    job_running = process_alive(job_pid)
    try:
        activity_started_ms = int(str(activity.get("started_ms") or now_ms()))
    except ValueError:
        activity_started_ms = now_ms()

    if (
        str(activity.get("state") or "").lower() == "running"
        and str(activity.get("request_id") or "").startswith("job-")
        and not job_running
    ):
        write_activity(
            "failed",
            request_id=str(activity.get("request_id") or ""),
            step="Tarefa interrompida. Nenhum processo de execução está ativo no Windows",
            exit_code=85,
            started_ms=activity_started_ms,
            finished_ms=now_ms(),
        )
        activity = read_activity()

    local_api_running = process_alive(api_pid)
    local_api_status = (
        local_api_health()
        if local_api_running
        else {
            "ok": False,
            "reachable": False,
            "skipped": "local_api_not_running",
        }
    )

    return {
        "ok": True,
        "version": APP_VERSION,
        "root": str(ROOT),
        "python": python_executable(),
        "state_dir": str(STATE_DIR),
        "config": data,
        "machine": machine_context(str(data.get("access_scope") or "computer")),
        "activity": activity,
        "job": {
            "pid": job_pid,
            "running": job_running,
        },
        "last_job": latest_job_result(),
        "mobile": {
            "token_exists": bool(token_value()),
            "agent_pid": agent_pid,
            "agent_running": process_alive(agent_pid),
            "gateway_url": data["gateway_url"],
        },
        "local_api": {
            "pid": api_pid,
            "running": local_api_running,
            "health": local_api_status,
        },
        "browser": {
            "project": browser_status_light("project"),
            "api": browser_status_light("api"),
        },
        "overlay": {
            "pid": overlay_pid,
            "running": process_alive(overlay_pid),
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "BonecoRunWindowsWebView/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / "daemon-http.log").open("a", encoding="utf-8") as handle:
            handle.write("[%s] %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), fmt % args))

    def send_common_headers(self, status: int, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_common_headers(status, "application/json; charset=utf-8")
        self.wfile.write(raw)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")

        if length <= 0:
            return {}

        if length > 2_000_000:
            raise ApiError("Corpo da requisicao muito grande.", status=413)

        raw = self.rfile.read(length)

        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError("JSON invalido: " + str(exc), status=400) from exc

        if not isinstance(payload, dict):
            raise ApiError("JSON deve ser um objeto.", status=400)

        return payload

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            file_path = WEB_DIR / "index.html"
        else:
            safe = path.lstrip("/")
            file_path = (WEB_DIR / safe).resolve()
            if WEB_DIR.resolve() not in file_path.parents and file_path != WEB_DIR.resolve():
                self.write_json(403, {"ok": False, "error": "acesso negado"})
                return

        if not file_path.is_file():
            self.write_json(404, {"ok": False, "error": "arquivo nao encontrado"})
            return

        content_type = CONTENT_TYPES.get(file_path.suffix, "application/octet-stream")
        self.send_common_headers(200, content_type)
        self.wfile.write(file_path.read_bytes())

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/health":
                self.write_json(200, {"ok": True, "service": "boneco-windows-webview"})
                return

            if path == "/api/status":
                self.write_json(200, system_status())
                return

            if path == "/api/logs":
                query = parse_qs(parsed.query)
                name = (query.get("name") or ["daemon-http.log"])[0]
                self.write_json(200, tail_log(name))
                return

            if path == "/api/update/check":
                self.write_json(200, check_updates())
                return

            self.serve_static(path)
        except ApiError as exc:
            self.write_json(exc.status, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.write_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def do_OPTIONS(self) -> None:
        self.send_common_headers(204, "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            payload = self.read_json()

            if path == "/api/config":
                self.write_json(200, {"ok": True, "config": update_config(payload)})
                return

            if path == "/api/project/select-folder":
                self.write_json(200, select_folder())
                return

            if path == "/api/browser/ensure":
                channel = str(payload.get("channel") or "project")
                if channel not in {"project", "api"}:
                    raise ApiError("Canal invalido.", status=400)
                self.write_json(200, ensure_browser(channel))
                return

            if path == "/api/browser/login":
                channel = str(payload.get("channel") or "project")
                mode = str(payload.get("mode") or "chatgpt")
                if channel not in {"project", "api"}:
                    raise ApiError("Canal invalido.", status=400)
                self.write_json(200, open_browser_login(channel, mode))
                return

            if path == "/api/browser/overlay":
                channel = str(payload.get("channel") or "project")
                self.write_json(200, inject_browser_overlay(channel))
                return

            if path == "/api/browser/stop":
                channel = str(payload.get("channel") or "project")
                if channel not in {"project", "api"}:
                    raise ApiError("Canal invalido.", status=400)
                self.write_json(200, stop_browser(channel))
                return

            if path == "/api/local-api/start":
                self.write_json(200, start_local_api())
                return

            if path == "/api/mobile/register":
                self.write_json(200, register_mobile_gateway(payload))
                return

            if path == "/api/mobile/pairing":
                self.write_json(200, create_pairing())
                return

            if path == "/api/mobile/agent/start":
                self.write_json(200, start_mobile_agent())
                return

            if path == "/api/mobile/agent/stop":
                self.write_json(200, stop_mobile_agent())
                return

            if path == "/api/job/start":
                self.write_json(202, start_job(str(payload.get("goal") or "")))
                return

            if path == "/api/job/cancel":
                self.write_json(200, cancel_job())
                return

            if path == "/api/system/shutdown":
                self.write_json(200, {"ok": True, "shutdown": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return

            self.write_json(404, {"ok": False, "error": "rota nao encontrada"})
        except ApiError as exc:
            self.write_json(exc.status, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self.write_json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def write_daemon_pid() -> None:
    write_pid(DAEMON_PID_FILE, os.getpid())


def serve(host: str, port: int) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json_file(CONFIG_FILE, config())
    write_daemon_pid()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"status=running")
    print(f"url=http://{host}:{port}")
    print(f"root={ROOT}")
    print(f"state_dir={STATE_DIR}")
    sys.stdout.flush()

    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BONECO RUN Windows WebView daemon")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BONECO_WINDOWS_PORT", "8791")))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        print("status=ok")
        print(f"root={ROOT}")
        print(f"state_dir={STATE_DIR}")
        print(f"python={python_executable()}")
        print(f"browser_manager_exists={BROWSER_MANAGER.is_file()}")
        print(f"job_controller_exists={JOB_CONTROLLER.is_file()}")
        print(f"device_agent_exists={DEVICE_AGENT.is_file()}")
        print(f"local_api_exists={LOCAL_API.is_file()}")
        return 0

    return serve(args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
