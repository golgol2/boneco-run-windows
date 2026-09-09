#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent if ROOT.name == "core" else ROOT
BROWSER = PROJECT_ROOT / "platform" / "windows" / "chatgpt_browser.py"
ADAPTER = ROOT / "chatgpt-browser-adapter.py"


def default_windows_state_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA", "").strip()
    if base:
        return Path(base) / "BonecoRunWindows" / "state"
    return Path.home() / "AppData" / "Local" / "BonecoRunWindows" / "state"


if os.name == "nt":
    STATE_DIR = Path(
        os.environ.get(
            "BONECO_STATE_DIR",
            str(default_windows_state_dir()),
        )
    )
else:
    STATE_DIR = Path(
        os.environ.get(
            "BONECO_STATE_DIR",
            str(Path.home() / ".cache" / "boneco-auto-run-dev"),
        )
)
PROJECT_DIR_FILE = STATE_DIR / "project_dir"
PROJECT_CONFIG_FILE = STATE_DIR / "project-config.json"
WINDOWS_CONFIG_FILE = STATE_DIR / "windows-webview-config.json"
DEFAULT_PROJECT_DIR = ROOT.parent if os.name == "nt" else Path("/media/allana/Dados240/BONECO_GAME")

HOST = os.environ.get("BONECO_LOCAL_API_HOST", "127.0.0.1")
PORT = int(os.environ.get("BONECO_LOCAL_API_PORT", "8765"))
MAX_BODY_BYTES = int(
    os.environ.get("BONECO_LOCAL_API_MAX_BODY_BYTES", "262144")
)
DEFAULT_TIMEOUT = float(
    os.environ.get("BONECO_LOCAL_API_TIMEOUT", "120")
)
MAX_TIMEOUT = float(
    os.environ.get("BONECO_LOCAL_API_MAX_TIMEOUT", "600")
)
PROJECT_CONTEXT_KEYS = (
    "project_url",
    "work_url",
    "site_url",
    "target_url",
    "url",
)
CHATGPT_URL_KEYS = (
    "chatgpt_url",
    "chatgpt_web_url",
    "conversation_url",
)
API_CHAT_PATHS = {
    "/chat",
    "/message",
    "/api/chat",
    "/v1/chat/completions",
}
PROJECT_CHAT_PATHS = {
    "/project/chat",
    "/project/message",
    "/v1/project/chat",
}

chat_lock = threading.Lock()


