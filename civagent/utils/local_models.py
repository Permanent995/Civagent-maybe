"""Local inference-server discovery and capability probing (httpx only, no new dependencies).

Why this exists: a seat's model id says nothing about *where* it runs. `qwen3:32b`,
`Qwen/Qwen2.5-32B-Instruct` and `deepseek-r1-distill-qwen` can each come from Ollama, LM Studio,
vLLM, SGLang or llama.cpp - on different ports, with different context budgets, and with or
without schema-constrained output. Guessing wrong looked like "the brain is fine, every seat
fell back to native AI", so the truth has to be probed and reported.

Everything here is duck-typed against `httpx.Response` so tests can fake the transport.
"""

import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

# Discovery must never stall a turn: a closed port fails instantly, an alive box answers fast.
PROBE_TIMEOUT_S = 0.8
GENERATE_PROBE_TIMEOUT_S = 8.0
CACHE_TTL_OK_S = 600.0
CACHE_TTL_FAIL_S = 30.0

# kind -> OpenAI-compatible base url. The `/v1` suffix is what every one of these serves;
# Ollama additionally has a native API at the root (/api/tags, /api/show, /api/ps).
#
# The names here are PORT HINTS, not identifications: llama.cpp's historic default is 8080 and
# its new default is 9931, mlx-lm also uses 8080, so `discover()` labels by port and
# `flavor_for()` then identifies by endpoint signature (which is the only thing that actually
# distinguishes vLLM from SGLang from LM Studio over the wire).
LOCAL_CANDIDATES: Tuple[Tuple[str, str], ...] = (
    ("ollama", "http://127.0.0.1:11434/v1"),
    ("lmstudio", "http://127.0.0.1:1234/v1"),
    ("vllm", "http://127.0.0.1:8000/v1"),
    ("sglang", "http://127.0.0.1:30000/v1"),
    ("llamacpp", "http://127.0.0.1:8080/v1"),
    ("llamacpp", "http://127.0.0.1:9931/v1"),   # llama.cpp default is migrating 8080 -> 9931
    ("lmdeploy", "http://127.0.0.1:23333/v1"),
    ("koboldcpp", "http://127.0.0.1:5001/v1"),
)

# Server-specific names for "how much context did you actually load?" - a GGUF build with a
# shrunken n_ctx is the single most common local deployment complaint.
_CONTEXT_KEYS = ("context_length", "max_context_length", "n_ctx", "total_n_ctx", "actual_ctx",
                 "max_model_len", "total_context", "num_ctx", "n_ctx_train",
                 "max_position_embeddings")
_QUANT_KEYS = ("quantization", "quantization_type", "quant_type", "file_type",
               "quantization_version")

