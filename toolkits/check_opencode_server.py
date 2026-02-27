import os
import json
import time
import argparse
import re
import requests
from pathlib import Path
from requests.auth import HTTPBasicAuth

BASE_URL = os.getenv("OPENCODE_BASE_URL", "http://127.0.0.1:4096")
OPENCODE_CONFIG_PATH = Path(
    os.getenv("OPENCODE_CONFIG_PATH", "~/.config/opencode/config.json")
).expanduser()

# Optional Basic Auth (only if you set OPENCODE_SERVER_PASSWORD)
USERNAME = os.getenv("OPENCODE_SERVER_USERNAME", "opencode")
PASSWORD = os.getenv("OPENCODE_SERVER_PASSWORD")  # set this if server requires it

DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true", "yes")

def opencode_request(method: str, path: str, json=None, params=None, timeout: int = 300):
    url = f"{BASE_URL}{path}"
    auth = HTTPBasicAuth(USERNAME, PASSWORD) if PASSWORD else None
    t0 = time.time()
    r = requests.request(method, url, json=json, params=params, auth=auth, timeout=timeout)
    elapsed = time.time() - t0
    if DEBUG:
        print(f"  [debug] {method} {path} -> {r.status_code} ({elapsed:.1f}s, {len(r.content)} bytes)")
        if r.headers.get("content-type"):
            print(f"  [debug]   Content-Type: {r.headers['content-type']}")
    r.raise_for_status()
    if not r.content:
        return None
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        return r.text

def parse_model_spec(spec: str):
    spec = (spec or "").strip()
    if not spec:
        return None

    separator = "/" if "/" in spec else ":"
    if separator not in spec:
        return None

    provider_id, model_id = spec.split(separator, 1)
    provider_id = provider_id.strip()
    model_id = model_id.strip()
    if not provider_id or not model_id:
        return None
    return {"providerID": provider_id, "modelID": model_id}

def fetch_server_model_catalog(directory: str | None = None):
    params = {"directory": directory} if directory else None
    raw = opencode_request("GET", "/config/providers", params=params)
    catalog = {}
    default_model = None

    def add_provider(provider_id, provider_data):
        if not provider_id or not isinstance(provider_data, dict):
            return
        entry = catalog.setdefault(str(provider_id), {
            "name": provider_data.get("name") or str(provider_id),
            "models": {},
            "aliases": {},
        })

        models = provider_data.get("models")
        if isinstance(models, dict):
            for model_key, model_data in models.items():
                canonical_id = str(model_key)
                model_name = canonical_id
                aliases = {canonical_id}
                if isinstance(model_data, dict):
                    explicit_id = model_data.get("id")
                    if explicit_id:
                        canonical_id = str(explicit_id)
                    model_name = model_data.get("name") or canonical_id
                    aliases.add(str(model_key))
                    aliases.add(canonical_id)
                    aliases.add(str(model_name))
                entry["models"][canonical_id] = str(model_name)
                for alias in aliases:
                    entry["aliases"][alias] = canonical_id

        elif isinstance(models, list):
            for model_data in models:
                if not isinstance(model_data, dict):
                    continue
                model_id = model_data.get("id", model_data.get("modelID"))
                if not model_id:
                    continue
                canonical_id = str(model_id)
                model_name = model_data.get("name") or canonical_id
                entry["models"][canonical_id] = str(model_name)
                entry["aliases"][canonical_id] = canonical_id
                entry["aliases"][str(model_name)] = canonical_id

    # Shape: {"providers": [...], "default": {"provider": "model"}}
    if isinstance(raw, dict) and isinstance(raw.get("providers"), list):
        for provider in raw["providers"]:
            if not isinstance(provider, dict):
                continue
            provider_id = provider.get("id", provider.get("providerID"))
            add_provider(provider_id, provider)
        default = raw.get("default")
        if isinstance(default, dict):
            for provider_id, model_id in default.items():
                if provider_id and model_id:
                    default_model = {"providerID": str(provider_id), "modelID": str(model_id)}
                    break
        return catalog, default_model

    # Shape: {"providerID": {name, models, ...}, ...}
    if isinstance(raw, dict):
        for provider_id, provider_data in raw.items():
            if not isinstance(provider_data, dict):
                continue
            if "models" not in provider_data and "name" not in provider_data:
                continue
            add_provider(provider_id, provider_data)
        return catalog, default_model

    # Shape: [{id/providerID, name, models}, ...]
    if isinstance(raw, list):
        for provider in raw:
            if not isinstance(provider, dict):
                continue
            provider_id = provider.get("id", provider.get("providerID"))
            add_provider(provider_id, provider)
    return catalog, default_model

