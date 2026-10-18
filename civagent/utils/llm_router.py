"""Vendor LLM routing (EXECUTION.md W1b).

All API keys come from environment slots (falling back to config.yaml's LLM section):
DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY|GOOGLE_API_KEY /
OPENAI_BASE_URL. Empty key = provider not configured. Keys are never logged; use
[redact] on anything that might carry a secret before it reaches a sink.

Seats (countries) only override the *model id* in config.yaml; the provider key slots
are shared. A seat model value may be a provider name ("deepseek") or a concrete model
id ("deepseek-chat", "gpt-4o", "claude-3-5-sonnet-latest", "gemini-1.5-flash", ...).
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from civagent.config import config_data
from civagent.utils import local_models

PROVIDERS = ("deepseek", "openai", "openai_compatible", "gemini", "anthropic", "ollama")

# Everything local speaks the OpenAI wire protocol; only Ollama has a native API as well.
# A seat may name its server explicitly (`vllm/Qwen/Qwen2.5-32B-Instruct`) to skip guessing.
LOCAL_ALIASES = {
    "ollama": "ollama",
    "vllm": "openai_compatible",
    "sglang": "openai_compatible",
    "lmstudio": "openai_compatible",
    "llamacpp": "openai_compatible",
    "llama-cpp": "openai_compatible",
    "lmdeploy": "openai_compatible",
    "mlx": "openai_compatible",
    "koboldcpp": "openai_compatible",
    "local": "openai_compatible",
}

# Quantisation / size / distill markers: with any of these in the id the model is a local
# checkpoint, not a vendor API product - `deepseek-r1-distill-qwen-14b` must not route to api.deepseek.com.
_LOCAL_ID = re.compile(
    r"(?:^|[-_:])(?:q\d{2}(?:_[a-z]\d?)?|iq\d|awq|gptq|fp16|bf16|int[24]|nl[24]|exl[23])(?:$|[-_:])"
    r"|(?:^|[-_:])\d+(?:\.\d+)?b(?:$|[-_:])"
    r"|(?:-distill|-gguf|-gptq|-awq)"
    r"|[:]|(?:^|[^/])/[^/]+$"
)

DEFAULT_MODEL_IDS = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
    "gemini": "gemini-1.5-flash",
    "anthropic": "claude-3-5-sonnet-latest",
    "ollama": "llama3",
    "openai_compatible": "default",
}

DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "ollama": "http://localhost:11434/v1",
    "anthropic": "https://api.anthropic.com/v1",
}

_SECRET_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "sk-***"),
    (re.compile(r"(Bearer\s+)\S{12,}"), lambda m: m.group(1) + "***"),
)


def redact(text: Any) -> str:
    """Scrub API-key-looking secrets from anything destined for a log sink."""
    text = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _llm_entry(key: str, default: str = "") -> str:
    env_value = os.environ.get(key)
    if env_value:
        return env_value
    return str(config_data.get("LLM", {}).get(key) or default)


def default_provider() -> str:
    provider = os.environ.get("LLM_DEFAULT_PROVIDER") or config_data.get("LLM", {}).get("default_provider")
    provider = str(provider or "deepseek").lower()
    return provider if provider in PROVIDERS else "deepseek"


def explicit_provider_choice() -> Optional[str]:
    """The provider the user actually selected, or None when nothing was chosen.

    `default_provider()` cannot answer this: it falls back to "deepseek", and a fallback is not
    a choice - treating it as one would route every local checkpoint to a cloud API.
    """
    raw = str(os.environ.get("LLM_DEFAULT_PROVIDER")
              or config_data.get("LLM", {}).get("default_provider") or "").lower().strip()
    if not raw:
        return None
    if raw in PROVIDERS:
        return raw
    return LOCAL_ALIASES.get(raw)


def _looks_local(model: str) -> bool:
    """A quantised / sized / tagged / HF-repo id is a local checkpoint, whatever it is named."""
    return bool(_LOCAL_ID.search(model.lower()))


def resolve_provider(model: str, default: Optional[str] = None) -> str:
    """Map a config model value / concrete model id to a provider.

    Guess order matters: an explicit `server/` prefix beats everything, a *chosen* default
    provider beats name heuristics for local checkpoints, and only then does the model name
    itself speak. The old code let the name always win, which pinned every `qwen*` seat to
    Ollama's :11434 - a vLLM or LM Studio user got a silent fallback to native AI.
    """
    lowered = str(model or "").lower().strip()
    if "/" in lowered and not lowered.startswith(("http://", "https://")):
        head, _, tail = lowered.partition("/")
        if tail and head in LOCAL_ALIASES:
            return LOCAL_ALIASES[head]
    if lowered in PROVIDERS:
        return lowered
    if lowered in LOCAL_ALIASES:
        return LOCAL_ALIASES[lowered]

    if _looks_local(lowered):
        chosen = default if default is not None else explicit_provider_choice()
        if chosen in ("ollama", "openai_compatible"):
            return chosen
        # Nobody chose: an HF repo id is a vLLM/llama.cpp launch, a `name:tag` is Ollama,
        # and a bare family name keeps the historical Ollama default.
        if "/" in lowered:
            return "openai_compatible"
        return "ollama"

    if "deepseek" in lowered:
        return "deepseek"
    if "claude" in lowered:
        return "anthropic"
    if "gemini" in lowered:
        return "gemini"
    if lowered.startswith("gpt") or lowered.startswith("o1") or lowered.startswith("o3"):
        return "openai"
    if lowered.startswith(("mistral", "llama", "gemma", "qwen")):
        return "ollama"
    return "openai_compatible"


def local_base_url(kind_hint: Optional[str] = None) -> str:
    """Explicit setting first, then whatever local server is actually answering.

    Without this a user with vLLM on :8000 had to know that `OPENAI_BASE_URL` is the variable
    the router reads for `openai_compatible` - and if they left it empty the seat resolved to a
    provider with no endpoint and fell back to native AI with no explanation.
    """
    explicit = (_llm_entry("CIVAGENT_LOCAL_BASE_URL") or _llm_entry("local_base_url")
                or _llm_entry("civagent_local_base_url"))
    if explicit:
        return explicit.rstrip("/")
    return local_models.first_local_base_url(kind_hint) or ""


def get_provider_config(provider: Optional[str] = None) -> Dict[str, str]:
    """Resolve key/base_url slots for a provider. Empty api_key = not configured."""
    provider = provider or default_provider()
    if provider == "deepseek":
        return {"api_key": _llm_entry("DEEPSEEK_API_KEY") or _llm_entry("deepseek_api_key"), "base_url": DEFAULT_BASE_URLS["deepseek"]}
    if provider == "openai":
        return {
            "api_key": _llm_entry("OPENAI_API_KEY") or _llm_entry("openai_api_key"),
            "base_url": _llm_entry("OPENAI_BASE_URL") or _llm_entry("openai_base_url") or DEFAULT_BASE_URLS["openai"],
        }
    if provider == "openai_compatible":
        return {
            "api_key": _llm_entry("OPENAI_API_KEY") or _llm_entry("openai_api_key"),
            "base_url": (_llm_entry("OPENAI_BASE_URL") or _llm_entry("openai_base_url")
                         or local_base_url()),
        }
    if provider == "gemini":
        return {
            "api_key": _llm_entry("GEMINI_API_KEY") or _llm_entry("GOOGLE_API_KEY") or _llm_entry("gemini_api_key"),
            "base_url": DEFAULT_BASE_URLS["gemini"],
        }
    if provider == "anthropic":
        return {"api_key": _llm_entry("ANTHROPIC_API_KEY") or _llm_entry("anthropic_api_key"), "base_url": DEFAULT_BASE_URLS["anthropic"]}
    if provider == "ollama":
        base_url = (os.environ.get("OLLAMA_BASE_URL") or _llm_entry("ollama_base_url")
                    or local_models.first_local_base_url("ollama")
                    or DEFAULT_BASE_URLS["ollama"])
        return {"api_key": "ollama", "base_url": base_url.rstrip("/")}
    raise ValueError(f"unknown provider: {provider}")


def provider_missing_reason(provider: Optional[str] = None) -> Optional[str]:
    """Why a provider is unusable, or None when it is configured.

    Configuration only - use `local_server_problem` for "is the box actually answering".
    """
    provider = provider or default_provider()
    provider_config = get_provider_config(provider)
    if provider == "ollama":
        return None
    if provider == "openai_compatible":
        base_url = provider_config["base_url"]
        if not base_url:
            return ("OPENAI_BASE_URL is not set and no local inference server answered on "
                    "the usual ports (Ollama 11434 / LM Studio 1234 / vLLM 8000 / "
                    "SGLang 30000 / llama.cpp 8080)")
        if local_models.is_local_base_url(base_url):
            return None  # loopback servers need no key
        if not provider_config["api_key"]:
            return "no api key configured (set OPENAI_API_KEY)"
        return None
    if not provider_config["api_key"]:
        env_hint = {
            "deepseek": "DEEPSEEK_API_KEY",
            "openai": "OPENAI_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }.get(provider, "OPENAI_API_KEY")
        return f"no api key configured (set {env_hint})"
    return None


def _local_problem_for(provider: str, model_id: str) -> Optional[str]:
    if provider not in ("ollama", "openai_compatible"):
        return None
    base_url = get_provider_config(provider)["base_url"]
    if not local_models.is_local_base_url(base_url):
        return None
    if not model_id:
        return f"no model selected for provider '{provider}'"
    return local_models.server_problem(base_url, model_id)


def local_server_problem(provider: Optional[str] = None, country: Optional[str] = None) -> Optional[str]:
    """Why a *configured* local provider cannot serve this seat's model right now.

    A powered-off GPU box passes every key check, so without this the brain reports `ok` and
    the game just shows native AI behaviour.
    """
    provider, model_id = model_id_for(country)
    return _local_problem_for(provider, model_id)


def client_dependency_problem(provider: Optional[str] = None) -> Optional[str]:
    """Why the provider's client cannot be *built* (missing deps), or None when it can.

    `provider_missing_reason` only checks configuration (api key / base url). A brain with a
    valid key but a missing package (e.g. `instructor`) looks healthy and then fails on the
    first chat - this probe lets /healthz report `broken` instead of `ok`.
    """
    provider = (provider or default_provider()).lower()
    if provider == "ollama":
        return None
    try:
        if provider == "anthropic":
            import anthropic  # noqa: F401
            return None
        # Building the OpenAI+instructor client does not touch the network.
        _make_instructor_client("http://127.0.0.1", "probe")
    except Exception as ex:
        return f"{type(ex).__name__}: {ex}"
    return None


def configured_model() -> str:
    """Global model override from Options → CivAgent. Empty = the provider's default."""
    return str(config_data.get("LLM", {}).get("model") or "")


