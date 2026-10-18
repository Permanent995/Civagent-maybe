"""Trust sidecar for intel (决策 17.4). Never writes Unciv opinion / diplomaticModifiers.

Key: (gameId, selfCiv, otherCiv) -> float in [0, 1].
Stored as deployment/data/{gameId}/trust.json
Claims: deployment/data/{gameId}/claims.jsonl
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

TRUST_HIGH = 0.65
TRUST_DEFAULT = 0.40
# W5 收尾: claim verification. A claim is verified when the engine state matches it,
# falsified when the window closes without matching. Trust pair is always
# (receiver, claimant): verified raises MY trust in the CLAIMANT, falsified lowers it.
VERIFY_BONUS = 0.05
FALSIFY_PENALTY = 0.05
CLAIM_WINDOW_TURNS = 10

# Only kinds observable through the ObservationSlice can be window-falsified:
# war/peace from `knownCivs[].atWar`; massing_troops from `borderThreats`.
# Unobservable kinds (e.g. nuke research) stay open - never falsify what we cannot see.
OBSERVABLE_CLAIM_KINDS = ("war", "peace", "massing_troops")

_BORDER_LEVELS = ("low", "medium", "high")

# Free-text claims (players type prose in 情报交换) get classified here. First match wins.
_CLAIM_KIND_PATTERNS = [
    ("nuke_research", re.compile(r"核弹|核武|原子弹|nuclear|nuke|atom(?:ic)?\s*bomb", re.I)),
    ("massing_troops", re.compile(r"囤兵|集结|调兵|驻军|军队|部队|troops?|massing|amass|gathering|arm(?:y|ies)|border|边境|边界", re.I)),
    ("war", re.compile(r"宣战|开战|进攻|入侵|will attack|declare war|invad", re.I)),
    ("peace", re.compile(r"议和|和平|停战|求和|peace|truce", re.I)),
]


def classify_claim_kind(text: str, explicit: str = "") -> str:
    """Structured kind for the W5.7 verification hook; falls back to keyword classification."""
    kind = (explicit or "").strip().lower()
    if kind and kind != "other":
        return kind
    for name, pattern in _CLAIM_KIND_PATTERNS:
        if pattern.search(text or ""):
            return name
    return "other"


def _safe(part: str) -> str:
    return "".join(c for c in part if c.isalnum() or c in "-_") or "unknown"


def trust_path(memory_root: str, game_id: str) -> str:
    return os.path.join(memory_root, _safe(game_id), "trust.json")


def claims_path(memory_root: str, game_id: str) -> str:
    return os.path.join(memory_root, _safe(game_id), "claims.jsonl")


def _pair_key(self_civ: str, other_civ: str) -> str:
    return f"{self_civ.lower()}|{other_civ.lower()}"


def load_trust_table(memory_root: str, game_id: str) -> Dict[str, float]:
    path = trust_path(memory_root, game_id)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file) or {}
        return {str(k): float(v) for k, v in data.items()}
    except Exception:
        return {}


def save_trust_table(memory_root: str, game_id: str, table: Dict[str, float]) -> None:
    path = trust_path(memory_root, game_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(table, file, ensure_ascii=False, indent=2)


def get_trust(memory_root: str, game_id: str, self_civ: str, other_civ: str) -> float:
    table = load_trust_table(memory_root, game_id)
    return float(table.get(_pair_key(self_civ, other_civ), TRUST_DEFAULT))


def set_trust(memory_root: str, game_id: str, self_civ: str, other_civ: str, value: float) -> float:
    table = load_trust_table(memory_root, game_id)
    clamped = max(0.0, min(1.0, float(value)))
    table[_pair_key(self_civ, other_civ)] = clamped
    save_trust_table(memory_root, game_id, table)
    return clamped


def trust_log_path(memory_root: str, game_id: str) -> str:
    return os.path.join(memory_root, _safe(game_id), "trust_log.jsonl")


def bump_trust(
    memory_root: str,
    game_id: str,
    self_civ: str,
    other_civ: str,
    delta: float,
    reason: str = "",
    evidence: str = "",
) -> float:
    """Every trust change is logged with its reason and (when available) the engine
    event that justifies it - trust becomes traceable instead of a black box number."""
    new_value = set_trust(memory_root, game_id, self_civ, other_civ, get_trust(memory_root, game_id, self_civ, other_civ) + delta)
    path = trust_log_path(memory_root, game_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "self": self_civ,
        "other": other_civ,
        "delta": round(float(delta), 4),
        "trust": new_value,
        "reason": reason,
        "evidence": evidence[:300],
    }
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    return new_value


def append_claim(
    memory_root: str,
    game_id: str,
    *,
    from_civ: str,
    to_civ: str,
    claim: str,
    source: str = "share_intel",
    kind: str = "other",
    about_civ: str = "",
    turn: int = 0,
) -> None:
    """kind: 'war' | 'peace' | 'other'. Only war/peace claims get auto verification."""
    path = claims_path(memory_root, game_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "from": from_civ,
        "to": to_civ,
        "claim": claim,
        "source": source,
        "kind": kind,
        "aboutCiv": about_civ,
        "turn": int(turn),
    }
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_claims(memory_root: str, game_id: str) -> List[Dict[str, Any]]:
    path = claims_path(memory_root, game_id)
    if not os.path.isfile(path):
        return []
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _claim_ref(entry: Dict[str, Any]) -> str:
    return f"{entry.get('time')}|{entry.get('from')}|{entry.get('to')}|{entry.get('claim')}"


def verify_pending_claims(
    memory_root: str,
    game_id: str,
    self_civ: str,
    slice_data: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Notification hook (W5.7): check open claims against engine truth.

    The ObservationSlice IS the notification source - `atWar` per known civ and
    `borderThreats` (visible enemy military near our cities) are exactly what the game
    would notify about. Verified/falsified verdicts bump trust between (receiver=me,
    claimant) and are appended to claims.jsonl so each claim resolves once.
    Only [OBSERVABLE_CLAIM_KINDS] are scored; anything else stays open.
    """
    entries = _read_claims(memory_root, game_id)
    resolved_refs = {
        _claim_ref(entry["resolution"])
        for entry in entries
        if entry.get("kind") == "resolution" and isinstance(entry.get("resolution"), dict)
    }
    war_state = {
        str(k["civID"]).lower(): bool(k.get("atWar", False))
        for k in slice_data.get("knownCivs", [])
    }
    border_level = {
        str(b.get("civID", "")).lower(): str(b.get("level", "none"))
        for b in slice_data.get("borderThreats", [])
    }
    turn = int(slice_data.get("turn", 0))
    verdicts: List[Dict[str, Any]] = []
    for entry in entries:
        kind = str(entry.get("kind", "other"))
        if kind not in OBSERVABLE_CLAIM_KINDS:
            continue
        ref = _claim_ref(entry)
        if ref in resolved_refs:
            continue
        # only claims directed AT me (I trusted the claimant) get scored by MY observation
        if str(entry.get("to", "")).lower() != self_civ.lower():
            continue
        about = str(entry.get("aboutCiv", "")).lower()
        if not about or about not in war_state:
            continue  # not observable yet - stays open
        claim_turn = int(entry.get("turn", 0))
        if kind == "war":
            verified = war_state[about]
        elif kind == "peace":
            verified = not war_state[about]
        else:  # massing_troops - visible enemy military near our cities
            verified = border_level.get(about, "none") in _BORDER_LEVELS
        expired = claim_turn > 0 and turn - claim_turn > CLAIM_WINDOW_TURNS
        if verified:
            delta = VERIFY_BONUS
            verdict = "verified"
        elif expired:
            delta = -FALSIFY_PENALTY
            verdict = "falsified"
        else:
            continue  # still open, window not closed
        claimant = str(entry.get("from", ""))
        new_trust = bump_trust(
            memory_root, game_id, self_civ, claimant, delta,
            reason=f"claim {verdict}",
            evidence=f"claim @{entry.get('time')}: {entry.get('claim')} (about {about}, turn {claim_turn})",
        )
        record = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "kind": "resolution",
            "resolution": entry,
            "verdict": verdict,
            "aboutCiv": about,
            "trustDelta": delta,
            "trust": new_trust,
            # W5.7: a falsified claim is the only moment denouncing "欺瞒" is legitimate.
            "denounceSuggested": verdict == "falsified",
        }
        path = claims_path(memory_root, game_id)
        with open(path, "a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
        verdicts.append(record)
    return verdicts


def recent_claims(memory_root: str, game_id: str, civ_id: str, limit: int = 5) -> List[str]:
    path = claims_path(memory_root, game_id)
    if not os.path.isfile(path):
        return []
    lines: List[str] = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if str(entry.get("to", "")).lower() != civ_id.lower() and str(entry.get("from", "")).lower() != civ_id.lower():
                continue
            lines.append(f"{entry.get('from')}->{entry.get('to')}: {entry.get('claim')}")
    return lines[-limit:]


def third_party_intel_allowed(trust: float, share_intel_active: bool) -> bool:
    return share_intel_active or trust >= TRUST_HIGH


def third_party_rule_text(trust: float, share_intel_active: bool) -> str:
    if third_party_intel_allowed(trust, share_intel_active):
        return (
            f"THIRD-PARTY INTEL: allowed this turn (trust={trust:.2f}"
            f"{', share_intel exchange' if share_intel_active else ''}). "
            "You may share ONLY what YOUR civ has itself observed about third parties. "
            "Never invent fog-of-war facts or open unexplored tiles."
        )
    return (
        f"THIRD-PARTY INTEL BAN: trust with interlocutor is {trust:.2f} (< {TRUST_HIGH}). "
        "Do NOT reveal cities, armies, techs, or locations of any third civilization. "
        "Refuse politely in character. You may discuss YOUR own lands and open diplomacy with the interlocutor."
    )
