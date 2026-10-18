"""Local sidecar secrets under ~/.civagent/secrets.json (决策 21).

Written by the Unciv Options → CivAgent tab (or Host). Never part of GameInfo.
Resolution for LLM keys: process env > this sidecar > config.local.yaml / config.yaml.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

PROVIDER_ENV = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai_compatible": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def secrets_path() -> Path:
    override = os.environ.get("CIVAGENT_SECRETS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".civagent" / "secrets.json"


def key_hint(api_key: str) -> str:
    trimmed = (api_key or "").strip()
    if not trimmed:
        return "(not set)"
    if len(trimmed) <= 4:
        return "••••"
    return f"••••{trimmed[-4:]}"


def load_secrets() -> Dict[str, Any]:
    path = secrets_path()
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_secrets(data: Dict[str, Any]) -> Path:
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": str(data.get("provider") or "deepseek"),
        "apiKey": str(data.get("apiKey") or data.get("api_key") or ""),
        "baseUrl": str(data.get("baseUrl") or data.get("base_url") or ""),
        "aiServerUrl": str(data.get("aiServerUrl") or data.get("ai_server_url") or "http://127.0.0.1:2335"),
        "repoRoot": str(data.get("repoRoot") or data.get("repo_root") or ""),
        "model": str(data.get("model") or ""),
        "streaming": bool(data.get("streaming") or False),
        "sampling": data.get("sampling") if isinstance(data.get("sampling"), dict) else {},
    }
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    return path


def apply_secrets_to_environ(data: Optional[Dict[str, Any]] = None, *, force: bool = False) -> Dict[str, Any]:
    """Map sidecar into os.environ for llm_router. force=True overwrites (UI hot-reload)."""
    secrets = data if data is not None else load_secrets()
    if not secrets and not force:
        return secrets
    provider = str(secrets.get("provider") or "deepseek").strip().lower() or "deepseek"
    api_key = str(secrets.get("apiKey") or secrets.get("api_key") or "").strip()
    base_url = str(secrets.get("baseUrl") or secrets.get("base_url") or "").strip()

    def _set(env_key: str, value: str) -> None:
        if force:
            if value:
                os.environ[env_key] = value
            else:
                os.environ.pop(env_key, None)
            return
        if value and not os.environ.get(env_key):
            os.environ[env_key] = value

    if force or not os.environ.get("LLM_DEFAULT_PROVIDER"):
        if force or provider:
            os.environ["LLM_DEFAULT_PROVIDER"] = provider

    env_key = PROVIDER_ENV.get(provider)
    if env_key:
        _set(env_key, api_key)
        if provider == "gemini":
            _set("GOOGLE_API_KEY", api_key)
    if force or base_url:
        _set("OPENAI_BASE_URL", base_url)
    # Options → CivAgent: global model override + streaming toggle + sampling knobs.
    _set("CIVAGENT_MODEL", str(secrets.get("model") or "").strip())
    _set("CIVAGENT_STREAMING", "1" if secrets.get("streaming") else "")
    sampling = secrets.get("sampling")
    _set("CIVAGENT_SAMPLING", json.dumps(sampling) if isinstance(sampling, dict) and sampling else "")
    return secrets


def public_status(secrets: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    secrets = secrets if secrets is not None else load_secrets()
    api_key = str(secrets.get("apiKey") or secrets.get("api_key") or "")
    return {
        "provider": str(secrets.get("provider") or "deepseek"),
        "configured": bool(api_key.strip()),
        "keyHint": key_hint(api_key),
        "baseUrl": str(secrets.get("baseUrl") or secrets.get("base_url") or ""),
        "aiServerUrl": str(secrets.get("aiServerUrl") or "http://127.0.0.1:2335"),
        "model": str(secrets.get("model") or ""),
        "streaming": bool(secrets.get("streaming") or False),
        "sampling": dict(secrets.get("sampling") or {}),
        "path": str(secrets_path()),
    }