class LocalApiError(RuntimeError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def clean_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if limit is not None and len(text) > limit:
        return text[:limit]
    return text


def windows_config() -> dict[str, Any]:
    try:
        value = json.loads(WINDOWS_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(value, dict):
        return {}

    return value


def valid_chatgpt_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except Exception:
        return False

    return (
        parsed.scheme == "https"
        and parsed.hostname in {
            "chatgpt.com",
            "www.chatgpt.com",
        }
    )


def run_command(
    command: list[str],
    *,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    command = [clean_text(part) for part in command]
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        **kwargs,
    )


def parse_key_value_output(text: str) -> dict[str, str]:
    result: dict[str, str] = {}

    for line in text.splitlines():
        key, separator, value = line.partition("=")

        if separator:
            result[clean_text(key)] = clean_text(value)

    return result


def extract_marked_block(
    output: str,
    start_marker: str,
    end_marker: str,
) -> str:
    if start_marker not in output or end_marker not in output:
        raise LocalApiError(
            "resposta do adaptador sem marcador esperado",
            status=502,
        )

    return output.split(start_marker, 1)[1].rsplit(end_marker, 1)[0].strip()


def extract_exchange_response(output: str) -> str:
    return extract_marked_block(
        output,
        "----- RESPONSE -----\n",
        "\n----- END RESPONSE -----",
    )


def api_base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def api_chat_endpoint(host: str, port: int) -> str:
    return api_base_url(host, port) + "/chat"


def configured_chatgpt_url(channel: str = "project") -> str:
    key = "chatgpt_api_url" if channel == "api" else "chatgpt_project_url"
    configured = clean_text(windows_config().get(key))
    if configured:
        return configured

    result = run_command(
        [
            sys.executable,
            str(BROWSER),
            "--channel",
            channel,
            "--get-url",
        ],
        timeout=5,
    )

    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    return "https://chatgpt.com/"


def configured_project_dir() -> str:
    configured = clean_text(windows_config().get("project_dir"))
    if configured:
        try:
            candidate = Path(configured).expanduser()
            if candidate.is_dir():
                return str(candidate.resolve())
        except (OSError, ValueError):
            pass

    try:
        raw = PROJECT_DIR_FILE.read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        raw = ""

    if raw:
        candidate = Path(raw).expanduser()

        if candidate.is_dir():
            return str(candidate.resolve())

    if DEFAULT_PROJECT_DIR.is_dir():
        return str(DEFAULT_PROJECT_DIR.resolve())

    return ""


def configured_project_url(project_dir: str) -> str:
    configured = clean_text(windows_config().get("project_target_url"))
    if configured:
        return configured

    if not project_dir:
        return ""

    try:
        config = json.loads(
            PROJECT_CONFIG_FILE.read_text(
                encoding="utf-8",
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return ""

    entry = config.get("projects", {}).get(project_dir, {})
    return str(entry.get("project_url") or "").strip()


def save_chatgpt_url(url: str, channel: str = "project") -> None:
    url = clean_text(url)
    if not valid_chatgpt_url(url):
        raise LocalApiError(
            "url deve ser HTTPS e pertencer a chatgpt.com",
            status=400,
        )

    result = run_command(
        [
            sys.executable,
            str(BROWSER),
            "--channel",
            channel,
            "--set-url",
            url,
        ],
        timeout=10,
    )

    if result.returncode != 0:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        raise LocalApiError(
            "falha ao salvar URL do ChatGPT: " + detail,
            status=502,
        )


def ensure_browser_running(
    url: str | None = None,
    *,
    channel: str = "project",
) -> dict[str, str]:
    if url:
        save_chatgpt_url(url, channel)

    result = run_command(
        [
            sys.executable,
            str(BROWSER),
            "--channel",
            channel,
        ],
        timeout=15,
    )

    if result.returncode != 0:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        raise LocalApiError(
            "falha ao abrir ChatGPT Browser: " + detail,
            status=502,
        )

    deadline = time.monotonic() + 20.0
    last_status = parse_key_value_output(result.stdout)

    while time.monotonic() < deadline:
        status_result = run_command(
            [
                sys.executable,
                str(BROWSER),
                "--channel",
                channel,
                "--status",
            ],
            timeout=5,
        )

        if status_result.returncode == 0:
            last_status = parse_key_value_output(status_result.stdout)

            if last_status.get("cdp_ready") == "true":
                return last_status

        time.sleep(0.5)

    raise LocalApiError(
        "ChatGPT Browser abriu, mas CDP não ficou pronto",
        status=502,
    )


def clamp_timeout(value: Any) -> float:
    if value in (None, ""):
        return DEFAULT_TIMEOUT

    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise LocalApiError("timeout inválido", status=400)

    if timeout <= 0:
        raise LocalApiError("timeout deve ser maior que zero", status=400)

    return min(timeout, MAX_TIMEOUT)


def message_from_payload(payload: Any) -> str:
    if isinstance(payload, str):
        message = clean_text(payload, 80_000)
    elif isinstance(payload, dict):
        message = clean_text(
            payload.get("message")
            or payload.get("text")
            or payload.get("prompt")
            or "",
            80_000,
        )

        if not message and isinstance(payload.get("messages"), list):
            parts = []
            for item in payload["messages"]:
                if not isinstance(item, dict):
                    continue
                content = item.get("content", "")
                content = clean_text(content, 80_000)
                if content:
                    role = clean_text(item.get("role") or "user")
                    parts.append(f"{role}: {content}")
            message = "\n\n".join(parts)
    else:
        message = ""

    if not isinstance(message, str) or not message.strip():
        raise LocalApiError("campo message/text/prompt obrigatório", status=400)

    return clean_text(message, 80_000)


def chatgpt_url_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""

    for key in CHATGPT_URL_KEYS:
        value = clean_text(payload.get(key))

        if value:
            return value

    return ""


def project_context_from_payload(payload: Any) -> dict[str, str]:
    project_dir = configured_project_dir()
    context: dict[str, str] = {}

    if project_dir:
        context["project_dir"] = project_dir
        project_url = configured_project_url(project_dir)

        if project_url:
            context["project_url"] = project_url

    if not isinstance(payload, dict):
        return context

    for key in PROJECT_CONTEXT_KEYS:
        value = clean_text(payload.get(key))

        if value:
            context[key] = value

    payload_project_dir = clean_text(payload.get("project_dir"))

    if payload_project_dir:
        context["project_dir"] = payload_project_dir

    return context


def message_with_project_context(
    message: str,
    payload: Any,
) -> tuple[str, dict[str, str]]:
    context = project_context_from_payload(payload)

    if not context:
        return message, context

    labels = {
        "project_url": "URL App/Site alvo",
        "work_url": "URL de trabalho",
        "site_url": "URL do site",
        "target_url": "URL alvo",
        "url": "URL informada pelo usuário",
        "project_dir": "Pasta do projeto",
    }

    lines = [
        message,
        "",
        "Contexto de trabalho informado pelo usuário:",
    ]

    for key, value in context.items():
        lines.append(f"- {labels.get(key, key)}: {value}")

    return "\n".join(lines).strip(), context


def chatgpt_exchange(
    message: str,
    *,
    timeout: float,
    channel: str = "project",
) -> tuple[str, str]:
    result = run_command(
        [
            sys.executable,
            str(ADAPTER),
            "exchange",
            "--text",
            message,
            "--timeout",
            str(timeout),
            "--channel",
            channel,
        ],
        timeout=timeout + 20,
    )

    if result.returncode != 0:
        detail = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )
        raise LocalApiError(
            "falha no intercâmbio com ChatGPT: " + detail,
            status=502,
        )

    return extract_exchange_response(result.stdout), result.stdout


def chat_channel_from_path(path: str) -> str:
    if path in PROJECT_CHAT_PATHS:
        return "project"

    return "api"


def chat_completion_payload(
    *,
    request_id: str,
    response: str,
) -> dict[str, Any]:
    created = int(time.time())

    return {
        "id": request_id,
        "object": "chat.completion",
        "created": created,
        "model": "boneco-local-chatgpt",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response,
                },
                "finish_reason": "stop",
            }
        ],
    }


