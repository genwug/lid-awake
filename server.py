#!/usr/bin/env python3
"""Keep a Mac awake with the lid closed, then turn sleep back on.

This runs on macOS itself. A Docker container cannot call pmset on the Mac.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOST = "127.0.0.1"
PORT = int(os.environ.get("LID_AWAKE_PORT", "8737"))
LABEL = "com.gidon.lid-awake"
PMSET = "/usr/bin/pmset"
MIN_MINUTES = 1
MAX_MINUTES = 24 * 60
ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
STATE_DIR = Path(
    os.environ.get(
        "LID_AWAKE_STATE_DIR",
        Path.home() / "Library" / "Application Support" / "LidAwake",
    )
)
STATE_PATH = STATE_DIR / "state.json"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "lid-awake.log"
SUDOERS_PATH = "/etc/sudoers.d/lid-awake"

lock = threading.Lock()
stop = threading.Event()
httpd: ThreadingHTTPServer | None = None


class AdminRequired(Exception):
    pass


class PowerError(Exception):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def username() -> str:
    import pwd

    name = pwd.getpwuid(os.getuid()).pw_name
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise PowerError(f"unusual username: {name}")
    return name


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)


def default_state() -> dict:
    return {
        "minutes": 60,
        "until_epoch": None,
        "token": secrets.token_urlsafe(32),
        "admin_ok": False,
    }


def load_state() -> dict:
    ensure_state_dir()
    if not STATE_PATH.exists():
        state = default_state()
        save_state(state)
        return state
    try:
        data = json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    state = default_state()
    minutes = data.get("minutes")
    if isinstance(minutes, int) and not isinstance(minutes, bool):
        if MIN_MINUTES <= minutes <= MAX_MINUTES:
            state["minutes"] = minutes
    until = data.get("until_epoch")
    if isinstance(until, (int, float)) and not isinstance(until, bool):
        state["until_epoch"] = float(until)
    token = data.get("token")
    if isinstance(token, str) and token:
        state["token"] = token
    state["admin_ok"] = data.get("admin_ok") is True
    if token != state["token"]:
        save_state(state)
    return state


def save_state(state: dict) -> None:
    ensure_state_dir()
    temporary = STATE_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(STATE_PATH)


def update_state(**changes) -> dict:
    state = load_state()
    state.update(changes)
    save_state(state)
    return state


def parse_sleep_disabled(ioreg_text: str) -> bool | None:
    match = re.search(r'"SleepDisabled"\s*=\s*(Yes|No|true|false)', ioreg_text)
    if not match:
        return None
    return match.group(1) in ("Yes", "true")


def sleep_disabled() -> bool:
    result = subprocess.run(
        ["ioreg", "-rc", "IOPMrootDomain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or "ioreg did not answer").strip()
        raise PowerError(detail)
    parsed = parse_sleep_disabled(result.stdout)
    if parsed is None:
        raise PowerError("IOPMrootDomain has no SleepDisabled")
    return parsed


def display_sleep_minutes() -> int | None:
    result = subprocess.run(
        ["pmset", "-g"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    in_use = False
    for raw in result.stdout.splitlines():
        if raw.startswith("Currently in use"):
            in_use = True
            continue
        if not in_use:
            continue
        if raw and not raw.startswith((" ", "\t")):
            break
        parts = raw.split()
        if len(parts) >= 2 and parts[0] == "displaysleep":
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def sudoers_ready() -> bool:
    result = subprocess.run(
        ["sudo", "-n", "-l"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    text = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and "NOPASSWD" in text and "disablesleep" in text


def set_sleep_disabled(disabled: bool) -> None:
    value = "1" if disabled else "0"
    result = subprocess.run(
        ["sudo", "-n", PMSET, "-a", "disablesleep", value],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        lowered = err.lower()
        if "password" in lowered or "operation not permitted" in lowered:
            raise AdminRequired(err or "administrator password required")
        raise PowerError(err or f"pmset exited with code {result.returncode}")
    deadline = time.time() + 3
    while time.time() < deadline:
        if sleep_disabled() == disabled:
            return
        time.sleep(0.1)
    raise PowerError("pmset finished, but SleepDisabled did not change")


def validate_minutes(value: object) -> int:
    if isinstance(value, bool):
        raise PowerError("enter the time as a whole number of minutes")
    if isinstance(value, float):
        if not value.is_integer():
            raise PowerError("enter the time as a whole number of minutes")
        value = int(value)
    if not isinstance(value, int):
        raise PowerError("enter the time as a whole number of minutes")
    if value < MIN_MINUTES or value > MAX_MINUTES:
        raise PowerError("the timer can be from 1 minute to 24 hours")
    return value


def stay_awake(minutes: object) -> dict:
    minutes = validate_minutes(minutes)
    with lock:
        until = time.time() + minutes * 60
        update_state(minutes=minutes, until_epoch=until)
        try:
            set_sleep_disabled(True)
        except AdminRequired:
            update_state(until_epoch=None, admin_ok=False)
            raise
        except PowerError:
            still_disabled = True
            try:
                still_disabled = sleep_disabled()
            except PowerError:
                still_disabled = True
            if not still_disabled:
                update_state(until_epoch=None)
            raise
        return update_state(admin_ok=True)


def allow_sleep() -> dict:
    with lock:
        try:
            if sleep_disabled():
                set_sleep_disabled(False)
                return update_state(until_epoch=None, admin_ok=True)
            return update_state(until_epoch=None)
        except AdminRequired:
            update_state(admin_ok=False)
            raise


_last_notice = {"key": "", "at": 0.0}


def notice(key: str, message: str, interval: float = 30) -> None:
    now = time.time()
    if _last_notice["key"] == key and now - _last_notice["at"] < interval:
        return
    _last_notice["key"] = key
    _last_notice["at"] = now
    log(message)


def reconcile() -> None:
    with lock:
        state = load_state()
        until = state.get("until_epoch")
        if until is None:
            return
        try:
            disabled = sleep_disabled()
        except PowerError as exc:
            notice("read", f"cannot read the sleep state: {exc}")
            return
        now = time.time()
        try:
            if now >= until:
                if disabled:
                    set_sleep_disabled(False)
                    log("timer ended, sleep is back on")
                update_state(until_epoch=None, admin_ok=True)
            elif not disabled:
                set_sleep_disabled(True)
                update_state(admin_ok=True)
                log("sleep is off again until the timer ends")
        except AdminRequired as exc:
            update_state(admin_ok=False)
            notice("admin", f"no permission to restore the setting: {exc}")
        except PowerError as exc:
            notice("power", f"could not change sleep: {exc}")


def watchdog() -> None:
    while not stop.wait(1):
        try:
            reconcile()
        except Exception as exc:
            log(f"timer: {exc}")


def sudoers_script_text(user: str | None = None) -> str:
    script = """#!/bin/bash
