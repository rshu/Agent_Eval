"""OpenCode HTTP client and session interaction helpers."""

import os
import threading
import time

import requests
from requests.auth import HTTPBasicAuth
from typing import Any, Optional

BASE_URL = os.getenv("OPENCODE_BASE_URL", "http://127.0.0.1:4096").rstrip("/")
USERNAME = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")
PASSWORD = os.getenv("OPENCODE_SERVER_PASSWORD")


class AgentDidNotRunError(RuntimeError):
    """Raised when a request is accepted but no assistant reply is produced."""


def _fmt_elapsed(seconds: float) -> str:
    """Format elapsed seconds as '15s' or '2m 30s'."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


class _ProgressTimer:
    """Background thread that prints elapsed time at regular intervals."""

    def __init__(self, interval: int = 15):
        self._interval = interval
        self._t0 = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(self._interval):
            print(f"[..] Waiting for agent... ({_fmt_elapsed(time.time() - self._t0)})")

    def __enter__(self):
        self._t0 = time.time()
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)

    @property
    def elapsed(self) -> float:
        return time.time() - self._t0


# ── HTTP helper ─────────────────────────────────────────────────────────

def opencode_request(method: str, path: str, json_body: Any = None,
                     params: Optional[dict] = None, timeout: int = 300) -> Any:
    url = f"{BASE_URL}{path}"
    auth = HTTPBasicAuth(USERNAME, PASSWORD) if PASSWORD else None
    r = requests.request(method, url, json=json_body, params=params,
                         auth=auth, timeout=timeout)
    r.raise_for_status()
    if not r.content:
        return None
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        return r.text


# ── Message helpers ─────────────────────────────────────────────────────

def is_assistant_message(msg):
    if not isinstance(msg, dict):
        return False
    role = msg.get("role")
    if role == "assistant":
        return True
    info = msg.get("info")
    return isinstance(info, dict) and info.get("role") == "assistant"


def normalize_message(msg):
    if not isinstance(msg, dict):
        return {"info": {}, "parts": []}
    if "info" in msg and "parts" in msg:
        parts = msg["parts"]
        if not isinstance(parts, list):
            return {**msg, "parts": []}
        return msg
    info = {"role": msg.get("role")} if msg.get("role") else {}
    parts = msg.get("parts", [])
    if not isinstance(parts, list):
        parts = []
    return {"info": info, "parts": parts}


def assistant_error_message(msg):
    if not isinstance(msg, dict):
        return None
    info = msg.get("info", {})
    if not isinstance(info, dict):
        return None
    err = info.get("error")
    if not isinstance(err, dict):
        return None
    data = err.get("data")
    if not isinstance(data, dict):
        return None
    return data.get("message")


def wait_for_assistant_message(session_id: str, directory: str = None,
                               timeout_sec: int = 120, poll_sec: float = 1.5):
    params = {"directory": directory} if directory else None
    deadline = time.time() + timeout_sec
    poll_count = 0
    while time.time() < deadline:
        messages = opencode_request("GET", f"/session/{session_id}/message",
                                    params=params, timeout=20) or []
        if isinstance(messages, dict):
            messages = [messages]
        poll_count += 1
        if isinstance(messages, list):
            if poll_count <= 3 or poll_count % 20 == 0:
                roles = [m.get("role")
                         or (m["info"] if isinstance(m.get("info"), dict) else {}).get("role", "?")
                         for m in messages if isinstance(m, dict)]
                print(f"  [poll {poll_count}] {len(messages)} message(s), roles={roles}")
                if poll_count == 1 and messages:
                    sample = messages[-1] if isinstance(messages[-1], dict) else {}
                    print(f"  [poll {poll_count}] last message keys: {list(sample.keys())}")
            for m in reversed(messages):
                if is_assistant_message(m):
                    return normalize_message(m)
        time.sleep(poll_sec)

    messages = opencode_request("GET", f"/session/{session_id}/message",
                                params=params, timeout=20) or []
    if isinstance(messages, dict):
        messages = [messages]
    if isinstance(messages, list) and messages:
        last = messages[-1] if isinstance(messages[-1], dict) else {}
        info = last.get("info", last) if isinstance(last, dict) else {}
        role = info.get("role") if isinstance(info, dict) else None
        error = info.get("error") if isinstance(info, dict) else None
        raise TimeoutError(
            f"No assistant message received within {timeout_sec}s. "
            f"Last message role={role!r}, error={error!r}"
        )
    raise TimeoutError(f"No assistant message received within {timeout_sec}s (no messages found).")


# ── Session lifecycle ───────────────────────────────────────────────────

def check_health() -> dict:
    health = opencode_request("GET", "/global/health")
    if not isinstance(health, dict):
        raise RuntimeError(
            f"Unexpected health response (expected dict, got {type(health).__name__}): {health!r}"
        )
    print(f"[ok] Server up — version: {health.get('version', '?')}")
    return health


def create_session(directory: str) -> str:
    session = opencode_request("POST", "/session",
                               json_body={"title": "patch-gen"},
                               params={"directory": directory})
    if not isinstance(session, dict) or "id" not in session:
        raise RuntimeError(
            f"Unexpected session response (expected dict with 'id'): {session!r}"
        )
    sid = session["id"]
    print(f"[ok] Session: {sid}")
    return sid


def send_task(session_id: str, prompt: str, directory: str,
              agent: str = "build", model: Optional[dict] = None) -> Any:
    body: dict[str, Any] = {
        "agent": agent,
        "parts": [{"type": "text", "text": prompt}],
    }
    if model:
        body["model"] = model
    model_desc = f"{model['providerID']}:{model['modelID']}" if model else "server default"
    print(f"[..] Sending task to '{agent}' (model: {model_desc}) — waiting for response...")

    with _ProgressTimer() as timer:
        msg = opencode_request("POST", f"/session/{session_id}/message",
                               json_body=body,
                               params={"directory": directory},
                               timeout=600)

        # If POST returned no body, poll for the assistant reply
        if msg is None or (isinstance(msg, str) and not msg.strip()):
            print("[..] Message POST returned no body; polling for assistant reply...")
            try:
                result = wait_for_assistant_message(session_id, directory=directory)
                print(f"[ok] Agent finished ({_fmt_elapsed(timer.elapsed)})")
                return result
            except TimeoutError:
                raise AgentDidNotRunError(
                    "Agent did NOT run — no assistant messages received after polling.\n"
                    "       Check that --model matches a valid providerID:modelID."
                )

        # If POST returned a list, find the last assistant message
        if isinstance(msg, list):
            assistant_msgs = [m for m in msg if is_assistant_message(m)]
            if assistant_msgs:
                print(f"[ok] Agent finished ({_fmt_elapsed(timer.elapsed)})")
                return normalize_message(assistant_msgs[-1])
            try:
                result = wait_for_assistant_message(session_id, directory=directory)
                print(f"[ok] Agent finished ({_fmt_elapsed(timer.elapsed)})")
                return result
            except TimeoutError:
                raise AgentDidNotRunError(
                    "Agent did NOT run — no assistant messages in session.\n"
                    "       Check that --model matches a valid providerID:modelID."
                )

        # Single dict message
        if isinstance(msg, dict):
            if is_assistant_message(msg):
                print(f"[ok] Agent finished ({_fmt_elapsed(timer.elapsed)})")
                return normalize_message(msg)

        # Unexpected or non-assistant response — try polling
        print("[..] Unexpected response shape; polling for assistant reply...")
        try:
            result = wait_for_assistant_message(session_id, directory=directory)
            print(f"[ok] Agent finished ({_fmt_elapsed(timer.elapsed)})")
            return result
        except TimeoutError:
            raise AgentDidNotRunError(
                "Agent did NOT run — no assistant reply received.\n"
                "       Check that --model matches a valid providerID:modelID."
            )


def print_response(msg: Any) -> None:
    if msg is None:
        print("[warn] No response from agent.")
        return
    parts = msg.get("parts", [])
    if not isinstance(parts, list):
        parts = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            print(f"  {p['text']}")
    tool_parts = [p for p in parts if isinstance(p, dict) and p.get("type") == "tool"]
    if tool_parts:
        print(f"[ok] {len(tool_parts)} tool call(s) made")


def cleanup_session(session_id: str, directory: str) -> None:
    try:
        opencode_request("DELETE", f"/session/{session_id}",
                         params={"directory": directory})
        print("[ok] Session cleaned up.")
    except (requests.HTTPError, requests.ConnectionError, requests.Timeout):
        pass