def status_payload(
    *,
    host: str,
    port: int,
) -> dict[str, Any]:
    api_result = run_command(
        [
            sys.executable,
            str(BROWSER),
            "--channel",
            "api",
            "--status",
        ],
        timeout=5,
    )
    project_result = run_command(
        [
            sys.executable,
            str(BROWSER),
            "--channel",
            "project",
            "--status",
        ],
        timeout=5,
    )

    api_browser = (
        parse_key_value_output(api_result.stdout)
        if api_result.returncode == 0
        else {
            "status_error": (
                api_result.stderr.strip()
                or api_result.stdout.strip()
                or f"exit {api_result.returncode}"
            )
        }
    )
    project_browser = (
        parse_key_value_output(project_result.stdout)
        if project_result.returncode == 0
        else {
            "status_error": (
                project_result.stderr.strip()
                or project_result.stdout.strip()
                or f"exit {project_result.returncode}"
            )
        }
    )
    project_dir = configured_project_dir()

    return {
        "ok": api_result.returncode == 0 and project_result.returncode == 0,
        "service": "boneco-local-api",
        "api": {
            "base_url": api_base_url(host, port),
            "chat_endpoint": api_chat_endpoint(host, port),
        },
        "host": host,
        "port": port,
        "busy": chat_lock.locked(),
        "project": {
            "dir": project_dir,
        },
        "target": {
            "url": configured_project_url(project_dir),
        },
        "chatgpt_api": {
            "channel": "api",
            "url": configured_chatgpt_url("api"),
            "browser": api_browser,
        },
        "chatgpt_project": {
            "channel": "project",
            "url": configured_chatgpt_url("project"),
            "browser": project_browser,
        },
        "endpoints": [
            "GET /health",
            "GET /status",
            "POST /chat",
            "POST /project/chat",
            "POST /v1/chat/completions",
            "POST /v1/project/chat",
        ],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "BonecoLocalAPI/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(
            "[boneco-local-api] "
            + fmt % args
            + "\n"
        )

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type",
        )
        super().end_headers()

    def write_json(
        self,
        status: int,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.write_json(200, {"ok": True})

    def do_GET(self) -> None:
        if self.path == "/health":
            self.write_json(
                200,
                {
                    "ok": True,
                    "service": "boneco-local-api",
                },
            )
            return

        if self.path == "/status":
            host, port = self.server.server_address[:2]
            self.write_json(
                200,
                status_payload(
                    host=str(host),
                    port=int(port),
                ),
            )
            return

        self.write_json(
            404,
            {
                "ok": False,
                "error": "rota não encontrada",
            },
        )

    def read_payload(self) -> Any:
        length = int(self.headers.get("Content-Length") or "0")

        if length <= 0:
            raise LocalApiError("corpo da requisição obrigatório", status=400)

        if length > MAX_BODY_BYTES:
            raise LocalApiError("corpo da requisição muito grande", status=413)

        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")

        if "application/json" in content_type:
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise LocalApiError(
                    "JSON inválido: " + str(exc),
                    status=400,
                ) from exc

        return raw.decode("utf-8").strip()

    def do_POST(self) -> None:
        if self.path not in API_CHAT_PATHS | PROJECT_CHAT_PATHS:
            self.write_json(
                404,
                {
                    "ok": False,
                    "error": "rota não encontrada",
                },
            )
            return

        request_id = "local-api-" + str(time.time_ns())

        try:
            payload = self.read_payload()
            message = message_from_payload(payload)
            message, project_context = message_with_project_context(
                message,
                payload,
            )
            timeout = clamp_timeout(
                payload.get("timeout")
                if isinstance(payload, dict)
                else None
            )
            chatgpt_url = chatgpt_url_from_payload(payload)
            chat_channel = chat_channel_from_path(self.path)

            if not chat_lock.acquire(blocking=False):
                raise LocalApiError(
                    "ChatGPT já está processando outra mensagem",
                    status=409,
                )

            try:
                browser = ensure_browser_running(
                    chatgpt_url or None,
                    channel=chat_channel,
                )
                response, raw = chatgpt_exchange(
                    message,
                    timeout=timeout,
                    channel=chat_channel,
                )
            finally:
                chat_lock.release()

            if self.path == "/v1/chat/completions":
                self.write_json(
                    200,
                    chat_completion_payload(
                        request_id=request_id,
                        response=response,
                    ),
                )
                return

            self.write_json(
                200,
                {
                    "ok": True,
                    "id": request_id,
                    "response": response,
                    "api": {
                        "chat_endpoint": api_chat_endpoint(
                            str(self.server.server_address[0]),
                            int(self.server.server_address[1]),
                        ),
                    },
                    "project_context": project_context,
                    (
                        "chatgpt_project"
                        if chat_channel == "project"
                        else "chatgpt_api"
                    ): {
                        "channel": chat_channel,
                        "url": configured_chatgpt_url(chat_channel),
                        "browser": browser,
                    },
                    "raw_length": len(raw),
                },
            )
        except LocalApiError as exc:
            self.write_json(
                exc.status,
                {
                    "ok": False,
                    "id": request_id,
                    "error": str(exc),
                },
            )
        except Exception as exc:
            self.write_json(
                500,
                {
                    "ok": False,
                    "id": request_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )


def serve(host: str, port: int) -> int:
    server = ThreadingHTTPServer(
        (host, port),
        Handler,
    )

    print(f"status=running")
    print(f"host={host}")
    print(f"port={port}")
    print(f"api_url={api_base_url(host, port)}")
    print(f"endpoint={api_chat_endpoint(host, port)}")
    sys.stdout.flush()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="API local de texto do BONECO RUN para ChatGPT Browser"
    )
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument(
        "--check",
        action="store_true",
    )
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost"}:
        print("status=error")
        print("erro=host permitido apenas em 127.0.0.1/localhost")
        return 2

    if args.check:
        print("status=ok")
        print(f"host={args.host}")
        print(f"port={args.port}")
        print(f"api_url={api_base_url(args.host, args.port)}")
        print(f"endpoint={api_chat_endpoint(args.host, args.port)}")
        print(f"chatgpt_api_browser_url={configured_chatgpt_url('api')}")
        return 0

    return serve(args.host, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