def choose_server_model(requested_model, preferred_name: str | None = None, directory: str | None = None):
    try:
        catalog, default_model = fetch_server_model_catalog(directory=directory)
    except Exception:
        # If provider discovery fails, keep requested model behavior.
        return requested_model, None, preferred_name, None

    def resolve_in_catalog(model_spec, preferred=None):
        if not model_spec:
            return None
        provider_id = model_spec.get("providerID")
        model_id = model_spec.get("modelID")
        if not provider_id or not model_id:
            return None
        entry = catalog.get(provider_id)
        if not entry:
            return None
        canonical_id = None
        if model_id in entry["models"]:
            canonical_id = model_id
        elif model_id in entry["aliases"]:
            canonical_id = entry["aliases"][model_id]
        elif preferred and preferred in entry["aliases"]:
            canonical_id = entry["aliases"][preferred]
        if not canonical_id:
            return None
        return {
            "providerID": provider_id,
            "modelID": canonical_id,
        }, entry["name"], entry["models"].get(canonical_id, canonical_id)

    if requested_model:
        resolved = resolve_in_catalog(requested_model, preferred=preferred_name)
        if resolved:
            return (*resolved, None)

        requested_desc = f"{requested_model.get('providerID', '?')}:{requested_model.get('modelID', '?')}"
        warning = (
            f"Configured model {requested_desc} is not available on this running server; "
            "using server default model instead."
        )
        if default_model:
            resolved_default = resolve_in_catalog(default_model)
            if resolved_default:
                # Return None payload to explicitly let server choose default.
                _, provider_label, model_label = resolved_default
                return None, provider_label, model_label, warning
        return None, None, None, warning

    if default_model:
        resolved_default = resolve_in_catalog(default_model)
        if resolved_default:
            _, provider_label, model_label = resolved_default
            return None, provider_label, model_label, None
    return None, None, None, None

def _slugify_model_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")

def find_alternative_model_by_name(model_name: str,
                                   exclude_model: dict | None = None,
                                   directory: str | None = None):
    if not model_name:
        return None
    try:
        catalog, _ = fetch_server_model_catalog(directory=directory)
    except Exception:
        return None

    target = model_name.lower()
    exclude_provider = (exclude_model or {}).get("providerID")
    exclude_model_id = (exclude_model or {}).get("modelID")

    candidates = []
    for provider_id, pdata in catalog.items():
        models = pdata.get("models", {})
        for model_id, display_name in models.items():
            if provider_id == exclude_provider and model_id == exclude_model_id:
                continue
            if str(display_name).lower() != target:
                continue
            # Prefer canonical IDs that look like a slug of the model label (e.g. glm-5 for GLM-5).
            score = 0
            if str(model_id).lower() != _slugify_model_name(str(display_name)):
                score += 10
            # Prefer built-in ZAI provider IDs when available.
            if provider_id not in {"zai-coding-plan", "zhipuai-coding-plan", "zhipuai", "zai"}:
                score += 5
            candidates.append((score, provider_id, model_id, pdata.get("name"), display_name))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    _, provider_id, model_id, provider_name, display_name = candidates[0]
    return {
        "providerID": provider_id,
        "modelID": model_id,
        "providerLabel": provider_name or provider_id,
        "modelLabel": display_name or model_id,
    }

