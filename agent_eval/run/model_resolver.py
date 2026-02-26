"""Model configuration and server catalog resolution."""

import os
import re
import json
from pathlib import Path

from .opencode_client import opencode_request

OPENCODE_CONFIG_PATH = Path(
    os.getenv("OPENCODE_CONFIG_PATH", "~/.config/opencode/config.json")
).expanduser()


def parse_model_spec(spec: str):
    spec = (spec or "").strip()
    if not spec:
        return None

    # Split on whichever separator appears first so that the model ID can
    # contain the other separator.  For example:
    #   "openrouter:anthropic/claude-sonnet-4"  → : first → openrouter + anthropic/claude-sonnet-4
    #   "openrouter/deepseek/deepseek-r1:free"  → / first → openrouter + deepseek/deepseek-r1:free
    colon_pos = spec.find(":")
    slash_pos = spec.find("/")
    if colon_pos < 0 and slash_pos < 0:
        return None
    if colon_pos >= 0 and (slash_pos < 0 or colon_pos < slash_pos):
        sep_pos = colon_pos
    else:
        sep_pos = slash_pos

    provider_id = spec[:sep_pos].strip()
    model_id = spec[sep_pos + 1:].strip()
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


def choose_server_model(requested_model, preferred_name: str | None = None,
                        directory: str | None = None):
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
            # Prefer canonical IDs that look like a slug of the model label.
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
        print(f"[warn] Ignoring invalid OPENCODE_MODEL={env_model!r} "
              "(expected provider/model or provider:model).")

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
