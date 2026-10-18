"""jevos decision adapter (TypeSafe Jev wire format, MIT licensed).

jevos (https://github.com/feder-cr/jev) is a CPU-only decision model: send a text plus
yes/no, multiple-choice or score questions and get probabilities back in ~30-130 ms. It
cannot write an intent, so it does not replace the LLM - it gates what the LLM proposed.

Wire format (POST {url}/v1/systemone, default http://127.0.0.1:8017):
    {"model": "jev-latest", "state": "<text>",
     "questions": {"q0": {"type": "noul", "instructions": "Should ...?"}}}
    -> {"answers": {"q0": {"type": "noul", "noul": 0.97}}, "usage": {...}}

Everything fails open: jev down, slow, or misconfigured means the seat plays exactly as it
did before this file existed. Nothing here downloads a model.
"""

import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from civagent.config import config_data

DEFAULT_URL = "http://127.0.0.1:8017"
DEFAULT_THRESHOLD = 0.5
DEFAULT_TIMEOUT_S = 2.0
MAX_QUESTIONS = 6
HEALTH_TTL_S = 30.0

_health_lock = threading.Lock()
_health_cache: Dict[str, Any] = {"until": 0.0, "ok": False, "info": None}


class JevUnavailable(RuntimeError):
    pass


def _entry(key: str, default: str = "") -> str:
    env = os.environ.get(key)
    if env:
        return env
    return str(config_data.get("LLM", {}).get(key) or default)


def _flag(value: Any) -> bool:
    return str(value).strip().lower() not in ("", "0", "false", "no", "off")


def enabled() -> bool:
    return _flag(_entry("CIVAGENT_JEV_GATE") or _entry("jev_gate"))


def url() -> str:
    return (_entry("CIVAGENT_JEV_URL") or _entry("jev_url") or DEFAULT_URL).rstrip("/")


def api_key() -> str:
    return _entry("CIVAGENT_JEV_KEY") or _entry("jev_key") or os.environ.get("JEV_API_KEY", "")


def threshold() -> float:
    raw = _entry("CIVAGENT_JEV_THRESHOLD") or _entry("jev_threshold")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD
    return min(1.0, max(0.0, value))