_THINK_BLOCK = re.compile(r"<(?P<tag>think|thinking|reasoning)>(?P<body>.*?)</(?P=tag)>",
                          re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<(?:think|thinking|reasoning)\b[^>]*>", re.IGNORECASE)
# Prose-labelled scratchpads (some GGUF chat templates): consume through the answer marker so
# only what the model actually says stays in the bubble.
_LABELLED_REASONING = re.compile(
    r"^[ \t]*\*{0,2}(?:Reasoning|Thinking|Thought)\s*:\s*\n"
    r".*?"
    r"^[ \t]*\*{0,2}(?:Answer|Response)\s*:\s*",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)


def _get(url: str, timeout: float = PROBE_TIMEOUT_S):
    """Seam for tests: production uses httpx.get."""
    import httpx

    return httpx.get(url, timeout=timeout)


def _post(url: str, payload: Dict[str, Any], timeout: float = GENERATE_PROBE_TIMEOUT_S):
    """Seam for tests: production uses httpx.post."""
    import httpx

    return httpx.post(url, json=payload, timeout=timeout)


def is_local_base_url(base_url: Optional[str]) -> bool:
    """Loopback only: a LAN/cloud base url is someone else's configuration problem."""
    host = (urlsplit(base_url or "").hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"} or host.startswith("127.")


def root_of(base_url: str) -> str:
    """`http://host:11434/v1` -> `http://host:11434` (Ollama's native API lives at the root)."""
    parts = urlsplit(base_url.rstrip("/"))
    path = parts.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[: -len("/v1")]
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _status(response: Any) -> int:
    return int(getattr(response, "status_code", 0) or 0)


def _body(response: Any) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _key_matches(key: str, wanted: Tuple[str, ...]) -> bool:
    """Exact, or namespaced the way Ollama's /api/show reports it (`qwen2.context_length`)."""
    return key in wanted or key.split(".")[-1] in wanted


def _walk_numbers(body: Any, keys: Tuple[str, ...]) -> Optional[int]:
    """Find the first positive int under any of `keys`, at any depth (fields vary per server)."""
    if isinstance(body, dict):
        for key, value in body.items():
            if _key_matches(str(key), keys) and isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
        for value in body.values():
            found = _walk_numbers(value, keys)
            if found:
                return found
    elif isinstance(body, list):
        for item in body:
            found = _walk_numbers(item, keys)
            if found:
                return found
    return None


def _walk_strings(body: Any, keys: Tuple[str, ...]) -> Optional[str]:
    if isinstance(body, dict):
        for key, value in body.items():
            if _key_matches(str(key), keys) and isinstance(value, str) and value.strip():
                return value.strip()
        for value in body.values():
            found = _walk_strings(value, keys)
            if found:
                return found
    elif isinstance(body, list):
        for item in body:
            found = _walk_strings(item, keys)
            if found:
                return found
    return None


def model_ids(response_body: Any) -> List[str]:
    """OpenAI `/models` shape ({"data": [{"id": ...}]}) or a bare list of ids/names."""
    if not isinstance(response_body, dict):
        return []
    entries = response_body.get("data", response_body.get("models", []))
    out: List[str] = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            name = entry.get("id") or entry.get("name")
            if name:
                out.append(str(name))
    return out


def list_models(base_url: str) -> Tuple[bool, List[str]]:
    """(reachable, model ids) from the OpenAI-compatible endpoint."""
    try:
        response = _get(f"{base_url.rstrip('/')}/models")
    except Exception:
        return False, []
    if _status(response) != 200:
        return False, []
    return True, model_ids(_body(response))


def _ollama_native_tags(base_url: str) -> List[str]:
    try:
        response = _get(f"{root_of(base_url)}/api/tags")
    except Exception:
        return []
    body = _body(response) if _status(response) == 200 else None
    if not isinstance(body, dict):
        return []
    return [str(m.get("name")) for m in body.get("models", []) if m.get("name")]


def _ollama_loaded(base_url: str, model_name: str) -> Dict[str, Any]:
    """Ollama's /api/ps: the context it ACTUALLY allocated for the running model.

    /api/show reports the checkpoint's maximum; `OLLAMA_CONTEXT_LENGTH` / `num_ctx` / a 2048
    default can all make the running budget far smaller, and the smaller number is what
    truncates the observation slice.
    """
    try:
        response = _get(f"{root_of(base_url)}/api/ps")
    except Exception:
        return {}
    body = _body(response) if _status(response) == 200 else None
    for entry in (body or {}).get("models", []) if isinstance(body, dict) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("model") or "")
        if name == model_name or name.split(":")[0] == model_name.split(":")[0]:
            details = entry.get("details") or entry.get("detail") or {}
            return {
                "allocated_context_length": _walk_numbers(entry, ("context_length", "num_ctx")),
                "quantization": _walk_strings(details, ("quantization_level",)),
                "family": _walk_strings(details, ("family",)),
                "size_vram": entry.get("size_vram"),
            }
    return {}


def _text(response: Any) -> str:
    return str(getattr(response, "text", "") or "")


_flavor_cache: Dict[str, Tuple[float, str]] = {}


def _answers(url: str) -> Optional[Any]:
    """Body of a 200 answer, or None - a missing route must not abort the identification chain."""
    try:
        response = _get(url)
    except Exception:
        return None
    return _body(response) if _status(response) == 200 else None


def _props_body(root: str) -> Optional[Any]:
    """llama.cpp moved its properties to /admin/server/props and gates the old route behind --props."""
    return _answers(f"{root}/props") or _answers(f"{root}/admin/server/props")


def _root_banner(root: str) -> str:
    """The landing page often names the product when no API route does."""
    try:
        return _text(_get(root + "/")).lower()
    except Exception:
        return ""


def flavor_for(base_url: str) -> str:
    """Which server this actually is, from endpoint signatures rather than the port.

    Port alone lies: llama.cpp's historic default and mlx-lm's default are both 8080, and
    llama.cpp is moving its own default to 9931. /api/tags says Ollama, /props says llama.cpp
    (when the operator has not left it disabled), LM Studio's richer native REST lives under
    /api/v0/, Prometheus metric prefixes separate vLLM from SGLang, and the landing page is
    the last name-check.
    """
    key = base_url.rstrip("/")
    cached = _flavor_cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]
    root = root_of(base_url)
    flavor = "openai_compatible"
    if _answers(f"{root}/api/tags") is not None:
        flavor = "ollama"
    elif _props_body(root) is not None:
        flavor = "llamacpp"
    elif _answers(f"{root}/api/v0/models") is not None:
        flavor = "lmstudio"
    else:
        metrics = ""
        try:
            metrics = _text(_get(f"{root}/metrics"))
        except Exception:
            metrics = ""
        banner = _root_banner(root)
        if "vllm:" in metrics or "vllm" in banner:
            flavor = "vllm"
        elif "sglang:" in metrics or "sglang" in banner:
            flavor = "sglang"
        elif "llamacpp:" in metrics or "llama.cpp" in banner:
            # /metrics works even when the operator left /props disabled, which is the default.
            flavor = "llamacpp"
    _flavor_cache[key] = (time.time() + CACHE_TTL_OK_S, flavor)
    return flavor


