#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent if ROOT.name == "core" else ROOT
BROWSER = PROJECT_ROOT / "platform" / "windows" / "chatgpt_browser.py"
SEND = ROOT / "chatgpt-browser-send.py"
READ = ROOT / "chatgpt-browser-read.py"
BROWSER_READY_TIMEOUT = 25.0
CHANNEL_PORTS = {
    "project": 9227,
    "api": 9228,
}


def no_window_kwargs() -> dict:
    if os.name != "nt":
        return {}

    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def run_process(command: list[str]) -> int:
    result = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        **no_window_kwargs(),
    )

    if result.stdout:
        print(result.stdout.rstrip())

    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)

    return result.returncode


def capture_process(command: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )


def parse_key_values(output: str) -> dict[str, str]:
    values: dict[str, str] = {}

    for line in output.splitlines():
        key, separator, value = line.partition("=")

        if separator:
            values[key.strip()] = value.strip()

    return values


def browser_port(channel: str) -> int:
    return CHANNEL_PORTS.get(channel, 9227)


def browser_status_data(channel: str = "project") -> dict[str, str]:
    try:
        result = capture_process(
            [
                sys.executable,
                str(BROWSER),
                "--channel",
                channel,
                "--status",
            ],
            timeout=5.0,
        )
    except subprocess.TimeoutExpired:
        return {
            "status_error": "timeout",
        }

    values = parse_key_values(result.stdout)

    if result.returncode != 0:
        values["status_error"] = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit {result.returncode}"
        )

    return values


def wait_for_cdp(timeout: float, channel: str = "project") -> dict[str, str]:
    deadline = time.monotonic() + timeout
    last_status: dict[str, str] = {}

    while time.monotonic() < deadline:
        last_status = browser_status_data(channel)

        if last_status.get("cdp_ready") == "true":
            return last_status

        time.sleep(0.5)

    return last_status


def launch_browser(channel: str = "project") -> int:
    return run_process(
        [
            sys.executable,
            str(BROWSER),
            "--channel",
            channel,
        ]
    )


def stop_browser(channel: str = "project") -> int:
    return run_process(
        [
            sys.executable,
            str(BROWSER),
            "--channel",
            channel,
            "--stop",
        ]
    )


def ensure_browser_ready(channel: str = "project") -> int:
    status_data = browser_status_data(channel)

    if status_data.get("cdp_ready") == "true":
        return 0

    print("browser_status=starting")
    print(f"browser_channel={channel}")

    launch_result = launch_browser(channel)

    if launch_result != 0:
        print("status=browser_start_failed")
        return launch_result

    status_data = wait_for_cdp(BROWSER_READY_TIMEOUT, channel)

    if status_data.get("cdp_ready") == "true":
        print("browser_status=ready")
        return 0

    if status_data.get("running") == "true":
        print("browser_status=restarting_without_cdp")
        stop_browser(channel)

        relaunch_result = launch_browser(channel)

        if relaunch_result != 0:
            print("status=browser_restart_failed")
            return relaunch_result

        status_data = wait_for_cdp(BROWSER_READY_TIMEOUT, channel)

        if status_data.get("cdp_ready") == "true":
            print("browser_status=ready")
            return 0

    print("status=browser_cdp_unavailable")
    print(
        "erro=ChatGPT Browser não ficou pronto na porta CDP "
        + str(browser_port(channel))
    )

    if status_data:
        print("last_browser_status=" + json.dumps(
            status_data,
            ensure_ascii=False,
            sort_keys=True,
        ))

    return 84


def status(channel: str = "project") -> int:
    return run_process(
        [
            sys.executable,
            str(BROWSER),
            "--channel",
            channel,
            "--status",
        ]
    )


def send(text: str, channel: str = "project") -> int:
    ready = ensure_browser_ready(channel)

    if ready != 0:
        return ready

    return run_process(
        [
            sys.executable,
            str(SEND),
            "--text",
            text,
            "--port",
            str(browser_port(channel)),
        ]
    )


def exchange(
    text: str,
    timeout: float | None = None,
    expected: str | None = None,
    channel: str = "project",
) -> int:
    send_result = send(text, channel)

    if send_result != 0:
        return send_result

    return read_response(expected=expected, timeout=timeout, channel=channel)


def read_response(
    expected: str | None = None,
    timeout: float | None = None,
    code_only: bool = False,
    channel: str = "project",
) -> int:
    ready = ensure_browser_ready(channel)

    if ready != 0:
        return ready

    command = [
        sys.executable,
        str(READ),
        "--port",
        str(browser_port(channel)),
    ]

    if code_only:
        command.append("--code-only")

    if expected:
        command.extend([
            "--expected",
            expected,
        ])

    if timeout is not None:
        command.extend([
            "--timeout",
            str(timeout),
        ])

    return run_process(command)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Adapter ChatGPT Web do BONECO RUN"
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    status_parser = sub.add_parser("status")
    status_parser.add_argument(
        "--channel",
        choices=tuple(CHANNEL_PORTS),
        default="project",
    )

    ensure_parser = sub.add_parser("ensure-ready")
    ensure_parser.add_argument(
        "--channel",
        choices=tuple(CHANNEL_PORTS),
        default="project",
    )

    send_parser = sub.add_parser("send")
    send_parser.add_argument(
        "--text",
        required=True,
    )
    send_parser.add_argument(
        "--channel",
        choices=tuple(CHANNEL_PORTS),
        default="project",
    )

    exchange_parser = sub.add_parser("exchange")
    exchange_parser.add_argument(
        "--text",
        required=True,
    )
    exchange_parser.add_argument(
        "--timeout",
        type=float,
    )
    exchange_parser.add_argument(
        "--expected",
    )
    exchange_parser.add_argument(
        "--channel",
        choices=tuple(CHANNEL_PORTS),
        default="project",
    )

    read_parser = sub.add_parser("read")
    read_parser.add_argument(
        "--expected",
    )
    read_parser.add_argument(
        "--timeout",
        type=float,
    )
    read_parser.add_argument(
        "--channel",
        choices=tuple(CHANNEL_PORTS),
        default="project",
    )

    code_parser = sub.add_parser("copy-code")
    code_parser.add_argument(
        "--expected",
    )
    code_parser.add_argument(
        "--timeout",
        type=float,
    )
    code_parser.add_argument(
        "--channel",
        choices=tuple(CHANNEL_PORTS),
        default="project",
    )

    args = parser.parse_args()

    if args.command == "status":
        return status(args.channel)

    if args.command == "ensure-ready":
        return ensure_browser_ready(args.channel)

    if args.command == "send":
        return send(args.text, args.channel)

    if args.command == "exchange":
        return exchange(args.text, args.timeout, args.expected, args.channel)

    if args.command == "read":
        return read_response(args.expected, args.timeout, channel=args.channel)

    if args.command == "copy-code":
        return read_response(
            args.expected,
            args.timeout,
            code_only=True,
            channel=args.channel,
        )

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
