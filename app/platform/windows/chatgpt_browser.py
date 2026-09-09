#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen


SCRIPT = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT.parents[2]
def app_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return Path(base) / "BonecoRunWindows"
        return Path.home() / "AppData" / "Local" / "BonecoRunWindows"

    return PROJECT_ROOT / "runtime"


STATE_DIR = Path(
    os.environ.get(
        "BONECO_STATE_DIR",
        str(app_data_dir() / "state"),
    )
)

URL_FILE = STATE_DIR / "chatgpt_project_url"
PID_FILE = STATE_DIR / "chatgpt_project_browser_pid"
API_URL_FILE = STATE_DIR / "chatgpt_api_url"
API_PID_FILE = STATE_DIR / "chatgpt_api_browser_pid"

PROFILE_DIR = app_data_dir() / "browser-profiles" / "chatgpt-project"
API_PROFILE_DIR = app_data_dir() / "browser-profiles" / "chatgpt-api"

FALLBACK_URL = "https://chatgpt.com/"
GOOGLE_PROFILE_URL = "https://accounts.google.com/"
DEBUG_PORT = 9227
API_DEBUG_PORT = 9228
CHANNELS = ("project", "api")
LOGIN_MODES = ("chatgpt", "google-profile")


def no_window_kwargs() -> dict:
    if os.name != "nt":
        return {}

    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def candidate_paths() -> list[Path]:
    values: list[Path] = []

    for name in ("BONECO_CHROME", "CHROME", "GOOGLE_CHROME_SHIM"):
        value = os.environ.get(name, "").strip().strip('"')
        if value:
            values.append(Path(value))

    for base_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(base_name, "").strip()
        if not base:
            continue
        base_path = Path(base)
        values.extend(
            [
                base_path / "Google" / "Chrome" / "Application" / "chrome.exe",
                base_path / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            ]
        )

    for command in ("chrome.exe", "msedge.exe"):
        found = shutil.which(command)
        if found:
            values.append(Path(found))

    return values


def chrome_binary() -> Path:
    seen: set[str] = set()

    for candidate in candidate_paths():
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)

        if candidate.is_file():
            return candidate

    raise RuntimeError("Google Chrome ou Microsoft Edge nao encontrado.")


def channel_config(channel: str) -> dict[str, object]:
    if channel == "api":
        return {
            "url_file": API_URL_FILE,
            "pid_file": API_PID_FILE,
            "profile_dir": API_PROFILE_DIR,
            "debug_port": API_DEBUG_PORT,
            "window_mode_file": STATE_DIR / "chatgpt_api_window_mode",
        }

    return {
        "url_file": URL_FILE,
        "pid_file": PID_FILE,
        "profile_dir": PROFILE_DIR,
        "debug_port": DEBUG_PORT,
        "window_mode_file": STATE_DIR / "chatgpt_project_window_mode",
    }


def profile_json_files(profile_dir: Path) -> list[Path]:
    return [
        profile_dir / "Local State",
        profile_dir / "Default" / "Preferences",
    ]


def mark_profile_clean(profile_dir: Path) -> None:
    for path in profile_json_files(profile_dir):
        if not path.is_file():
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        profile = data.setdefault("profile", {})
        if isinstance(profile, dict):
            profile["exited_cleanly"] = True
            profile["exit_type"] = "Normal"

        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass


def has_saved_session(channel: str = "project") -> bool:
    config = channel_config(channel)
    profile_dir = config["profile_dir"]
    assert isinstance(profile_dir, Path)

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


def window_mode_file(channel: str = "project") -> Path:
    config = channel_config(channel)
    mode_file = config["window_mode_file"]
    assert isinstance(mode_file, Path)
    return mode_file


def saved_window_mode(channel: str = "project") -> str:
    try:
        value = window_mode_file(channel).read_text(encoding="utf-8").strip()
    except OSError:
        return ""

    return value if value in {"app", "normal"} else ""


