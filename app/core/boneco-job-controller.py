#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import locale
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "boneco-clipboard-runner"
WINDOWS_RUNNER = ROOT / "boneco-clipboard-runner.ps1"
ATSPI = ROOT / "chatgpt-atspi.py"
BROWSER_ADAPTER = ROOT / "chatgpt-browser-adapter.py"
STATIC_SITE_QUALITY = ROOT / "boneco-static-site-quality.py"
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
PAUSE_FILE = STATE_DIR / "paused"
ACTIVITY_FILE = STATE_DIR / "activity_status"


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()

    if not value:
        return default

    try:
        return int(value)
    except ValueError:
        return default


MAX_STEPS = env_int("BONECO_JOB_MAX_STEPS", 0)
JOB_TIMEOUT_SECONDS = env_int("BONECO_JOB_TIMEOUT_SECONDS", 0)
COMMAND_TIMEOUT_SECONDS = env_int("BONECO_JOB_COMMAND_TIMEOUT_SECONDS", 10 * 60)
MAX_REPEATED_CONTENT = env_int("BONECO_JOB_MAX_REPEATED_CONTENT", 4)
MAX_RESULT_CHARS = 48_000


class JobError(RuntimeError):
    pass


class LoopDetected(JobError):
    pass


def decode_process_output(data: bytes | str) -> str:
    if isinstance(data, str):
        return data
    if not data:
        return ""

    encodings = [
        "utf-8",
        locale.getpreferredencoding(False),
        "cp850",
        "cp1252",
        "latin-1",
    ]
    tried: set[str] = set()

    for encoding in encodings:
        if not encoding:
            continue
        normalized = encoding.lower()
        if normalized in tried:
            continue
        tried.add(normalized)
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    return data.decode("utf-8", errors="replace")


def now_ms() -> int:
    return int(time.time() * 1000)


def safe_status_text(value: str, limit: int = 260) -> str:
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


def adapter_error_summary(output: str, fallback: str) -> str:
    text = str(output or "")

    if "validation=login_required" in text:
        return "ChatGPT precisa de login no perfil do BONECO RUN Windows"

    validation = ""
    errors: list[str] = []

    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if clean.startswith("validation="):
            validation = clean.split("=", 1)[1].strip()
        elif clean.startswith("erro="):
            errors.append(clean.split("=", 1)[1].strip())

    if errors:
        return safe_status_text(errors[-1])

    if validation:
        return "Falha no ChatGPT Browser/CDP: " + validation

    return safe_status_text(fallback)


def write_activity_status(
    state: str,
    *,
    job_id: str = "",
    step: str = "",
    started_ms: int = 0,
    finished_ms: int = 0,
    exit_code: str | int = "",
) -> None:
    try:
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
            f"event_id={job_id}\n"
            f"request_id={job_id}\n"
            f"step={safe_status_text(step)}\n"
            "mode=auto\n"
            "return_mode=status\n"
        )
        tmp = ACTIVITY_FILE.with_name(ACTIVITY_FILE.name + f".job.tmp.{os.getpid()}")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(ACTIVITY_FILE)
    except OSError:
        pass


def extract_final_feedback(response: str) -> str:
    text = str(response or "").strip()
    summary = re.search(
        r"(?is)#\s*BONECO_SUMMARY:?\s*(.*?)(?:\n\s*#\s*BONECO_VALIDATION:?\s*|$)",
        text,
    )
    validation = re.search(
        r"(?is)#\s*BONECO_VALIDATION:?\s*(.*)$",
        text,
    )

    parts: list[str] = []

    if summary:
        cleaned_summary = safe_status_text(summary.group(1), 600)
        if cleaned_summary:
            parts.append(cleaned_summary)

    if validation:
        cleaned_validation = safe_status_text(validation.group(1), 600)
        if cleaned_validation:
            parts.append(cleaned_validation)

    if parts:
        return " ".join(parts)

    return safe_status_text(text, 600)


def extract_job_id(text: str) -> str:
    match = re.search(r"\bjob-\d+\b", text)
    if match is None:
        raise JobError("JOB_ID ausente no prompt inicial")
    return match.group(0)