def discover(candidates: Optional[Tuple[Tuple[str, str], ...]] = None) -> List[Dict[str, Any]]:
    """Every local server currently answering, with its model ids.

    `kind` is which candidate port answered; `flavor` is what the endpoint signatures say the
    server actually is - trust `flavor`, and expect them to disagree on 8080 (llama.cpp vs mlx-lm).
    """
    found: List[Dict[str, Any]] = []
    for kind, base_url in (candidates or LOCAL_CANDIDATES):
        reachable, ids = list_models(base_url)
        if not reachable and kind == "ollama":
            # Ollama's /v1/models needs the compat layer; the native API is always there.
            ids = _ollama_native_tags(base_url)
            reachable = bool(ids)
        if reachable:
            found.append({"kind": kind, "flavor": flavor_for(base_url),
                          "base_url": base_url, "models": ids})
    return found


_discovery: Optional[Tuple[float, List[Dict[str, Any]]]] = None
_discovery_guard = threading.Lock()


def discover_cached(ttl: float = CACHE_TTL_FAIL_S) -> List[Dict[str, Any]]:
    """Port scan is a handful of loopback probes - cache it so no turn pays for it twice.

    The ttl is deliberately the short one: a user starting Ollama after the brain should be
    picked up on the next turn, not after a brain restart.
    """
    global _discovery
    with _discovery_guard:
        if _discovery and _discovery[0] > time.time():
            return _discovery[1]
    found = discover()
    with _discovery_guard:
        _discovery = (time.time() + ttl, found)
    return found


def first_local_base_url(kind_hint: Optional[str] = None) -> Optional[str]:
    """The base_url of the first answering server, optionally preferring one kind."""
    found = discover_cached()
    for entry in found:
        if kind_hint is None or entry["kind"] == kind_hint:
            return str(entry["base_url"])
    return None


def local_model_names(base_url: Optional[str]) -> List[str]:
    for entry in discover_cached():
        if entry["base_url"] == base_url:
            return list(entry["models"])
    return []


def _structured_mode(base_url: str, model_id: str) -> str:
    """Behavioural probe: ask for constrained output and see whether the server accepts it.

    Response fields cannot answer this - vLLM, Ollama and llama.cpp all advertise `/models`
    identically but differ in whether they honour `response_format`.
    """
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    attempts = (
        ("json_schema", {"type": "json_schema", "json_schema":
                         {"name": "civagent_probe", "strict": True, "schema": schema}}),
        ("json", {"type": "json_object"}),
    )
    for mode, response_format in attempts:
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": "Reply with a JSON object."}],
            "max_tokens": 4,
            "response_format": response_format,
        }
        try:
            response = _post(f"{base_url.rstrip('/')}/chat/completions", payload)
        except Exception:
            return "unknown"  # server vanished mid-probe; don't blame the model
        code = _status(response)
        if code == 200:
            return mode
        # 4xx on the constraint itself means the server (not the model) rejects it.
        if 400 <= code < 500:
            continue
        return "unknown"
    return "none"


_cache: Dict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = {}
_cache_guard = threading.Lock()


def _cache_get(key: Tuple[str, str]) -> Optional[Dict[str, Any]]:
    with _cache_guard:
        entry = _cache.get(key)
        if not entry:
            return None
        expires, value = entry
        if expires < time.time():
            _cache.pop(key, None)
            return None
        return value


def _cache_put(key: Tuple[str, str], value: Dict[str, Any]) -> None:
    ttl = CACHE_TTL_OK_S if value.get("reachable") else CACHE_TTL_FAIL_S
    with _cache_guard:
        _cache[key] = (time.time() + ttl, value)


def clear_cache() -> None:
    """Tests / a user starting a server without restarting the brain."""
    with _cache_guard:
        _cache.clear()
    _flavor_cache.clear()
    global _discovery
    with _discovery_guard:
        _discovery = None