set -euo pipefail
umask 077
tmp=$(mktemp)
cat > "$tmp" <<'EOF'
__USER__ ALL=(root) NOPASSWD: /usr/bin/pmset -a disablesleep 0, /usr/bin/pmset -a disablesleep 1
EOF
/usr/sbin/visudo -c -f "$tmp"
/bin/mkdir -p /etc/sudoers.d
/bin/cp "$tmp" __SUDOERS__
/bin/chmod 440 __SUDOERS__
/usr/sbin/chown root:wheel __SUDOERS__
/bin/rm -f "$tmp"
"""
    return script.replace("__USER__", user or username()).replace("__SUDOERS__", SUDOERS_PATH)


def install_sudoers() -> None:
    script = sudoers_script_text()
    ensure_state_dir()
    path = STATE_DIR / "install-sudoers.sh"
    path.write_text(script)
    os.chmod(path, 0o700)
    source = """on run argv
  do shell script "/bin/bash " & quoted form of (item 1 of argv) with administrator privileges
end run
"""
    result = subprocess.run(
        ["osascript", "-", str(path)],
        input=source,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        if "User canceled" in err or "-128" in err:
            raise PowerError("password was not entered")
        raise PowerError(err or "could not write the permission")
    if not sudoers_ready():
        raise PowerError("password was accepted, but sudo does not see the pmset permission")
    update_state(admin_ok=True)


def gui_domain() -> str:
    return f"gui/{os.getuid()}"


def service_loaded() -> bool:
    result = subprocess.run(
        ["launchctl", "print", f"{gui_domain()}/{LABEL}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def write_plist() -> None:
    def xml(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{xml(sys.executable)}</string>
    <string>{xml(str(ROOT / "server.py"))}</string>
    <string>--serve</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>2</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>LimitLoadToSessionType</key>
  <string>Aqua</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>LID_AWAKE_LAUNCHD</key>
    <string>1</string>
  </dict>
  <key>StandardOutPath</key>
  <string>{xml(str(LOG_PATH))}</string>
  <key>StandardErrorPath</key>
  <string>{xml(str(LOG_PATH))}</string>
</dict>
</plist>
"""
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist)