def timeout() -> float:
    raw = _entry("CIVAGENT_JEV_TIMEOUT") or _entry("jev_timeout")
    try:
        return max(0.05, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _headers() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _get(path: str, request_timeout: float):
    """Seam for tests: production uses httpx."""
    import httpx

    return httpx.get(url() + path, headers=_headers(), timeout=request_timeout)


def _post(path: str, payload: Dict[str, Any], request_timeout: float):
    """Seam for tests: production uses httpx."""
    import httpx

    return httpx.post(url() + path, headers=_headers(), json=payload, timeout=request_timeout)


def health(force: bool = False) -> Dict[str, Any]:
    """GET /health (readiness + model hashes) - costs no inference, cached for 30 s."""
    now = time.time()
    with _health_lock:
        if not force and now < _health_cache["until"]:
            return {"ok": _health_cache["ok"], "info": _health_cache["info"]}
    ok, info = False, None
    try:
        response = _get("/health", min(timeout(), 1.0))
        ok = int(getattr(response, "status_code", 0) or 0) == 200
        if ok:
            try:
                info = response.json()
            except Exception:
                info = None
    except Exception:
        ok = False
    with _health_lock:
        _health_cache.update(until=now + HEALTH_TTL_S, ok=ok, info=info)
    return {"ok": ok, "info": info}


def ask(state: Any, questions: Dict[str, Any], request_timeout: Optional[float] = None) -> Dict[str, Any]:
    """POST /v1/systemone and return the `answers` map."""
    payload = {"model": "jev-latest", "state": state, "questions": questions}
    response = _post("/v1/systemone", payload, request_timeout or timeout())
    status = int(getattr(response, "status_code", 0) or 0)
    if status != 200:
        raise JevUnavailable(f"HTTP {status}")
    body = response.json()
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        raise JevUnavailable("response has no answers map")
    return answers


def _probability(answer: Any) -> Optional[float]:
    if not isinstance(answer, dict):
        return None
    for key in ("noul", "probability", "p", "confidence"):
        value = answer.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def state_from_slice(slice_data: Dict[str, Any]) -> str:
    """A compact, fact-only description for jevos (its context is 8192 tokens, and latency
    grows with the text, so this stays under a few hundred words)."""
    def number(key: str, digits: int = 0) -> str:
        value = slice_data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{value:.{digits}f}"
        return "?"

    cities = slice_data.get("cities") or []
    city_bits = []
    for city in cities[:8] if isinstance(cities, list) else []:
        if not isinstance(city, dict):
            continue
        construction = city.get("currentConstruction") or "nothing"
        turns = city.get("constructionTurnsLeft")
        city_bits.append(
            f"{city.get('name', '?')} (pop {city.get('population', '?')}, building {construction}"
            + (f", {turns} turns left" if isinstance(turns, int) else "") + ")"
        )
    known = []
    at_war = []
    for civ in slice_data.get("knownCivs") or []:
        if not isinstance(civ, dict):
            continue
        known.append(f"{civ.get('civID', '?')} ({civ.get('relationship', 'Unknown')})")
        if civ.get("atWar"):
            at_war.append(str(civ.get("civID", "?")))
    balance = slice_data.get("factionBalance")
    parts = [
        f"Turn {slice_data.get('turn', '?')}. Civilization: {slice_data.get('civID', '?')}. "
        f"Era: {slice_data.get('era', '?')} (#{slice_data.get('eraNumber', '?')}).",
        f"Gold {number('gold')} ({number('goldPerTurn', 1)} per turn), "
        f"science {number('sciencePerTurn', 1)} per turn, "
        f"culture {number('culturePerTurn', 1)} per turn, happiness {number('happiness')}.",
        f"Cities: {'; '.join(city_bits) or 'none'}.",
        f"At war with: {', '.join(at_war) or 'nobody'}. "
        f"Known civilizations: {', '.join(known) or 'nobody'}.",
    ]
    if balance is not None:
        parts.append(f"Faction balance (war/peace scale, -100..100): {balance}.")
    return " ".join(parts)


def _intent_question(intent: Any) -> str:
    kind = str(getattr(intent, "type", "") or "unknown")
    target = getattr(intent, "targetCivID", None)
    return ("Should this civilization carry out the diplomatic action "
            f"'{kind}'" + (f" toward '{target}'" if target else "") +
            " right now, given the situation?")


def gate_intents(
    state: str, intents: List[Any], limit: int = MAX_QUESTIONS
) -> Tuple[List[Any], Dict[str, Any]]:
    """Keep only the intents jevos believes are worth doing now.

    One request carries all questions, so the state text is read once. On any failure the
    original list is returned untouched (fail-open) with the reason in the report.
    """
    report: Dict[str, Any] = {"enabled": True, "threshold": threshold()}
    if not intents:
        report["skipped"] = "no intents"
        return list(intents), report
    status = health()
    if not status["ok"]:
        report["skipped"] = "jev not reachable at " + url()
        return list(intents), report

    head = list(intents[:limit])
    questions = {f"i{index}": {"type": "noul", "instructions": _intent_question(intent)}
                 for index, intent in enumerate(head)}
    try:
        answers = ask(state, questions)
    except Exception as exc:
        report["skipped"] = f"{type(exc).__name__}: {exc}"
        return list(intents), report

    probabilities: Dict[str, float] = {}
    kept: List[Any] = []
    head_kept = 0
    for index, intent in enumerate(head):
        probability = _probability(answers.get(f"i{index}"))
        if probability is None:
            kept.append(intent)  # no verdict is not a rejection
            head_kept += 1
            continue
        key = f"{getattr(intent, 'type', '?')}->{getattr(intent, 'targetCivID', None) or '-'}"
        probabilities[key] = round(probability, 4)
        if probability >= threshold():
            kept.append(intent)
            head_kept += 1
    kept.extend(intents[limit:])  # beyond the question cap: untouched, fail-open
    report.update({
        "kept": len(kept),
        "asked": len(questions),
        "dropped": len(head) - head_kept,
        "probabilities": probabilities,
    })
    return kept, report