def save_window_mode(channel: str, mode: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    window_mode_file(channel).write_text(mode + "\n", encoding="utf-8")


def valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except Exception:
        return False

    return (
        parsed.scheme == "https"
        and parsed.hostname in {"chatgpt.com", "www.chatgpt.com"}
    )


def configured_url(channel: str = "project") -> str:
    config = channel_config(channel)
    url_file = config["url_file"]

    assert isinstance(url_file, Path)

    try:
        value = url_file.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""

    if valid_url(value):
        return value

    return FALLBACK_URL


def save_url(value: str, channel: str = "project") -> str:
    value = value.strip()

    if not valid_url(value):
        raise ValueError("A URL deve usar HTTPS e pertencer a chatgpt.com.")

    config = channel_config(channel)
    url_file = config["url_file"]

    assert isinstance(url_file, Path)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = url_file.with_name(url_file.name + ".tmp")
    tmp.write_text(value + "\n", encoding="utf-8")
    tmp.replace(url_file)
    return value


def saved_pid(channel: str = "project") -> int | None:
    config = channel_config(channel)
    pid_file = config["pid_file"]

    assert isinstance(pid_file, Path)

    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None


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
                **no_window_kwargs(),
            )
            return str(pid) in result.stdout
        except Exception:
            return False

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_pid(pid: int | None) -> None:
    if not pid:
        return

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
            **no_window_kwargs(),
        )
        return

    os.kill(pid, 15)


def debug_status(channel: str = "project") -> dict:
    config = channel_config(channel)
    debug_port = int(config["debug_port"])
    url = f"http://127.0.0.1:{debug_port}/json/version"

    try:
        with urlopen(url, timeout=1.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


def browser_targets(channel: str = "project") -> list[dict]:
    config = channel_config(channel)
    debug_port = int(config["debug_port"])
    url = f"http://127.0.0.1:{debug_port}/json"

    try:
        with urlopen(url, timeout=1.0) as response:
            targets = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []

    if isinstance(targets, list):
        return [
            target
            for target in targets
            if isinstance(target, dict)
        ]

    return []


def has_chatgpt_target(channel: str = "project") -> bool:
    return any(
        "chatgpt.com" in str(target.get("url") or "")
        for target in browser_targets(channel)
    )


def launch(
    *,
    channel: str,
    url: str,
    width: int,
    height: int,
    app_mode: bool = True,
    force_restart: bool = False,
) -> int:
    chrome = chrome_binary()
    config = channel_config(channel)
    profile_dir = config["profile_dir"]
    pid_file = config["pid_file"]
    debug_port = int(config["debug_port"])

    assert isinstance(profile_dir, Path)
    assert isinstance(pid_file, Path)

    profile_dir.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    requested_mode = "app" if app_mode else "normal"
    existing = saved_pid(channel)

    if process_alive(existing):
        deadline = time.monotonic() + 6

        while time.monotonic() < deadline:
            if debug_status(channel):
                if force_restart:
                    break
                current_mode = saved_window_mode(channel)
                if current_mode and current_mode != requested_mode:
                    break
                if not current_mode and requested_mode == "app":
                    break
                if "chatgpt.com" in url and not has_chatgpt_target(channel):
                    break
                print("status=already_running")
                print(f"channel={channel}")
                print(f"pid={existing}")
                print(f"port={debug_port}")
                print(f"debug_port={debug_port}")
                print("cdp_ready=true")
                print(f"url={configured_url(channel)}")
                print(f"profile_dir={profile_dir}")
                print(f"browser={chrome}")
                print(f"window_mode={current_mode or requested_mode}")
                print("session_saved=" + ("true" if has_saved_session(channel) else "false"))
                return 0
            time.sleep(0.25)

        print("status=restarting_without_cdp")
        print(f"old_pid={existing}")
        terminate_pid(existing)
        mark_profile_clean(profile_dir)
        time.sleep(1.0)

    mark_profile_clean(profile_dir)

    command = [
        str(chrome),
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        f"--remote-debugging-port={debug_port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        f"--window-size={width},{height}",
    ]

    if app_mode and channel == "project":
        command.append("--window-position=-32000,-32000")

    if app_mode:
        command.append(f"--app={url}")
    else:
        command.extend([
            "--new-window",
            url,
        ])

    popen_kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        flags = 0
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        popen_kwargs["creationflags"] = flags
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **popen_kwargs)
    pid_file.write_text(str(process.pid) + "\n", encoding="utf-8")
    save_window_mode(channel, requested_mode)

    print("status=started")
    print(f"channel={channel}")
    print(f"pid={process.pid}")
    print(f"port={debug_port}")
    print(f"debug_port={debug_port}")
    print(f"url={url}")
    print(f"profile_dir={profile_dir}")
    print(f"browser={chrome}")
    print("window_mode=" + requested_mode)
    print("session_saved=" + ("true" if has_saved_session(channel) else "false"))
    return 0


