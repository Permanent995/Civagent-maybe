import json
import os
from typing import Any, Dict, Optional

import yaml

from civagent.utils import secrets_store

# Resolution order: $CIVAGENT_CONFIG_PATH -> ./config.yaml -> empty (import must never crash).
# Secrets: process env > ~/.civagent/secrets.json sidecar > config.local.yaml / yaml.
config_path: Optional[str] = os.environ.get("CIVAGENT_CONFIG_PATH")
local_config_path: Optional[str] = os.environ.get("CIVAGENT_CONFIG_LOCAL_PATH") or "config.local.yaml"


def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader: KEY=VALUE lines, no shell interpolation, existing env wins.

    Skipped when CIVAGENT_NO_DOTENV is set - it writes os.environ directly, so otherwise the
    developer's real .env (e.g. a relay endpoint) would leak into every test that reloads config.
    """
    if os.environ.get("CIVAGENT_NO_DOTENV"):
        return
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    llm = config.setdefault("LLM", {})
    env_map = {
        "DEEPSEEK_API_KEY": "deepseek_api_key",
        "OPENAI_API_KEY": "openai_api_key",
        "ANTHROPIC_API_KEY": "anthropic_api_key",
        "GEMINI_API_KEY": "gemini_api_key",
        "GOOGLE_API_KEY": "gemini_api_key",
        "OPENAI_BASE_URL": "openai_base_url",
        "CIVAGENT_MODEL": "model",
    }
    for env_key, cfg_key in env_map.items():
        value = os.environ.get(env_key)
        if value:
            llm[cfg_key] = value
    # Options → CivAgent streaming toggle (sidecar maps to "1" / "").
    streaming = os.environ.get("CIVAGENT_STREAMING")
    if streaming is not None:
        llm["streaming"] = streaming not in ("", "0", "false", "False")
    # Options → CivAgent sampling knobs (single JSON blob; unset keys are cleared).
    raw_sampling = os.environ.get("CIVAGENT_SAMPLING")
    if raw_sampling is not None:
        try:
            parsed = json.loads(raw_sampling) if raw_sampling.strip() else {}
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        for key in ("temperature", "max_tokens", "top_p", "frequency_penalty", "presence_penalty"):
            llm.pop(key, None)
        for key, value in parsed.items():
            if value is not None and value != "":
                llm[key] = value
    default_provider = os.environ.get("LLM_DEFAULT_PROVIDER")
    if default_provider:
        llm["default_provider"] = default_provider
        llm["default_model"] = default_provider
    return config


def load_config() -> Dict[str, Any]:
    _load_dotenv()
    # Sidecar fills empty env slots (UI-written keys); explicit env still wins.
    secrets_store.apply_secrets_to_environ(force=False)
    path = config_path or (
        "config.yaml" if os.path.isfile("config.yaml") else None
    )
    config: Dict[str, Any] = {}
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as file:
            config = yaml.safe_load(file) or {}
    if local_config_path and os.path.isfile(local_config_path):
        with open(local_config_path, "r", encoding="utf-8") as file:
            local = yaml.safe_load(file) or {}
        config = _deep_merge(config, local)
    return _apply_env_overrides(config)


def reload_config_data() -> Dict[str, Any]:
    """Re-read yaml + sidecar into the module-level snapshot (after UI hot-reload)."""
    global config_data
    config_data = load_config()
    return config_data


config_data = load_config()