def status_payload() -> dict:
    state = load_state()
    error = None
    disabled: bool | None
    try:
        disabled = sleep_disabled()
    except PowerError as exc:
        disabled = None
        error = str(exc)
    until = state.get("until_epoch")
    remaining = None
    if isinstance(until, float):
        remaining = max(0, int(until - time.time()))
    return {
        "ok": True,
        "sleep_disabled": disabled,
        "until_epoch": until,
        "remaining_seconds": remaining,
        "minutes": state["minutes"],
        "admin_ok": state["admin_ok"] is True,
        "autostart": service_loaded(),
        "in_app": os.environ.get("LID_AWAKE_APP") == "1",
        "display_sleep_minutes": display_sleep_minutes(),
        "error": error,
        "now": time.time(),
    }


def json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        log(f"{self.address_string()} {fmt % args}")

    def host_ok(self) -> bool:
        return self.headers.get("Host", "") in {f"{HOST}:{PORT}", f"localhost:{PORT}"}

    def do_GET(self) -> None:
        if not self.host_ok():
            self.send_json({"ok": False, "error": "wrong address"}, 403)
            return
        path = urlparse(self.path).path
        if path == "/":
            self.serve_index()
            return
        if path == "/api/health":
            self.send_json({"ok": True})
            return
        if path == "/api/status":
            if not self.authorized():
                return
            self.send_json(status_payload())
            return
        if path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_json({"ok": False, "error": "no such page"}, 404)

    def do_POST(self) -> None:
        if not self.host_ok():
            self.send_json({"ok": False, "error": "wrong address"}, 403)
            return
        if not self.authorized():
            return
        if not self.origin_ok() or not self.json_content():
            self.send_json({"ok": False, "error": "request rejected"}, 403)
            return
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/stay-awake":
                stay_awake(body.get("minutes"))
            elif path == "/api/allow-sleep":
                allow_sleep()
            elif path == "/api/install-helper":
                install_sudoers()
            else:
                self.send_json({"ok": False, "error": "no such action"}, 404)
                return
            self.send_json(status_payload())
        except AdminRequired as exc:
            self.send_json({"ok": False, "need_admin": True, "error": str(exc)}, 403)
        except PowerError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "could not read JSON"}, 400)

    def authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {load_state()['token']}"
        if len(header) != len(expected) or not secrets.compare_digest(header, expected):
            self.send_json({"ok": False, "error": "no access"}, 401)
            return False
        host = self.headers.get("Host", "")
        if host not in {f"{HOST}:{PORT}", f"localhost:{PORT}"}:
            self.send_json({"ok": False, "error": "wrong address"}, 403)
            return False
        return True

    def origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        allowed = {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}
        return origin in allowed

    def json_content(self) -> bool:
        content_type = self.headers.get("Content-Type", "")
        return content_type.split(";", 1)[0].strip().lower() == "application/json"

    def read_json(self) -> dict:
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise PowerError("bad request length") from exc
        if length < 0 or length > 8192:
            raise PowerError("request is too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise PowerError("expected an object")
        return data

    def serve_index(self) -> None:
        token = load_state()["token"]
        boot = json.dumps({"token": token}, ensure_ascii=True)
        html = INDEX.read_text(encoding="utf-8").replace("{{BOOT}}", boot)
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict, code: int = 200) -> None:
        body = json_bytes(payload)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def stop_server(restore: bool) -> None:
    if restore:
        try:
            if load_state().get("until_epoch"):
                log("stopping, turning sleep back on")
                allow_sleep()
        except Exception as exc:
            log(str(exc))
    stop.set()
    if httpd is not None:
        httpd.shutdown()