def profile_for(base_url: str, model_id: str) -> Dict[str, Any]:
    """{reachable, context_length, quantization, structured, models} for one server+model.

    Cached by ttl (long when reachable, short after a failure so `ollama serve` coming up late
    is picked up without restarting the brain).

    `context_length` is what the server will actually hold for this model; `context_capacity`
    is the checkpoint's maximum. They differ more often than users expect.
    """
    key = (base_url.rstrip("/"), model_id)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    profile: Dict[str, Any] = {
        "base_url": base_url, "model": model_id, "model_resolved": None, "reachable": False,
        "flavor": None, "context_length": None, "context_capacity": None, "context_note": None,
        "quantization": None, "structured": "unknown", "models": [], "reason": None,
    }
    if base_url and is_local_base_url(base_url) and model_id:
        body = _models_body(base_url)
        ids = model_ids(body)
        if not ids:
            # Ollama's OpenAI layer is not always enabled; the native tag list always is.
            ids = _ollama_native_tags(base_url)
        profile["models"] = ids
        profile["reachable"] = bool(ids)
        if ids:
            known = next((name for name in ids if name == model_id
                          or name.split(":")[0] == model_id.split(":")[0]), None)
            if known is None:
                profile["reachable"] = False
                profile["reason"] = f"model '{model_id}' is not loaded on {base_url}"
                profile["available"] = ids[:12]
            else:
                flavor = flavor_for(base_url)
                profile["flavor"] = flavor
                profile["model_resolved"] = known
                running = _ollama_loaded(base_url, known) if flavor == "ollama" else {}
                capacity_source: Any = None
                if flavor == "ollama":
                    try:
                        response = _post(f"{root_of(base_url)}/api/show",
                                         {"model": known, "verbosity": False})
                        capacity_source = _body(response) if _status(response) == 200 else None
                    except Exception:
                        capacity_source = None
                elif flavor == "llamacpp":
                    capacity_source = _props_body(root_of(base_url))
                elif flavor == "lmstudio":
                    # Its native REST reports per-model state, which the OpenAI layer does not.
                    v0 = _answers(f"{root_of(base_url)}/api/v0/models")
                    capacity_source = _entry_for(v0, known)
                if capacity_source is None:
                    # Generic OpenAI servers: whatever /models chose to publish. vLLM frequently
                    # omits max_model_len there, so this is best-effort and stays honest about it.
                    capacity_source = _entry_for(body, known)
                profile["context_capacity"] = _walk_numbers(capacity_source, _CONTEXT_KEYS)
                # The allocated budget is what truncates the slice, not the checkpoint's maximum:
                # OLLAMA_CONTEXT_LENGTH, a `num_ctx` default of 2048, or a shrunken -c all bite here.
                profile["context_length"] = (running.get("allocated_context_length")
                                             or profile["context_capacity"])
                profile["quantization"] = (running.get("quantization")
                                           or _walk_strings(capacity_source, _QUANT_KEYS))
                if not profile["context_length"]:
                    profile["context_note"] = (
                        f"{flavor} does not publish its context budget over HTTP - set it yourself "
                        "(--max-model-len / -c / OLLAMA_CONTEXT_LENGTH) and keep the slice under it"
                    )
                profile["structured"] = _structured_mode(base_url, known)

    _cache_put(key, profile)
    return profile


def _models_body(base_url: str) -> Optional[Any]:
    return _json_or_none(f"{base_url.rstrip('/')}/models")


def _json_or_none(url: str) -> Optional[Any]:
    try:
        response = _get(url)
    except Exception:
        return None
    return _body(response) if _status(response) == 200 else None


def _entry_for(body: Any, model_id: str) -> Optional[Dict[str, Any]]:
    """The matching entry of `/models`, so server-specific context fields stay readable."""
    entries = body.get("data", body.get("models", [])) if isinstance(body, dict) else []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and (entry.get("id") or entry.get("name")) == model_id:
            return entry
    return None


def server_problem(base_url: Optional[str], model_id: Optional[str]) -> Optional[str]:
    """Why a local provider cannot serve this model right now, or None when it can.

    Same contract as `llm_router.client_dependency_problem`: configuration may be perfect while
    the box is switched off, and `/healthz` must say so instead of reporting ok.
    """
    if not base_url or not is_local_base_url(base_url):
        return None
    if not model_id:
        return None
    profile = profile_for(base_url, model_id)
    if profile["reachable"]:
        return None
    return profile.get("reason") or (
        f"nothing answered at {base_url} - start the server "
        f"(ollama serve / LM Studio / python -m vllm.entrypoints.openai.api_server)"
    )


def strip_reasoning(text: str) -> str:
    """Drop chain-of-thought before parsing.

    Reasoning models emit their scratchpad in the same content field: `qwen3` wraps it in
    ``, DeepSeek-R1-style servers prefix it, and a response truncated by max_tokens
    leaves an unterminated opener whose body runs to the end of the reply.
    """
    if not text:
        return ""
    cleaned = _THINK_BLOCK.sub("", text)
    cleaned = _LABELLED_REASONING.sub("", cleaned)
    opener = _THINK_OPEN.search(cleaned)
    if opener:
        # Unterminated: everything from the tag on is scratchpad, keep what came before it.
        cleaned = cleaned[: opener.start()]
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
