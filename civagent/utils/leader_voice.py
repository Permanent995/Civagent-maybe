"""Per-civ speaking style (Canon) + era anachronism guards for chat/decision lines.

Voice notes lock Chinese (or other) stylistic differences per nation. Era rules keep
classical leaders from threatening with nukes / tanks / the internet.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Vanilla Unciv era index (Ruleset assigns by Eras.json order).
ERA_ANCIENT = 0
ERA_CLASSICAL = 1
ERA_MEDIEVAL = 2
ERA_RENAISSANCE = 3
ERA_INDUSTRIAL = 4
ERA_MODERN = 5
ERA_ATOMIC = 6
ERA_INFORMATION = 7
ERA_FUTURE = 8

# Mods can reorder / add / remove eras; Ruleset.eraNumber is just the index in Eras.json.
# Normalize to the vanilla 0..8 tier so anachronism guards stay meaningful on mods.
_ERA_TIER_KEYWORDS: List[Tuple[str, int]] = [
    ("neolithic", ERA_ANCIENT),
    ("ancient", ERA_ANCIENT),
    ("classical", ERA_CLASSICAL),
    ("medieval", ERA_MEDIEVAL),
    ("middle ages", ERA_MEDIEVAL),
    ("renaissance", ERA_RENAISSANCE),
    ("industrial", ERA_INDUSTRIAL),
    ("modern", ERA_MODERN),
    ("atomic", ERA_ATOMIC),
    ("information", ERA_INFORMATION),
    ("future", ERA_FUTURE),
]


def normalize_era_tier(era: str, era_number: int, era_count: int = 0) -> int:
    """Map a (possibly modded) era onto the vanilla 0..8 tier.

    1. A recognized era name wins (e.g. "Neolithic era" -> ancient tier).
    2. Otherwise scale the ruleset index by the ruleset's era count.
    3. Otherwise fall back to the raw index (legacy behavior).
    """
    name = str(era or "").strip().lower()
    for keyword, tier in _ERA_TIER_KEYWORDS:
        if keyword in name:
            return tier
    try:
        index = int(era_number)
    except (TypeError, ValueError):
        index = 0
    try:
        count = int(era_count)
    except (TypeError, ValueError):
        count = 0
    if count >= 2:
        scaled = round(index * ERA_FUTURE / (count - 1))
        return max(0, min(ERA_FUTURE, scaled))
    return index


# Each entry: (min_era_number_required, pattern). Matched case-insensitively.
# If the seat's eraNumber is strictly below min_era, the term is banned.
_ANACHRONISM_TERMS: List[Tuple[int, re.Pattern]] = [
    (ERA_ATOMIC, re.compile(
        r"nuke|nuclear|atom(?:ic)?\s*bomb|icbm|warhead|"
        r"核弹|核武|原子弹|氢弹|导弹(?!车)|洲际导弹",
        re.IGNORECASE,
    )),
    (ERA_MODERN, re.compile(
        r"\btank\b|fighter\s*jet|bomber|aircraft\s*carrier|radar|submarine|"
        r"坦克|战斗机|轰炸机|航母|航空母舰|雷达|潜艇",
        re.IGNORECASE,
    )),
    (ERA_INDUSTRIAL, re.compile(
        r"machine\s*gun|artillery|railroad|locomotive|steam\s*engine|"
        r"机枪|火炮|铁路|蒸汽机|火车",
        re.IGNORECASE,
    )),
    (ERA_INFORMATION, re.compile(
        r"internet|smartphone|drone|satellite|cyber|\bAI\b|chatgpt|"
        r"互联网|智能手机|无人机|卫星|网络攻击|人工智能",
        re.IGNORECASE,
    )),
]

_DEFAULT_VOICE = (
    "Speak as a pragmatic sovereign: concise, formal, first-person plural when fitting. "
    "No modern slang, no assistant disclaimers."
)

# Canon voice cards keyed by civ id (lower). Override via config seat `voice` if needed.
VOICE_BY_CIV = {
    "china": (
        "中国帝王口吻：文言白话夹杂、克制、先礼后兵；少用网络梗与现代口语。"
        "威胁用征伐、城破、岁贡，不用现代武器名。"
    ),
    "rome": (
        "Roman magistrate tone: law, treaty, duty; short declarative sentences. "
        "Threats are legions and conquest, never modern weapons."
    ),
    "mongolia": (
        "直白、赏罚分明；可强硬但不油滑。威胁用铁骑、屠城、臣服，不用现代武器。"
    ),
    "egypt": (
        "Regal, ritual cadence; invoke Nile, gods, and permanence. "
        "Threats are armies and curses of the age, never modern weapons."
    ),
    "greece": (
        "Civic and rhetorical: debate, honor, polis. Threats are hoplites and fleets of the age."
    ),
    "aztecs": (
        "Fierce, ritual, honor-bound. Threats are sacrifice and conquest of this age only."
    ),
}


def seat_voice(civ_id: str, config_override: Optional[str] = None) -> str:
    if config_override:
        return str(config_override)
    key = str(civ_id or "").strip().lower()
    base = VOICE_BY_CIV.get(key) or generic_voice_for(civ_id)
    return base + _ANTI_MIMICRY


# Unciv ships thousands of nations across mods, so a hardcoded table can never cover them.
# The generic card is derived from the nation's own name plus a translation-flavour rule.
_ANTI_MIMICRY = (
    " Keep YOUR OWN national voice: never adopt the interlocutor's register, catchphrases or "
    "court style just because they used them. Classical/literary Chinese court rhetoric is for "
    "Chinese-court nations only - everyone else answers in their own national flavour."
)


def generic_voice_for(civ_id: str) -> str:
    """Per-nation voice card that works for any ruleset, including total-conversion mods."""
    name = str(civ_id or "").strip() or "your nation"
    return (
        f"You are the ruler of {name}. Speak as a {name} leader of your own age would - "
        f"rendered naturally in the required language with a {name} translation flavour "
        f"(address forms, idiom, imagery and cadence typical of {name}), not a generic court voice."
    )


def era_lock_instructions(era: str, era_number: int) -> str:
    era_label = era or "unknown era"
    return (
        f"ERA LOCK: you are in the {era_label} (eraNumber={int(era_number)}). "
        "Speak and threaten ONLY with means plausible in this era. "
        "Forbidden when too early: nuclear weapons, tanks, aircraft, internet, smartphones, AI. "
        "If the player mentions future tech, deflect in-character without confirming it exists."
    )


def banned_hits(text: str, era_number: int) -> List[str]:
    hits: List[str] = []
    for min_era, pattern in _ANACHRONISM_TERMS:
        if int(era_number) >= min_era:
            continue
        for match in pattern.finditer(text):
            token = match.group(0)
            if token and token not in hits:
                hits.append(token)
    return hits


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?])\s+")


def scrub_anachronisms(text: str, era_number: int, language: str = "English", era: str = "", era_count: int = 0) -> str:
    """Drop sentences that contain era-illegal terms; fall back to a safe one-liner.

    era/era_count are optional: when given, the modded era index is normalized first.
    """
    tier = normalize_era_tier(era, era_number, era_count)
    if not text or not banned_hits(text, tier):
        return text
    kept: List[str] = []
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        piece = sentence.strip()
        if piece and not banned_hits(piece, tier):
            kept.append(piece)
    if kept:
        return " ".join(kept)
    lang = str(language).lower()
    if lang.startswith("zh") or "chinese" in lang or "中文" in language:
        return "时运未至，此事非今日所能妄言。且以当世之力论之。"
    return "Such weapons are beyond our age. Let us speak of what our armies can still do."