def request_stop(signum, _frame) -> None:
    restore = os.environ.get("LID_AWAKE_LAUNCHD") != "1"
    threading.Thread(target=stop_server, args=(restore,), daemon=True).start()


def serve() -> None:
    global httpd
    if sys.platform != "darwin":
        sys.exit(
            "Lid Awake runs only on macOS. "
            "A Docker container cannot switch Mac sleep: it cannot see pmset."
        )
    reconcile()
    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        sys.exit(
            f"port {PORT} is in use ({exc}). "
            f"If the app is already running, open http://{HOST}:{PORT}"
        )
    httpd.daemon_threads = True
    log(f"Lid Awake is listening on http://{HOST}:{PORT}")
    try:
        httpd.serve_forever()
    finally:
        stop.set()
        httpd.server_close()


def wait_until_up(timeout: float = 8) -> bool:
    import urllib.request

    deadline = time.time() + timeout
    url = f"http://{HOST}:{PORT}/api/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def ensure_agent() -> None:
    if sys.platform != "darwin":
        sys.exit(
            "Lid Awake runs only on macOS. "
            "A Docker container cannot switch Mac sleep: it cannot see pmset."
        )
    write_plist()
    domain = gui_domain()
    if service_loaded() and wait_until_up(timeout=2):
        return
    if service_loaded():
        subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=False)
    else:
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(PLIST_PATH)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and not service_loaded():
            detail = (result.stderr or result.stdout or "launchctl bootstrap failed").strip()
            sys.exit(detail)
    if not wait_until_up():
        sys.exit(f"the server did not open port {PORT}. Log: {LOG_PATH}")


def ensure_service_and_open() -> None:
    if sys.platform != "darwin":
        sys.exit(
            "Lid Awake runs only on macOS. "
            "A Docker container cannot switch Mac sleep: it cannot see pmset."
        )
    ensure_agent()
    subprocess.run(["open", f"http://{HOST}:{PORT}"], check=False)


def run_self_test() -> None:
    global STATE_DIR, STATE_PATH, PLIST_PATH, LOG_PATH
    STATE_DIR = Path(tempfile.mkdtemp(prefix="lid-awake-"))
    STATE_PATH = STATE_DIR / "state.json"
    PLIST_PATH = STATE_DIR / "job.plist"
    LOG_PATH = STATE_DIR / "lid-awake.log"

    assert parse_sleep_disabled('"SleepDisabled" = Yes') is True
    assert parse_sleep_disabled('"SleepDisabled" = No') is False
    assert parse_sleep_disabled("SleepDisabled = Yes") is None

    assert validate_minutes(1) == 1
    assert validate_minutes(60.0) == 60
    assert validate_minutes(MAX_MINUTES) == MAX_MINUTES
    for bad in (0, -1, MAX_MINUTES + 1, True, False, "60", 1.5, None):
        try:
            validate_minutes(bad)
        except PowerError:
            continue
        raise SystemExit(f"should have rejected {bad!r}")

    state = load_state()
    assert state["minutes"] == 60
    assert state["until_epoch"] is None
    assert state["admin_ok"] is False
    update_state(minutes=15, until_epoch=123.0, admin_ok=True)
    again = load_state()
    assert again["minutes"] == 15
    assert again["until_epoch"] == 123.0
    assert again["token"] == state["token"]
    assert again["admin_ok"] is True
    assert "{{BOOT}}" in INDEX.read_text(encoding="utf-8")
    script = sudoers_script_text("gidon")
    assert "gidon ALL=(root)" in script
    check = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
    if check.returncode != 0:
        raise SystemExit(check.stderr)
    write_plist()
    lint = subprocess.run(["plutil", "-lint", str(PLIST_PATH)], capture_output=True, text=True)
    if lint.returncode != 0:
        raise SystemExit(lint.stderr or lint.stdout)
    print("ok")


def main() -> None:
    parser = argparse.ArgumentParser(description="Mac sleep with the lid closed")
    parser.add_argument("--serve", action="store_true", help="server only, do not open a window")
    parser.add_argument("--ensure-agent", action="store_true", help="keep the timer running in the background")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        return
    if args.ensure_agent:
        ensure_agent()
        return
    if args.serve:
        serve()
        return
    ensure_service_and_open()


if __name__ == "__main__":
    main()
