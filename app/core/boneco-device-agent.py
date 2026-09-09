#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
if ROOT.name == "core":
    PROJECT_ROOT = ROOT.parent
else:
    PROJECT_ROOT = ROOT

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
            str(Path.home() / ".cache/boneco-auto-run-dev"),
        )
    )

SERVER_URL = os.environ.get(
    "BONECO_AGENT_SERVER_URL",
    "https://run.oboneco.com.br",
).rstrip("/")
TOKEN_FILE = Path(
    os.environ.get(
        "BONECO_AGENT_TOKEN_FILE",
        str(STATE_DIR / "mobile-boneco-token"),
    )
)
JOB_CONTROLLER = ROOT / "boneco-job-controller.py"
ACTIVITY_FILE = STATE_DIR / "activity_status"
POLL_INTERVAL_SECONDS = float(os.environ.get("BONECO_AGENT_POLL_INTERVAL", "3"))
HTTP_TIMEOUT_SECONDS = float(os.environ.get("BONECO_AGENT_HTTP_TIMEOUT", "20"))
MAX_FINISH_OUTPUT_CHARS = int(os.environ.get("BONECO_AGENT_MAX_FINISH_OUTPUT", "180000"))


class AgentError(RuntimeError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_user_text(value: str, limit: int = 240) -> str:
    text = re.sub(r"```[\s\S]*?```", "[bloco tecnico omitido]", str(value or ""))
    lines = [
        line
        for line in text.splitlines()
        if not re.match(r"^\s*#?\s*BONECO_", line.strip())
        and not re.match(r"^\s*(editor_result|send_result)=", line.strip())
        and not re.match(r"^\s*-{3,}", line.strip())
    ]
    text = " ".join("\n".join(lines).split()).strip()

    if len(text) > limit:
        text = text[:limit].rstrip() + "..."

    return text


def clipped_output(value: str) -> str:
    text = str(value or "")

    if len(text) <= MAX_FINISH_OUTPUT_CHARS:
        return text

    half = MAX_FINISH_OUTPUT_CHARS // 2
    removed = len(text) - (half * 2)
    return (
        text[:half]
        + f"\n\n[... {removed} caracteres omitidos pelo agente Boneco ...]\n\n"
        + text[-half:]
    )


def extract_between_markers(text: str, start: str, end: str) -> str:
    if start not in text or end not in text:
        return ""
    return text.split(start, 1)[1].rsplit(end, 1)[0].strip()


def final_response_from_output(output: str) -> str:
    return extract_between_markers(
        output,
        "----- FINAL RESPONSE -----\n",
        "\n----- END FINAL RESPONSE -----",
    )


def mobile_finish_output(exit_code: int, output: str) -> str:
    final_response = final_response_from_output(output)

    if final_response:
        return clipped_output(final_response)

    relevant_lines: list[str] = []
    for line in str(output or "").splitlines():
        clean = line.strip()
        if not clean:
            continue
        if clean.startswith(("job_status=", "erro=", "job_activity=", "job_step=")):
            relevant_lines.append(clean)

    if relevant_lines:
        return clipped_output("\n".join(relevant_lines[-40:]))

    fallback = "Tarefa finalizada no computador."
    if exit_code != 0:
        fallback = f"Tarefa finalizada com erro {exit_code}."
    return fallback


def token() -> str:
    try:
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AgentError(f"token do Boneco não encontrado: {TOKEN_FILE}") from exc

    if not value:
        raise AgentError(f"token do Boneco vazio: {TOKEN_FILE}")

    return value


def write_activity(
    state: str,
    *,
    task_id: str = "",
    step: str = "",
    started_ms: int = 0,
    finished_ms: int = 0,
    exit_code: str | int = "",
) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not started_ms:
        started_ms = now_ms()
    duration_ms = max(0, finished_ms - started_ms) if finished_ms else 0
    payload = (
        f"state={state}\n"
        f"started_ms={started_ms}\n"
        f"finished_ms={finished_ms}\n"
        f"exit_code={exit_code}\n"
        f"duration_ms={duration_ms}\n"
        f"event_id={task_id}\n"
        f"request_id={task_id}\n"
        f"step={safe_user_text(step)}\n"
        "mode=auto\n"
        "return_mode=status\n"
    )
    tmp = ACTIVITY_FILE.with_name(ACTIVITY_FILE.name + f".agent.tmp.{os.getpid()}")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(ACTIVITY_FILE)


def api_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {
        "Authorization": "Bearer " + token(),
        "Content-Type": "application/json",
    }

    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = Request(
        SERVER_URL + path,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except URLError as exc:
        raise AgentError("gateway indisponível: " + str(exc.reason)) from exc

    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise AgentError("gateway retornou JSON inválido") from exc

    if status >= 400:
        raise AgentError(str(payload.get("error") or f"HTTP {status}"))

    return payload


def next_task() -> dict[str, Any] | None:
    payload = api_request("GET", "/v1/bonecos/tasks/next")
    task = payload.get("task")

    if not isinstance(task, dict):
        return None

    return task


def post_task_status(task_id: str, current_step: str, job_status: str = "") -> None:
    api_request(
        "POST",
        f"/v1/bonecos/tasks/{task_id}/status",
        {
            "status": "running",
            "current_step": safe_user_text(current_step),
            "job_status": safe_user_text(job_status, 80),
        },
    )


def finish_task(task_id: str, exit_code: int, output: str) -> None:
    api_request(
        "POST",
        f"/v1/bonecos/tasks/{task_id}/finish",
        {
            "exit_code": exit_code,
            "output": mobile_finish_output(exit_code, output),
        },
    )


def prompt_file_for(task_id: str, prompt: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    prompt_dir = STATE_DIR / "agent-prompts"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    path = prompt_dir / (re.sub(r"[^A-Za-z0-9_.-]", "", task_id) + ".txt")
    path.write_text(prompt, encoding="utf-8")
    path.chmod(0o600)
    return path


def windows_prompt(prompt: str) -> str:
    text = str(prompt or "")
    replacements = {
        "um bloco Bash autocontido": "um bloco PowerShell autocontido",
        "um bloco Bash": "um bloco PowerShell",
        "Bash valido": "PowerShell valido",
        "Bash válido": "PowerShell valido",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return (
        text
        + " ATENCAO WINDOWS: este computador executa PowerShell. "
        "Use bloco ```powershell com comandos compativeis com Windows. "
        "Nao use Bash, apt, systemctl, caminhos Linux ou comandos destrutivos."
    )


def resolve_project_dir_from_prompt(prompt: str) -> Path:
    patterns = [
        r"Pasta autorizada do projeto:\s*(.+?)(?:\. Transporte atual:| Transporte atual:|$)",
        r"Pasta autorizada:\s*(.+?)(?:\. Transporte atual:| Transporte atual:|$)",
    ]

    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue

        value = " ".join(match.group(1).split()).strip().strip('"')
        if value and not value.startswith(("/tmp/", "/media/")):
            path = Path(value)
            if path.is_dir():
                return path

    default = Path(
        os.environ.get(
            "BONECO_PROJECT_DIR",
            str(PROJECT_ROOT),
        )
    )
    return default if default.is_dir() else PROJECT_ROOT


def run_task(task: dict[str, Any], transport: str) -> int:
    task_id = str(task.get("task_id") or "").strip()
    prompt = windows_prompt(str(task.get("prompt") or ""))

    if not task_id:
        raise AgentError("tarefa sem task_id")
    if not prompt:
        raise AgentError("tarefa sem prompt")
    if not JOB_CONTROLLER.is_file():
        raise AgentError(f"controlador não encontrado: {JOB_CONTROLLER}")

    started_ms = now_ms()
    output_parts: list[str] = []
    project_dir = resolve_project_dir_from_prompt(prompt)
    prompt_path = prompt_file_for(task_id, prompt)
    log_path = STATE_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]', '', task_id)}.agent.log"

    write_activity(
        "running",
        task_id=task_id,
        step="Comando recebido pelo celular. Abrindo IA do projeto no computador",
        started_ms=started_ms,
    )
    post_task_status(task_id, "Abrindo IA do projeto no computador")

    command = [
        sys.executable,
        str(JOB_CONTROLLER),
        "--transport",
        transport,
        "--prompt-file",
        str(prompt_path),
    ]
    env = os.environ.copy()
    env["BONECO_STATE_DIR"] = str(STATE_DIR)
    env["BONECO_JOB_PROJECT_DIR"] = str(project_dir)
    env["BONECO_JOB_SOURCE"] = "mobile"

    with log_path.open("w", encoding="utf-8") as log_file:
        popen_options = {}
        if os.name == "nt":
            popen_options["creationflags"] = getattr(
                subprocess,
                "CREATE_NO_WINDOW",
                0,
            )
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(project_dir),
            env=env,
            **popen_options,
        )

        assert process.stdout is not None

        for line in process.stdout:
            log_file.write(line)
            log_file.flush()
            output_parts.append(line)
            clean = line.strip()

            if clean.startswith("job_activity="):
                step = clean.split("=", 1)[1].strip()
                write_activity("running", task_id=task_id, step=step, started_ms=started_ms)
                post_task_status(task_id, step)
            elif clean.startswith("job_step="):
                step = "Preparando próxima etapa no computador"
                write_activity("running", task_id=task_id, step=step, started_ms=started_ms)
                post_task_status(task_id, step)
            elif clean.startswith("job_status="):
                post_task_status(task_id, "Processando resposta final", clean.split("=", 1)[1])

        exit_code = process.wait()

    output = "".join(output_parts)
    finish_task(task_id, exit_code, output)
    finished_ms = now_ms()
    final_feedback = safe_user_text(mobile_finish_output(exit_code, output), 600)
    write_activity(
        "completed" if exit_code == 0 else "failed",
        task_id=task_id,
        step=final_feedback or "Tarefa mobile finalizada",
        started_ms=started_ms,
        finished_ms=finished_ms,
        exit_code=exit_code,
    )
    return exit_code


def run_loop(transport: str, once: bool = False) -> int:
    print("status=running")
    print(f"server_url={SERVER_URL}")
    print(f"state_dir={STATE_DIR}")
    print(f"token_file={TOKEN_FILE}")
    print(f"project_root={PROJECT_ROOT}")
    sys.stdout.flush()

    while True:
        try:
            task = next_task()
            if task is not None:
                print(f"task_claimed={task.get('task_id')}")
                sys.stdout.flush()
                run_task(task, transport)
            elif once:
                print("task=none")
                return 0
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            print("status=error")
            print("erro=" + message)
            write_activity("completed", step="Agente do Boneco falhou: " + message, exit_code=1)
            if once:
                return 1

        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Agente local do Boneco para tarefas mobile.")
    parser.add_argument("--transport", choices=("desktop", "browser"), default="browser")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        print("status=ok")
        print(f"server_url={SERVER_URL}")
        print(f"state_dir={STATE_DIR}")
        print(f"token_file_exists={TOKEN_FILE.is_file()}")
        print(f"job_controller_exists={JOB_CONTROLLER.is_file()}")
        print(f"project_root={PROJECT_ROOT}")
        return 0

    return run_loop(args.transport, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