def login(channel: str, mode: str, width: int, height: int) -> int:
    if mode == "google-profile":
        url = GOOGLE_PROFILE_URL
    elif mode == "chatgpt":
        url = configured_url(channel)
    else:
        print("status=error")
        print("erro=modo de login invalido")
        return 2

    return launch(
        channel=channel,
        url=url,
        width=max(900, width),
        height=max(700, height),
        app_mode=False,
        force_restart=True,
    )


def stop(channel: str = "project") -> int:
    pid = saved_pid(channel)

    if not process_alive(pid):
        print("status=not_running")
        print(f"channel={channel}")
        return 0

    terminate_pid(pid)
    deadline = time.monotonic() + 6

    while time.monotonic() < deadline:
        if not process_alive(pid):
            break
        time.sleep(0.2)

    config = channel_config(channel)
    profile_dir = config["profile_dir"]
    assert isinstance(profile_dir, Path)
    mark_profile_clean(profile_dir)
    print("status=stopped" if not process_alive(pid) else "status=stopping")
    print(f"channel={channel}")
    return 0


def status(channel: str = "project") -> int:
    config = channel_config(channel)
    profile_dir = config["profile_dir"]
    debug_port = int(config["debug_port"])
    pid = saved_pid(channel)
    debug = debug_status(channel)
    alive = process_alive(pid) or bool(debug)

    print(f"running={'true' if alive else 'false'}")
    print(f"channel={channel}")
    if pid:
        print(f"pid={pid}")
    print(f"port={debug_port}")
    print(f"debug_port={debug_port}")
    print(f"url={configured_url(channel)}")
    print(f"profile_dir={profile_dir}")
    print("cdp_ready=" + ("true" if bool(debug) else "false"))
    print("session_saved=" + ("true" if has_saved_session(channel) else "false"))
    print(f"window_mode={saved_window_mode(channel)}")

    browser = debug.get("Browser")
    if browser:
        print(f"browser={browser}")

    websocket = debug.get("webSocketDebuggerUrl")
    if websocket:
        print(f"websocket={websocket}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", choices=CHANNELS, default="project")
    parser.add_argument("--url")
    parser.add_argument("--set-url")
    parser.add_argument("--get-url", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--login", choices=LOGIN_MODES)
    parser.add_argument("--app-window", action="store_true")
    parser.add_argument("--normal-window", action="store_true")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if args.set_url:
            value = save_url(args.set_url, args.channel)
            print("status=saved")
            print(f"channel={args.channel}")
            print(f"url={value}")
            return 0

        if args.get_url:
            print(configured_url(args.channel))
            return 0

        if args.stop:
            return stop(args.channel)

        if args.status:
            return status(args.channel)

        if args.check:
            print("status=ok")
            print(f"channel={args.channel}")
            print(f"chrome={chrome_binary()}")
            print(f"url={configured_url(args.channel)}")
            print(f"profile_dir={channel_config(args.channel)['profile_dir']}")
            print("persistent_session=true")
            print(f"debug_port={channel_config(args.channel)['debug_port']}")
            print("engine=Chrome_or_Edge")
            return 0

        if args.login:
            return login(
                args.channel,
                args.login,
                width=max(900, args.width),
                height=max(700, args.height),
            )

        url = args.url.strip() if args.url else configured_url(args.channel)

        if not valid_url(url):
            print("status=error")
            print("erro=URL invalida")
            return 2

        return launch(
            channel=args.channel,
            url=url,
            width=max(1100, args.width),
            height=max(620, args.height),
            app_mode=args.app_window or (
                not args.normal_window
                and has_saved_session(args.channel)
            ),
        )
    except Exception as exc:
        print("status=error")
        print(f"erro={type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