def streaming_enabled() -> bool:
    """Options → CivAgent: stream chat replies token-by-token instead of one final answer."""
    return bool(config_data.get("LLM", {}).get("streaming"))


def sampling_params() -> Dict[str, Any]:
    """Options → CivAgent sampling knobs.

    Only values the user actually set are forwarded, so an empty setting keeps the provider
    default (and avoids providers/models that reject e.g. `temperature`).
    """
    llm = config_data.get("LLM", {})
    out: Dict[str, Any] = {}
    for key, cast in (
        ("temperature", float),
        ("max_tokens", int),
        ("top_p", float),
        ("frequency_penalty", float),
        ("presence_penalty", float),
    ):
        value = llm.get(key)
        if value is None or value == "":
            continue
        try:
            out[key] = cast(value)
        except (TypeError, ValueError):
            continue
    return out


def estimate_tokens(text: str) -> int:
    """Cheap CJK-aware estimate: ~4 ASCII chars per token, ~1 token per CJK/kana/hangul char."""
    if not text:
        return 0
    wide = sum(
        1
        for ch in text
        if "\u3400" <= ch <= "\u9fff"
        or "\u3040" <= ch <= "\u30ff"
        or "\uac00" <= ch <= "\ud7af"
    )
    return wide + max(1, (len(text) - wide) // 4)


def strip_server_prefix(model: str) -> str:
    """`vllm/Qwen/Qwen2.5-32B-Instruct` -> `Qwen/Qwen2.5-32B-Instruct` (the prefix only routes)."""
    text = str(model or "")
    lowered = text.lower()
    if "/" in lowered and not lowered.startswith(("http://", "https://")):
        head, _, tail = lowered.partition("/")
        if tail and head in LOCAL_ALIASES:
            return text.split("/", 1)[1]
    return text


def _local_first_model(provider: str, fallback: str) -> str:
    """What the local server actually has, when the config only named the provider.

    `DEFAULT_MODEL_IDS["ollama"] = llama3` is a guess; a box holding only qwen3 answers
    "model 'llama3' is not loaded" and the seat goes native.
    """
    if provider not in ("ollama", "openai_compatible"):
        return fallback
    names = local_models.local_model_names(get_provider_config(provider)["base_url"])
    return names[0] if names else fallback


def model_id_for(country: Optional[str], model: Optional[str] = None) -> Tuple[str, str]:
    """Resolve (provider, model_id). Seat config only overrides the model id.

    `model` wins when given; else the seat's config entry (model value may be a provider
    name or a concrete model id); else the default provider's default model id.
    """
    raw = model
    if not raw:
        # Options → CivAgent global choice wins over the per-seat yaml block (it is an explicit
        # user action); empty means "not set", so per-seat routing still works by default.
        raw = configured_model()
    if not raw and country:
        raw = config_data.get(country.lower(), {}).get("model")
    if raw:
        named_provider = str(raw).lower().strip()
        provider = resolve_provider(strip_server_prefix(str(raw)))
        model_id = strip_server_prefix(str(raw))
        if named_provider in PROVIDERS or named_provider in LOCAL_ALIASES:
            model_id = _local_first_model(provider, DEFAULT_MODEL_IDS[provider])
        return provider, model_id
    provider = default_provider()
    return provider, _local_first_model(provider, DEFAULT_MODEL_IDS[provider])


def chat(
    payload: Dict[str, Any],
    model: Optional[str] = None,
    request_timeout: float = 130.0,
    llm_config: Optional[Dict[str, Any]] = None,
    country: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Single entry point for all LLM calls. Returns (message, raw) like the legacy utils."""
    llm_config = llm_config or {}
    provider, model_id = model_id_for(country, model)
    reason = provider_missing_reason(provider)
    if reason:
        raise RuntimeError(f"LLM provider '{provider}' not configured: {reason}")
    down = _local_problem_for(provider, model_id)
    if down:
        # Cached probe, so this costs nothing per turn - and it names the port instead of
        # letting the call die in a 130s timeout that the game already fell out of.
        raise RuntimeError(f"local model unavailable: {down}")
    provider_config = get_provider_config(provider)
    if provider == "anthropic":
        return _chat_anthropic(provider_config, model_id, payload, llm_config, request_timeout)
    return _chat_openai_compatible(provider_config, model_id, payload, llm_config)


def chat_stream_text(
    payload: Dict[str, Any],
    *,
    country: Optional[str] = None,
    model: Optional[str] = None,
    on_delta: Optional[Any] = None,
    request_timeout: float = 130.0,
) -> Tuple[str, Dict[str, Any]]:
    """Plain-text streaming completion (Options → CivAgent "Streaming").

    Deliberately bypasses instructor/JSON mode: streaming a structured object would only emit an
    unparsable prefix, so the chat prompt already asks for plain prose plus one ```intent fence,
    which callers parse out of the returned text.
    """
    provider, model_id = model_id_for(country, model)
    reason = provider_missing_reason(provider)
    if reason:
        raise RuntimeError(f"LLM provider '{provider}' not configured: {reason}")
    provider_config = get_provider_config(provider)
    if provider == "anthropic":
        # No Anthropic streaming path yet: one buffered plain-text call, delivered as one delta.
        message, raw = _chat_anthropic(
            provider_config, model_id, payload, {"response_model": None}, request_timeout
        )
        text = str(message.get("content") or "")
        if on_delta:
            on_delta(text)
        return text, raw

    from openai import OpenAI

    client = OpenAI(
        base_url=provider_config["base_url"],
        api_key=provider_config["api_key"] or "none",
        timeout=request_timeout,
    )
    stream = client.chat.completions.create(
        model=model_id, messages=payload["messages"], stream=True, **sampling_params()
    )
    parts: List[str] = []
    scratch: List[str] = []
    for chunk in stream:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = choices[0].delta
        piece = getattr(delta, "content", None)
        if not piece:
            # Servers that split the scratchpad onto the wire use `reasoning_content`; it must
            # not reach the player's bubble, but it is the only text if content stays empty.
            hidden = getattr(delta, "reasoning_content", None)
            if hidden:
                scratch.append(hidden)
            continue
        parts.append(piece)
        if on_delta:
            # Cutting here (not only at the end) is what keeps `` from painting
            # the model's private reasoning into the live bubble.
            on_delta(local_models.strip_reasoning("".join(parts)))
    text = "".join(parts)
    text = local_models.strip_reasoning(text) or local_models.strip_reasoning("".join(scratch))
    return text, {"provider": provider, "model": model_id, "streamed": True}


# Local generations are slow but must not outlive the call that wanted them: the game gives up
# at 15 s, and an unbounded brain thread would hold the seat lock for OpenAI's 600 s default.
LOCAL_REQUEST_TIMEOUT_S = 120.0


def _make_instructor_client(base_url: str, api_key: str, mode=None):
    """Seam for tests: production builds a real instructor-wrapped OpenAI client.

    Only the local path passes `mode`; the cloud path keeps the client defaults byte-for-byte.
    """
    import instructor
    from openai import OpenAI

    timeout = LOCAL_REQUEST_TIMEOUT_S if mode is not None else None
    client = OpenAI(base_url=base_url, api_key=api_key or "none", timeout=timeout)
    return instructor.from_openai(client, mode=mode or instructor.Mode.JSON)


def _openai_client(base_url: str, api_key: str):
    from openai import OpenAI

    return OpenAI(base_url=base_url, api_key=api_key or "none", timeout=LOCAL_REQUEST_TIMEOUT_S)


# What constrained-output mechanism to try, per what the server answered to the capability probe.
_OUTPUT_LADDER = {
    "json_schema": ("json_schema", "json", "plain"),
    "json": ("json", "plain"),
    "unknown": ("json", "plain"),
    "none": ("plain",),
}


def _chat_openai_compatible(
    provider_config: Dict[str, str],
    model_id: str,
    payload: Dict[str, Any],
    llm_config: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    response_model = llm_config.get("response_model", None)
    base_url = provider_config["base_url"]
    if local_models.is_local_base_url(base_url):
        structured = local_models.profile_for(base_url, model_id)["structured"]
        return _chat_with_ladder(provider_config, model_id, payload, response_model, structured)

    client = _make_instructor_client(base_url, provider_config["api_key"])
    resp = client.chat.completions.create(
        model=model_id,
        messages=payload["messages"],
        response_model=response_model,
        **sampling_params(),
    )
    if response_model is None:
        content = resp.choices[0].message.content
        raw: Dict[str, Any] = {"content": content}
    else:
        content = resp.model_dump_json()
        raw = {"content": content}
    return {"role": "user", "content": content}, raw


def _plain_completion(
    provider_config: Dict[str, str], model_id: str, payload: Dict[str, Any]
) -> str:
    """One unstructured completion, reasoning stripped - what a small local model can always do."""
    client = _openai_client(provider_config["base_url"], provider_config["api_key"])
    resp = client.chat.completions.create(
        model=model_id, messages=payload["messages"], **sampling_params()
    )
    return local_models.strip_reasoning(str(resp.choices[0].message.content or ""))


def _plain_json_completion(
    provider_config: Dict[str, str], model_id: str, payload: Dict[str, Any], response_model: Any
) -> str:
    """Ask for the JSON in prose and validate it ourselves.

    This is the rung that saves a weak local model: no `response_format` support, no tool
    calling, just "output only this object" plus one retry with the schema in the message.
    """
    schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
    attempts = [
        payload["messages"],
        payload["messages"] + [{"role": "system", "content":
            "Output exactly one JSON object matching this schema and nothing else - no prose, "
            f"no markdown fences, no reasoning.\n{schema}"}],
    ]
    last_error: Optional[str] = None
    for messages in attempts:
        text = _plain_completion(provider_config, model_id, {"messages": messages})
        if not text.strip():
            last_error = "empty completion (raise max_tokens: the model spent them thinking)"
            continue
        try:
            return response_model.model_validate_json(_extract_json(text)).model_dump_json()
        except Exception as ex:
            last_error = f"{type(ex).__name__}: {ex}"
    raise RuntimeError(f"unparsable JSON from local model: {last_error}")


def _chat_with_ladder(
    provider_config: Dict[str, str],
    model_id: str,
    payload: Dict[str, Any],
    response_model: Any,
    structured: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Try the strongest output mechanism the server claimed, downgrading on any failure.

    The probe can only ask "does the server accept the field", not "does this checkpoint use it
    well" - so a model that returns prose under json_schema still lands on the rung that works
    instead of erroring out into a native-AI fallback.
    """
    rungs = _OUTPUT_LADDER.get(structured, _OUTPUT_LADDER["unknown"])
    if response_model is None:
        text = _plain_completion(provider_config, model_id, payload)
        return {"role": "user", "content": text}, {"content": text, "structured_mode": "plain"}

    base_url = provider_config["base_url"]
    api_key = provider_config["api_key"]
    import instructor

    last_error: Optional[BaseException] = None
    for rung in rungs:
        try:
            if rung == "plain":
                content = _plain_json_completion(provider_config, model_id, payload, response_model)
            else:
                mode = instructor.Mode.JSON_SCHEMA if rung == "json_schema" else instructor.Mode.JSON
                client = _make_instructor_client(base_url, api_key, mode)
                content = client.chat.completions.create(
                    model=model_id, messages=payload["messages"],
                    response_model=response_model, **sampling_params(),
                ).model_dump_json()
            return {"role": "user", "content": content}, {
                "content": content, "structured_mode": rung, "model": model_id,
            }
        except Exception as ex:
            last_error = ex
            continue
    raise RuntimeError(
        f"local model '{model_id}' at {base_url} produced no valid "
        f"{response_model.__name__} (tried {' -> '.join(rungs)}): {last_error}"
    )


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> str:
    match = _JSON_BLOCK.search(text)
    return match.group(0) if match else text


def _chat_anthropic(
    provider_config: Dict[str, str],
    model_id: str,
    payload: Dict[str, Any],
    llm_config: Dict[str, Any],
    request_timeout: float,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Anthropic Messages API over httpx (no extra dependency). JSON mode via schema prompt."""
    import httpx

    response_model = llm_config.get("response_model", None)
    system_lines = []
    messages = []
    for entry in payload["messages"]:
        if entry.get("role") == "system":
            system_lines.append(entry["content"])
        else:
            messages.append({"role": entry["role"], "content": entry["content"]})
    if response_model is not None:
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        system_lines.append(
            "Respond with a single JSON object that validates this JSON Schema. "
            f"No prose, no markdown fences.\n{schema}"
        )
    response = httpx.post(
        f"{provider_config['base_url']}/messages",
        headers={
            "x-api-key": provider_config["api_key"],
            "anthropic-version": "2023-06-01",
        },
        json={
            "model": model_id,
            "max_tokens": int(llm_config.get("max_tokens", 2048)),
            "system": "\n\n".join(system_lines),
            "messages": messages,
        },
        timeout=request_timeout,
    )
    response.raise_for_status()
    raw_body = response.json()
    text = "".join(block.get("text", "") for block in raw_body.get("content", []))
    if response_model is not None:
        content = response_model.model_validate_json(_extract_json(text)).model_dump_json()
    else:
        content = text
    raw = {"content": text, "usage": raw_body.get("usage", {})}
    return {"role": "user", "content": content}, raw