def resolve_model(agent: str = "build"):
    selected_name = None

    env_model = os.getenv("OPENCODE_MODEL")
    if env_model:
        parsed = parse_model_spec(env_model)
        if parsed:
            return parsed, None
        print(f"[warn] Ignoring invalid OPENCODE_MODEL={env_model!r} (expected provider/model or provider:model).")

    if not OPENCODE_CONFIG_PATH.exists():
        return None, None

    try:
        with OPENCODE_CONFIG_PATH.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[warn] Could not read {OPENCODE_CONFIG_PATH}: {e}")
        return None, None

    candidates = [
        ((cfg.get("agent") or {}).get(agent) or {}).get("model"),
        ((cfg.get("mode") or {}).get(agent) or {}).get("model"),  # deprecated config field
        cfg.get("model"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            parsed = parse_model_spec(candidate)
            if parsed:
                return parsed, None

    # Fallback: if no default model string is configured, use the first custom provider/model.
    providers = cfg.get("provider")
    if isinstance(providers, dict):
        for provider_id, provider_data in providers.items():
            if not isinstance(provider_data, dict):
                continue
            models = provider_data.get("models")
            if isinstance(models, dict):
                for model_key, model_data in models.items():
                    if provider_id and model_key:
                        if isinstance(model_data, dict):
                            selected_name = model_data.get("name")
                            # Prefer explicit upstream model id when provided.
                            selected_model_id = model_data.get("id", model_key)
                        else:
                            selected_model_id = model_key
                        return {
                            "providerID": str(provider_id),
                            "modelID": str(selected_model_id),
                        }, selected_name
    return None, None

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

def send_message_and_wait(session_id: str, body: dict, directory: str):
    msg = opencode_request(
        "POST",
        f"/session/{session_id}/message",
        json=body,
        params={"directory": directory},
        timeout=600,
    )
    if msg is None or (isinstance(msg, str) and not msg.strip()):
        print("Message POST returned no body; waiting for assistant reply via session messages...")
        return wait_for_assistant_message(session_id, directory=directory)
    if isinstance(msg, list):
        assistant_msgs = [m for m in msg if is_assistant_message(m)]
        if not assistant_msgs:
            return wait_for_assistant_message(session_id, directory=directory)
        return normalize_message(assistant_msgs[-1])
    return normalize_message(msg)

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
                roles = [m.get("role", m.get("info", {}).get("role", "?"))
                         for m in messages if isinstance(m, dict)]
                print(f"  [poll {poll_count}] {len(messages)} message(s), roles={roles}")
                if poll_count == 1 and messages:
                    # Dump first message structure for debugging
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

def main():
    parser = argparse.ArgumentParser(description="Check opencode server health and send a test message")
    parser.add_argument("-d", "--directory",
                        default=os.getcwd(),
                        help="Target project directory (default: current working directory)")
    args = parser.parse_args()
    directory = os.path.abspath(args.directory)

    # 1) Health check: GET /global/health
    health = opencode_request("GET", "/global/health")
    print("Health:", health)

    # 2) List agents: GET /agent
    agents = opencode_request("GET", "/agent")
    print("Agents:", [a.get("id") or a.get("name") for a in agents])

    # 3) Create a session: POST /session
    session = opencode_request("POST", "/session",
                               json={"title": "Python SDK-like demo"},
                               params={"directory": directory})
    session_id = session["id"]
    print("Session ID:", session_id)
    print("Directory:", directory)

    configured_model, configured_name = resolve_model(agent="build")
    selected_model, provider_label, model_label, model_warning = choose_server_model(
        configured_model,
        preferred_name=configured_name,
        directory=directory,
    )

    if model_warning:
        print(f"[warn] {model_warning}")

    if selected_model:
        print(f"Model: {provider_label or selected_model['providerID']}:{model_label or selected_model['modelID']}")
    elif provider_label and model_label:
        print(f"Model: {provider_label}:{model_label} (server default)")
    else:
        print("Model: server default")

    # 4) Send a message and wait for response: POST /session/:id/message
    body = {
        "agent": "build",
        "parts": [{"type": "text", "text": "Say hello and summarize what you can do in this repo."}],
    }
    if selected_model:
        body["model"] = selected_model

    msg = send_message_and_wait(session_id=session_id, body=body, directory=directory)

    # If provider rejects alias model IDs (e.g. "my-model-name"), retry once with
    # a server-native model that has the same display name (e.g. "glm-5").
    err_msg = assistant_error_message(msg) or ""
    if selected_model and "unknown model" in err_msg.lower():
        alt = find_alternative_model_by_name(
            model_name=(model_label or selected_name or "").strip(),
            exclude_model=selected_model,
            directory=directory,
        )
        if alt:
            print(
                "[warn] Provider returned 'Unknown Model'. Retrying with "
                f"{alt['providerLabel']}:{alt['modelLabel']} ({alt['providerID']}:{alt['modelID']})."
            )
            retry_body = {
                "agent": "build",
                "model": {"providerID": alt["providerID"], "modelID": alt["modelID"]},
                "parts": body["parts"],
            }
            msg = send_message_and_wait(session_id=session_id, body=retry_body, directory=directory)

    print("\nAssistant message info:", msg.get("info", {}))
    print("Assistant parts:")
    for p in msg.get("parts", []):
        if p.get("type") == "text":
            print("-", p.get("text"))

if __name__ == "__main__":
    main()