def normalize_newlines(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def extract_exchange_response(output: str) -> str:
    output = normalize_newlines(output)
    marker = "----- RESPONSE -----\n"
    end_marker = "\n----- END RESPONSE -----"
    if marker not in output or end_marker not in output:
        raise JobError("resposta não encontrada no retorno da IA")
    return output.split(marker, 1)[1].rsplit(end_marker, 1)[0].strip()


def extract_code_response(output: str) -> str:
    output = normalize_newlines(output)
    marker = "----- CODE -----\n"
    end_marker = "\n----- END CODE -----"
    if marker not in output or end_marker not in output:
        raise JobError("bloco de código não encontrado no retorno da IA")
    return output.split(marker, 1)[1].rsplit(end_marker, 1)[0].strip()


def response_outside_code(response: str) -> str:
    response = normalize_newlines(response)
    return re.sub(r"```.*?```", "", response, flags=re.DOTALL)


def response_state(response: str) -> str:
    outside = response_outside_code(response)
    matches = re.findall(
        r"(?mi)^\s*#?\s*BONECO_JOB_"
        r"(ACTION|DONE|NEEDS_USER|BLOCKED|FAILED)\s*$",
        outside,
    )
    if len(matches) != 1:
        raise JobError(
            "a resposta deve conter exatamente um estado BONECO_JOB"
        )
    return matches[0]


def validate_response_job_id(response: str, expected: str) -> None:
    outside = response_outside_code(response)
    matches = re.findall(
        r"(?mi)^\s*#?\s*BONECO_JOB_ID:\s*(job-\d+)\s*$",
        outside,
    )
    if not matches:
        raise JobError("BONECO_JOB_ID ausente na resposta")
    if len(matches) != 1:
        raise JobError("a resposta deve conter exatamente um BONECO_JOB_ID")
    if matches[0] != expected:
        raise JobError(
            f"BONECO_JOB_ID divergente: {matches[0]}"
        )


def extract_action_code(response: str) -> str:
    blocks = re.findall(
        r"```(?:bash|sh|shell|powershell|ps1|pwsh)\s*\n(.*?)```",
        response,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if len(blocks) != 1:
        raise JobError(
            "BONECO_JOB_ACTION exige exatamente um bloco PowerShell"
        )
    code = blocks[0].strip()
    validate_action_code(code)
    return code


def validate_action_code(code: str) -> None:
    if not code.startswith("# BONECO_RUN\n"):
        raise JobError("o bloco executavel não começa com # BONECO_RUN")
    if re.search(
        r"(?mi)^\s*#\s*BONECO_CAPABILITY:\s*"
        r"(desktop|browser)\.chatgpt\.job\s*$",
        code,
    ):
        raise JobError("recursão chatgpt.job bloqueada")


def action_step_text(code: str) -> str:
    match = re.search(r"(?mi)^\s*#\s*BONECO_STEP:\s*(.+?)\s*$", code)

    if not match:
        return "Executando etapa no computador"

    text = normalized_loop_text(match.group(1))

    if len(text) > 240:
        text = text[:240].rstrip() + "..."

    return text or "Executando etapa no computador"


def clipped_result(value: str) -> str:
    if len(value) <= MAX_RESULT_CHARS:
        return value
    half = MAX_RESULT_CHARS // 2
    removed = len(value) - (half * 2)
    return (
        value[:half]
        + f"\n\n[... {removed} caracteres omitidos pelo Boneco Run ...]\n\n"
        + value[-half:]
    )


def normalized_loop_text(value: str) -> str:
    return " ".join(
        value.replace("\x00", " ").split()
    ).strip()


def track_repetition(
    repeated: dict[str, int],
    *,
    kind: str,
    value: str,
) -> None:
    normalized = normalized_loop_text(value)

    if not normalized or MAX_REPEATED_CONTENT <= 0:
        return

    digest = hashlib.sha256(
        f"{kind}\0{normalized}".encode("utf-8")
    ).hexdigest()
    repeated[digest] = repeated.get(digest, 0) + 1

    if repeated[digest] >= MAX_REPEATED_CONTENT:
        raise LoopDetected(
            f"loop detectado: {kind} repetido "
            f"{repeated[digest]} vezes"
        )


def run_process_with_controls(
    command: list[str],
    *,
    input_text: str | None = None,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryFile(mode="w+b") as output_file:
        popen_options = {}
        if os.name == "nt":
            flags = 0
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
            popen_options["creationflags"] = flags
        else:
            popen_options["start_new_session"] = True

        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if input_text is not None else None,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            env=env,
            **popen_options,
        )
        if input_text is not None and process.stdin is not None:
            process.stdin.write(input_text.encode("utf-8"))
            process.stdin.close()

        deadline = (
            time.monotonic() + timeout_seconds
            if timeout_seconds > 0
            else None
        )
        while process.poll() is None:
            if PAUSE_FILE.exists():
                stop_process(process)
                raise JobError("trabalho pausado pelo usuário")
            if deadline is not None and time.monotonic() >= deadline:
                stop_process(process)
                raise JobError("timeout da etapa")
            time.sleep(0.20)

        output_file.seek(0)
        output = decode_process_output(output_file.read())
        return subprocess.CompletedProcess(
            command,
            process.returncode,
            output,
            "",
        )


def stop_process(process: subprocess.Popen) -> None:
    if os.name == "nt":
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return

    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def desktop_exchange(prompt: str, timeout_seconds: float = 240.0) -> str:
    result = run_process_with_controls(
        [sys.executable, str(ATSPI), "exchange", "--prompt", prompt,
         "--timeout", str(timeout_seconds)],
        timeout_seconds=timeout_seconds + 15,
    )
    if result.returncode != 0:
        raise JobError(
            "falha no intercâmbio AT-SPI: "
            + (result.stdout.strip() or f"exit {result.returncode}")
        )
    return extract_exchange_response(result.stdout)


def browser_exchange(
    prompt: str,
    timeout_seconds: float = 240.0,
    expected: str | None = None,
) -> str:
    command = [
        sys.executable,
        str(BROWSER_ADAPTER),
        "exchange",
        "--text",
        prompt,
        "--timeout",
        str(timeout_seconds),
    ]

    if expected:
        command.extend(
            [
                "--expected",
                expected,
            ]
        )

    result = run_process_with_controls(
        command,
        timeout_seconds=timeout_seconds + 15,
    )
    if result.returncode != 0:
        raise JobError(
            "falha no intercâmbio Browser/CDP: "
            + adapter_error_summary(
                result.stdout,
                f"exit {result.returncode}",
            )
        )
    return extract_exchange_response(result.stdout)


def browser_latest_code(
    timeout_seconds: float = 30.0,
    expected: str | None = None,
) -> str:
    command = [
        sys.executable,
        str(BROWSER_ADAPTER),
        "copy-code",
        "--timeout",
        str(timeout_seconds),
    ]

    if expected:
        command.extend(["--expected", expected])

    result = run_process_with_controls(
        command,
        timeout_seconds=timeout_seconds + 15,
    )
    if result.returncode != 0:
        raise JobError(
            "falha extraindo bloco de código Browser/CDP: "
            + adapter_error_summary(
                result.stdout,
                f"exit {result.returncode}",
            )
        )
    code = extract_code_response(result.stdout)
    validate_action_code(code)
    return code


def exchange(
    prompt: str,
    transport: str,
    timeout_seconds: float = 240.0,
    expected: str | None = None,
) -> str:
    if transport == "desktop":
        return desktop_exchange(prompt, timeout_seconds)
    if transport == "browser":
        return browser_exchange(prompt, timeout_seconds, expected)
    raise JobError(f"transporte desconhecido: {transport}")


def execute_action(code: str) -> tuple[int, str]:
    environment = os.environ.copy()
    environment["BONECO_NESTED_JOB"] = "1"
    command = [str(RUNNER), "--test-input", "-"]

    if os.name == "nt":
        command = [
            "powershell.exe",
            "-WindowStyle",
            "Hidden",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(WINDOWS_RUNNER),
            "-TestInput",
            "__stdin__",
        ]

    result = run_process_with_controls(
        command,
        input_text=code,
        timeout_seconds=COMMAND_TIMEOUT_SECONDS,
        env=environment,
    )
    return result.returncode, clipped_result(result.stdout)


def quality_gate_command() -> list[str] | None:
    configured = os.environ.get("BONECO_JOB_QUALITY_COMMAND", "").strip()

    if configured:
        return shlex.split(configured)

    project_dir_value = os.environ.get("BONECO_JOB_PROJECT_DIR", "").strip()

    if not project_dir_value:
        return None

    project_dir = Path(project_dir_value).expanduser()

    if (
        STATIC_SITE_QUALITY.is_file()
        and (project_dir / "index.html").is_file()
        and (project_dir / "styles.css").is_file()
        and (project_dir / "script.js").is_file()
    ):
        return [
            sys.executable,
            str(STATIC_SITE_QUALITY),
            "--site-dir",
            str(project_dir),
        ]

    return None


def run_quality_gate() -> tuple[bool, str]:
    command = quality_gate_command()

    if command is None:
        return True, "quality_gate=skipped\nreason=nenhum validador configurado"

    result = run_process_with_controls(
        command,
        timeout_seconds=120,
    )
    output = result.stdout.strip()
    status = "quality_ok" if result.returncode == 0 else "quality_failed"
    details = (
        f"quality_gate={status}\n"
        f"quality_exit_code={result.returncode}\n"
        "quality_command=" + " ".join(command) + "\n"
        "----- QUALITY OUTPUT -----\n"
        + output
        + "\n----- END QUALITY OUTPUT -----"
    )
    return result.returncode == 0, clipped_result(details)


def quality_feedback_prompt(job_id: str, step: int, quality_output: str) -> str:
    return (
        f"BONECO_RUN_QUALITY_GATE DO TRABALHO {job_id}, ETAPA {step}. "
        "A resposta anterior declarou # BONECO_JOB_DONE, mas a validacao local "
        "de qualidade falhou. Analise o resultado real abaixo e responda com "
        "# BONECO_JOB_ACTION, o mesmo # BONECO_JOB_ID e exatamente um bloco "
        "PowerShell valido para corrigir. Use somente # BONECO_RETURN: output e "
        "# BONECO_MODE: auto. Se perceber o mesmo conteudo sem progresso por "
        "4 ciclos, responda com # BONECO_JOB_BLOCKED para parar o loop. "
        "Nao declare DONE ate a quality gate passar. "
        "Resultado:\n\n"
        + quality_output
    )


def result_prompt(
    job_id: str,
    step: int,
    exit_code: int,
    result: str,
) -> str:
    return (
        f"BONECO_RUN_RESULTADO DO TRABALHO {job_id}, ETAPA {step}. "
        f"exit_code do executor: {exit_code}. Analise o resultado real abaixo. "
        "Se ainda houver trabalho, responda com # BONECO_JOB_ACTION, o mesmo "
        "# BONECO_JOB_ID e exatamente um bloco PowerShell valido. Use somente "
        "# BONECO_RETURN: output e # BONECO_MODE: auto. Se concluiu e validou "
        "tudo, responda com # BONECO_JOB_DONE, o mesmo # BONECO_JOB_ID, "
        "# BONECO_SUMMARY e # BONECO_VALIDATION. Se precisar do usuário, use "
        "# BONECO_JOB_NEEDS_USER. Se receber o mesmo conteúdo sem progresso "
        "por 4 ciclos, responda com # BONECO_JOB_BLOCKED para parar o loop. "
        "Resultado:\n\n"
        + result
    )


def run_job(initial_prompt: str, transport: str) -> int:
    job_id = extract_job_id(initial_prompt)
    started = time.monotonic()
    started_ms = now_ms()
    repeated: dict[str, int] = {}

    print(f"capability={transport}.chatgpt.job")
    print(f"job_id={job_id}")
    print(f"transport={transport}")
    print("max_steps=" + (str(MAX_STEPS) if MAX_STEPS > 0 else "live"))
    print(f"max_repeated_content={MAX_REPEATED_CONTENT}")
    sys.stdout.flush()

    write_activity_status(
        "running",
        job_id=job_id,
        step="Abrindo ChatGPT do projeto e enviando objetivo",
        started_ms=started_ms,
    )
    response = exchange(initial_prompt, transport, expected=job_id)

    step = 0

    while True:
        step += 1

        if MAX_STEPS > 0 and step > MAX_STEPS:
            raise JobError(f"limite de {MAX_STEPS} etapas atingido")
        if (
            JOB_TIMEOUT_SECONDS > 0
            and time.monotonic() - started >= JOB_TIMEOUT_SECONDS
        ):
            raise JobError("timeout total do trabalho")
        if PAUSE_FILE.exists():
            raise JobError("trabalho pausado pelo usuário")

        track_repetition(
            repeated,
            kind="resposta da IA",
            value=response,
        )
        state = response_state(response)
        validate_response_job_id(response, job_id)
        print(f"job_step={step} state={state}")
        sys.stdout.flush()
        write_activity_status(
            "running",
            job_id=job_id,
            step=f"IA respondeu com estado {state}",
            started_ms=started_ms,
        )

        if state == "DONE":
            quality_ok, quality_output = run_quality_gate()
            print("----- QUALITY GATE -----")
            print(quality_output)
            print("----- END QUALITY GATE -----")
            sys.stdout.flush()

            if not quality_ok:
                if MAX_STEPS > 0 and step >= MAX_STEPS:
                    raise JobError("quality gate falhou no ultimo passo")

                write_activity_status(
                    "running",
                    job_id=job_id,
                    step="Validação local falhou. Enviando retorno para a IA corrigir",
                    started_ms=started_ms,
                )
                response = exchange(
                    quality_feedback_prompt(job_id, step, quality_output),
                    transport,
                    expected=job_id,
                )
                continue

            print("job_status=done")
            print("----- FINAL RESPONSE -----")
            print(response)
            print("----- END FINAL RESPONSE -----")
            write_activity_status(
                "completed",
                job_id=job_id,
                step=extract_final_feedback(response) or "Tarefa concluida e validada",
                started_ms=started_ms,
                finished_ms=now_ms(),
                exit_code=0,
            )
            return 0
        if state in {"NEEDS_USER", "BLOCKED", "FAILED"}:
            print(f"job_status={state.lower()}")
            print("----- FINAL RESPONSE -----")
            print(response)
            print("----- END FINAL RESPONSE -----")
            exit_code = {"NEEDS_USER": 81, "BLOCKED": 82, "FAILED": 83}[state]
            activity_state = "blocked" if state in {"NEEDS_USER", "BLOCKED"} else "failed"
            write_activity_status(
                activity_state,
                job_id=job_id,
                step=extract_final_feedback(response) or state,
                started_ms=started_ms,
                finished_ms=now_ms(),
                exit_code=exit_code,
            )
            return exit_code

        if transport == "browser":
            write_activity_status(
                "running",
                job_id=job_id,
                step="Lendo bloco executavel gerado no ChatGPT",
                started_ms=started_ms,
            )
            code = browser_latest_code(expected=job_id)
        else:
            code = extract_action_code(response)

        track_repetition(
            repeated,
            kind="ação",
            value=code,
        )

        print(f"job_activity={action_step_text(code)}")
        sys.stdout.flush()
        write_activity_status(
            "running",
            job_id=job_id,
            step=action_step_text(code),
            started_ms=started_ms,
        )
        exit_code, execution_result = execute_action(code)
        track_repetition(
            repeated,
            kind="resultado do executor",
            value=execution_result,
        )
        write_activity_status(
            "running",
            job_id=job_id,
            step="Enviando resultado da execucao para a IA analisar",
            started_ms=started_ms,
            exit_code=exit_code,
        )
        feedback = result_prompt(
            job_id,
            step,
            exit_code,
            execution_result,
        )
        response = exchange(feedback, transport, expected=job_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt")
    prompt_group.add_argument("--prompt-file")
    parser.add_argument(
        "--transport",
        choices=("desktop", "browser"),
        default="desktop",
    )
    args = parser.parse_args()
    job_id = ""
    started_ms = now_ms()

    try:
        prompt = args.prompt or ""
        if args.prompt_file:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        try:
            job_id = extract_job_id(prompt)
        except JobError:
            job_id = ""
        return run_job(prompt, args.transport)
    except OSError as exc:
        print("job_status=internal_error")
        print(f"erro=prompt-file: {exc}")
        write_activity_status(
            "failed",
            job_id=job_id,
            step=f"Falha ao ler prompt do trabalho: {exc}",
            started_ms=started_ms,
            finished_ms=now_ms(),
            exit_code=85,
        )
        return 85
    except LoopDetected as exc:
        print("job_status=loop_stopped")
        print(f"erro={exc}")
        write_activity_status(
            "blocked",
            job_id=job_id,
            step=str(exc),
            started_ms=started_ms,
            finished_ms=now_ms(),
            exit_code=86,
        )
        return 86
    except JobError as exc:
        print("job_status=error")
        print(f"erro={exc}")
        write_activity_status(
            "failed",
            job_id=job_id,
            step=str(exc),
            started_ms=started_ms,
            finished_ms=now_ms(),
            exit_code=84,
        )
        return 84
    except Exception as exc:
        print("job_status=internal_error")
        print(f"erro={type(exc).__name__}: {exc}")
        write_activity_status(
            "failed",
            job_id=job_id,
            step=f"{type(exc).__name__}: {exc}",
            started_ms=started_ms,
            finished_ms=now_ms(),
            exit_code=85,
        )
        return 85


if __name__ == "__main__":
    raise SystemExit(main())
