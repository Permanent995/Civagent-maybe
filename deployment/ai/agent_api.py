"""AgentAPI - the decision brain + chat brain (EXECUTION.md「冻结契约」/ W3).

Standalone Flask app: no Redis, no JVM, no chat providers. Endpoints:
  GET  /healthz      {"llm":"ok","provider":…} or 503 with the missing slot named
  GET|POST /v1/config  localhost-only secrets sidecar (~/.civagent/secrets.json)
  POST /v1/decision  body = ObservationSlice (docs/protocol/observation_slice.schema.json)
                     reply = {"intents":[Intent...]} (docs/protocol/intent.schema.json)
  POST /v1/applied   { gameId, civID, applied[] } - logged to the seat's memory file
  POST /message      { gameId, fromCiv, toCiv, text, language } - 200 = 已开始 (async)
  GET  /events       SSE; events carry status=stable and any ```intent``` drafts
  GET  /messages     transcript JSON for one civ pair
  GET  /status       process/LLM summary

W3 semantics: chat replies are immediate, but intents inside ```intent fences are DRAFTS -
they are returned to the game, shown to the player, and only executed on the agent seat's
next turn (G4). Decision and chat share one lock per seat. Language is locked to the game
language supplied by the client. All LLM calls go through civagent.utils.llm_router.
Memory is jsonl files under deployment/data/{gameId}/ - no SQLite, no vector store.
"""

import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, Response, request
from werkzeug.exceptions import HTTPException

try:  # gevent is optional; fall back to the werkzeug dev server
    from gevent.pywsgi import WSGIServer
except ImportError:  # pragma: no cover
    WSGIServer = None

from civagent import config as civagent_config
from civagent.config import config_data
from civagent.utils import llm_router
from civagent.utils import secrets_store
from civagent.utils.intent_models import INTENT_TYPES, ChatOutput, DecisionOutput, Intent, sanitize_intents
from civagent.utils.leader_voice import era_lock_instructions, scrub_anachronisms, seat_voice
from civagent.utils import jev_decider, local_models, trust_store

app = Flask(__name__)

MEMORY_ROOT = os.environ.get("CIVAGENT_MEMORY_ROOT", "deployment/data")

REQUIRED_SLICE_FIELDS = (
    "gameId", "civID", "turn", "gold", "goldPerTurn", "happiness", "resources", "knownCivs",
)

SYSTEM_PROMPT = (
    "You are the ruler of a civilization in a Civilization-like strategy game (Unciv). "
    "You control exactly one civ: {civ_id}. Personality: {personality}.\n"
    "VOICE: {voice}\n"
    "{era_lock}\n"
    "{third_party_rule}\n"
    "You are a player, not an assistant: protect your own interests, keep promises when it "
    "suits you, and never reveal information your civ has not observed. "
    "Border pressure and navyShipCount are engine summaries - you do NOT receive unit coordinates. "
    "Military movement is handled by native AI after you declare war. "
    "LANGUAGE: the player's game language is {language} - any chatText you emit MUST be "
    "written in {language}, regardless of your nation's flavor voice. "
    "Given the observation slice, decide what your civ intends this turn. "
    "Valid intent types: {intent_types}. Only intents for your own civ are legal; "
    "'chat' carries a diplomatic line in chatText; share_intel requires intel.claim text. "
    "VALUE SCALE (rough anchor, not exact law): 1 production ~ 2.5 gold of buy value; "
    "1 science ~ 1 gold long-term; 1 food feeds growth and is worth more than its gold "
    "price when expanding; happiness is a hard cap - a deal that drops you below 0 "
    "happiness is usually bad. Your cities carry ENGINE numbers: constructionBuyCost is "
    "the exact gold price to buy the current build NOW, constructionTurnsLeft is the "
    "exact wait - prefer these over any conversion guess. "
    "ACTIONS OVER PROSE: when you discuss an exchange of goods, actually emit a "
    "propose_trade/buy_luxury intent in the same batch - a chat line alone can never "
    "deliver a trade. Keep chatText under 60 words; the player sees it in a confirm popup. "
    "Respond only with the JSON schema given."
)

CHAT_SYSTEM_PROMPT = (
    "You are the ruler of a civilization in a Civilization-like strategy game (Unciv), "
    "chatting with another leader. You control exactly one civ: {civ_id}. "
    "Personality: {personality}.\n"
    "VOICE: {voice}\n"
    "{era_lock}\n"
    "{third_party_rule}\n"
    "You are a player, not an assistant: negotiate, haggle, "
    "bluff, refuse - as a ruler would. Never reveal what your civ has not observed.\n"
    "LANGUAGE RULE: reply ONLY in {language} - no other language, ever.\n"
    "If the conversation implies an action (declare war, make peace, propose a trade, "
    "open borders, share intel, ...), append ONE fenced block:\n"
    "```intent\n{{\"type\": \"...\", \"actorCivID\": \"{civ_id}\", \"targetCivID\": \"...\", ...}}\n```\n"
    "For share_intel the fence MUST include \"intel\": {{\"claim\": \"...\"}} with a text claim first.\n"
    "The fenced intent is only a DRAFT: the game applies it on your next turn, never while "
    "you are still talking. Valid types: {intent_types}."
)

# one lock per (gameId, civId) shared by decision + chat (W3.6)
_seat_locks: Dict[Tuple[str, str], threading.Lock] = {}
_seat_locks_guard = threading.Lock()

# Latest observation slice per (gameId, civId). The chat request carries no slice, so without this
# the model had no idea what it owns and could only say "trade ivory" without amounts.
_last_slices: Dict[Tuple[str, str], Dict[str, Any]] = {}
_last_slices_guard = threading.Lock()

# Last assembled prompt per (gameId, civId) - powers the in-game prompt viewer (GET /v1/prompt).
_last_prompts: Dict[Tuple[str, str], Dict[str, Any]] = {}
_last_prompts_guard = threading.Lock()

# Latest jevos gate verdict, for /status. Written by the decision thread, read by HTTP.
_last_jev_report: Optional[Dict[str, Any]] = None


def _remember_prompt(game_id: str, civ_id: str, kind: str, messages: List[Dict[str, str]]) -> None:
    system = next((str(m.get("content") or "") for m in messages if m.get("role") == "system"), "")
    user = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")
    try:
        provider, model_id = llm_router.model_id_for(civ_id)
    except Exception:
        provider, model_id = "?", "?"
    record = {
        "kind": kind,
        "time": datetime.now().isoformat(timespec="seconds"),
        "gameId": game_id,
        "civID": civ_id,
        "provider": provider,
        "model": model_id,
        "sampling": {str(k): str(v) for k, v in llm_router.sampling_params().items()},
        "streaming": llm_router.streaming_enabled(),
        "systemTokens": llm_router.estimate_tokens(system),
        "userTokens": llm_router.estimate_tokens(user),
        "promptTokens": llm_router.estimate_tokens(system) + llm_router.estimate_tokens(user),
        "system": system,
        "user": user,
    }
    with _last_prompts_guard:
        _last_prompts[(str(game_id), str(civ_id).lower())] = record


def _remember_slice(slice_data: Dict[str, Any]) -> None:
    game_id = str(slice_data.get("gameId") or "")
    civ_id = str(slice_data.get("civID") or "")
    if not game_id or not civ_id:
        return
    with _last_slices_guard:
        _last_slices[(game_id, civ_id.lower())] = slice_data


def _slice_for(game_id: str, civ_id: str) -> Optional[Dict[str, Any]]:
    with _last_slices_guard:
        return _last_slices.get((str(game_id), str(civ_id).lower()))


def _trade_state_line(
    game_id: str, civ_id: str, partner: str = "", slice_data: Optional[Dict[str, Any]] = None
) -> str:
    """The engine's own trade/deal state: standing deals, relation statistics, open offers.

    Trade *notifications* are unavailable for agent seats - Unciv's Civilization.addNotification
    returns early for PlayerType.AI - so this reads the live structures instead.
    """
    if slice_data is None:
        slice_data = _slice_for(game_id, civ_id)
    if not slice_data:
        return "TRADE/DEAL STATE: not observed yet."
    lines: List[str] = []
    for known in slice_data.get("knownCivs", []):
        name = str(known.get("civID") or "")
        if partner and name.lower() != str(partner).lower():
            continue
        bits: List[str] = []
        if known.get("hasOpenBorders"):
            bits.append("open borders")
        for deal in known.get("agreements") or []:
            bits.append(f"signed deal: {deal}")
        for modifier in known.get("modifiers") or []:
            bits.append(str(modifier))
        if bits:
            lines.append(f"{name}: " + "; ".join(bits))
    incoming = slice_data.get("incomingTradeRequests") or []
    if incoming:
        lines.append("offers awaiting our answer: " + " | ".join(str(i) for i in incoming))
    return "TRADE/DEAL STATE (engine truth): " + ("; ".join(lines) if lines else "no standing deals")


def _agent_actions_line(
    game_id: str, civ_id: str, slice_data: Optional[Dict[str, Any]] = None
) -> str:
    """What this agent did recently. Unciv keeps no notifications for AI seats, so the game
    records CivAgent's own applied intents (failures included) into the slice."""
    if slice_data is None:
        slice_data = _slice_for(game_id, civ_id)
    actions = (slice_data or {}).get("recentAgentActions") or []
    if not actions:
        return "YOUR RECENT ACTIONS: none recorded yet."
    return (
        "YOUR RECENT ACTIONS (engine-reported; do not contradict or repeat them): "
        + "; ".join(str(action) for action in actions)
    )


def _economy_line(game_id: str, civ_id: str, partner: str = "") -> str:
    """What this seat actually owns, so trades can be quantified instead of vague."""
    slice_data = _slice_for(game_id, civ_id)
    if not slice_data:
        return (
            "YOUR ECONOMY: not observed yet - do NOT invent amounts; ask for the figures first."
        )
    kinds = slice_data.get("resourceKinds") or {}
    resources = slice_data.get("resources") or {}
    rendered = ", ".join(
        f"{name}={amount} ({kinds.get(name, 'luxury')})" for name, amount in sorted(resources.items())
    ) or "none"
    parts = [
        f"gold={slice_data.get('gold')} (+{slice_data.get('goldPerTurn')}/turn)",
        f"happiness={slice_data.get('happiness')}",
        f"resources: {rendered}",
    ]
    if partner:
        known = next(
            (
                k
                for k in slice_data.get("knownCivs", [])
                if str(k.get("civID", "")).lower() == str(partner).lower()
            ),
            None,
        )
        if known is not None and known.get("gold") is not None:
            parts.append(f"{partner} treasury (visible)={known.get('gold')}")
        partner_resources = (known or {}).get("resources") or {}
        if partner_resources:
            parts.append(
                f"{partner} resources (visible): "
                + ", ".join(f"{name}={amount}" for name, amount in sorted(partner_resources.items()))
            )
    return "YOUR ECONOMY (you may only offer what is listed here): " + "; ".join(parts) + "."


def _trading_rules(civ_id: str, target: str) -> str:
    return (
        "TRADING RULES: always state concrete numbers (gold amounts, resource counts) - never "
        "'some', 'a few' or 'plenty'. Only offer what YOUR ECONOMY lists. A peace deal must say "
        "what each side gives. For a trade intent use exactly:\n"
        "```intent\n"
        '{"type": "propose_trade", "actorCivID": "' + str(civ_id) + '", "targetCivID": "' + str(target) + '", '
        '"trade": {"ourOffers": [{"type": "Gold", "amount": 50}], '
        '"theirOffers": [{"type": "Gold", "amount": 100}]}}\n'
        "```\n"
        'Offer types: "Gold", "gold_per_turn", "open_border", "embassy", or a resource name '
        'with "amount" = how many copies.'
    )


def seat_lock(game_id: str, civ_id: str) -> threading.Lock:
    with _seat_locks_guard:
        return _seat_locks.setdefault((str(game_id), str(civ_id).lower()), threading.Lock())


def memory_path(game_id: str, civ_id: str) -> str:
    safe_game = "".join(c for c in game_id if c.isalnum() or c in "-_") or "unknown"
    safe_civ = "".join(c for c in civ_id if c.isalnum() or c in "-_") or "unknown"
    return os.path.join(MEMORY_ROOT, safe_game, f"{safe_civ}.jsonl")


def chat_memory_path(game_id: str, civ_a: str, civ_b: str) -> str:
    safe_game = "".join(c for c in game_id if c.isalnum() or c in "-_") or "unknown"
    pair = "_".join(sorted(c.lower() for c in (civ_a, civ_b)))
    pair = "".join(c for c in pair if c.isalnum() or c in "-_")
    return os.path.join(MEMORY_ROOT, safe_game, f"chat_{pair}.jsonl")


def append_jsonl(path: str, record: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    record = {"time": datetime.now().isoformat(timespec="seconds"), **record}
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_memory(game_id: str, civ_id: str, record: Dict[str, Any]) -> None:
    append_jsonl(memory_path(game_id, civ_id), record)


SALIENT_CATEGORIES = {"War", "Trade", "Diplomacy", "Cities"}


def select_memory(
    game_id: str,
    civ_id: str,
    turn: int = 0,
    interlocutor: Optional[str] = None,
    recent_limit: int = 12,
    salient_limit: int = 8,
    interlocutor_limit: int = 6,
) -> List[str]:
    """P-memory 三层结构化检索（纯 jsonl 过滤，无向量库 - 决策 23 兼容）:

    - 近因层: the newest records regardless of kind (replaces the old fixed window)
    - 显著层: engine events in salient categories from ALL history - wars, trades,
      city events. Engine notifications are what the civ itself was told, so the
      historical "who did what to me when" survives long games.
    - 对手层: records that mention the current interlocutor.
    Layers are deduplicated (by jsonl line identity) and returned newest-first.
    """
    path = memory_path(game_id, civ_id)
    if not os.path.isfile(path):
        return []
    entries: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue

    def describe(entry: Dict[str, Any]) -> str:
        kind = entry.get("kind", "?")
        turn = entry.get("turn", "?")
        if kind == "engine_event":
            return f"turn {turn} [{entry.get('category', 'General')}]: {entry.get('text', '')}"
        if kind == "decision":
            intents = ", ".join(
                i.get("type", "?") + (f"->{i.get('targetCivID')}" if i.get("targetCivID") else "")
                for i in entry.get("intents", [])
            ) or "none"
            return f"turn {turn} decided: {intents}"
        if kind == "applied":
            results = ", ".join(
                (a.get("type", "?") + (":ok" if a.get("ok") else ":failed"))
                for a in entry.get("applied", [])
            ) or "nothing"
            return f"turn applied: {results}"
        return f"turn {turn} {kind}: {str(entry)[:160]}"

    seen: set = set()
    layers: List[List[Dict[str, Any]]] = []

    recent = entries[-recent_limit:]
    layers.append(recent)

    salient = [
        e for e in entries
        if e.get("kind") == "engine_event" and str(e.get("category", "")) in SALIENT_CATEGORIES
    ]
    layers.append(salient[-salient_limit:])

    if interlocutor:
        other = str(interlocutor).lower()
        involved = [
            e for e in entries
            if other in json.dumps(e, ensure_ascii=False).lower()
        ]
        layers.append(involved[-interlocutor_limit:])

    lines: List[str] = []
    for layer in reversed(layers):  # salient/interlocutor first, recency last
        for entry in layer:
            marker = id(entry)
            if marker in seen:
                continue
            seen.add(marker)
            lines.append(describe(entry))
    return lines


def read_recent_memory(game_id: str, civ_id: str, limit: int = 5) -> List[str]:
    path = memory_path(game_id, civ_id)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as file:
        lines = file.readlines()
    return [line.strip() for line in lines[-limit:] if line.strip()]


def seat_personality(civ_id: str, slice_personality: Optional[str] = None) -> str:
    """P4 性格锁: the engine's nation personality (from the slice) wins; config only overrides."""
    if slice_personality and str(slice_personality).strip() and str(slice_personality) != "neutral":
        return f"{slice_personality} (engine nation personality)"
    seat = config_data.get(civ_id.lower(), {})
    return str(seat.get("personality", "pragmatic ruler"))


def seat_voice_for(civ_id: str) -> str:
    seat = config_data.get(str(civ_id).lower(), {})
    override = seat.get("voice")
    return seat_voice(civ_id, str(override) if override else None)


def intent_type_list() -> str:
    # Single source of truth: civagent/utils/intent_models.INTENT_TYPES.
    return ", ".join(sorted(INTENT_TYPES))


def _era_from_slice(slice_data: Dict[str, Any]) -> Tuple[str, int, int]:
    era = str(slice_data.get("era") or "")
    try:
        era_number = int(slice_data.get("eraNumber", 0) or 0)
    except (TypeError, ValueError):
        era_number = 0
    try:
        era_count = int(slice_data.get("eraCount", 0) or 0)
    except (TypeError, ValueError):
        era_count = 0
    return era, era_number, era_count


def scrub_intent_chat_text(intents: List[Intent], era_number: int, language: str, era: str = "", era_count: int = 0) -> List[Intent]:
    scrubbed: List[Intent] = []
    for intent in intents:
        if intent.chatText:
            intent = intent.model_copy(
                update={"chatText": scrub_anachronisms(intent.chatText, era_number, language, era, era_count)}
            )
        scrubbed.append(intent)
    return scrubbed


def build_prompt(
    slice_data: Dict[str, Any],
    memory_lines: List[str],
    *,
    interlocutor: Optional[str] = None,
    share_intel_active: bool = False,
) -> List[Dict[str, str]]:
    civ_id = slice_data["civID"]
    game_id = str(slice_data["gameId"])
    era, era_number, _ = _era_from_slice(slice_data)
    trust = trust_store.TRUST_DEFAULT
    if interlocutor:
        trust = trust_store.get_trust(MEMORY_ROOT, game_id, civ_id, interlocutor)
    system = SYSTEM_PROMPT.format(
        civ_id=civ_id,
        personality=seat_personality(civ_id, slice_data.get("personality")),
        voice=seat_voice_for(civ_id),
        era_lock=era_lock_instructions(era, era_number),
        third_party_rule=trust_store.third_party_rule_text(trust, share_intel_active),
        language=str(slice_data.get("language") or "English"),
        intent_types=intent_type_list(),
    )
    known = ", ".join(
        (
            f"{k['civID']}({k.get('relationship', 'unknown')}, war={k.get('atWar', False)}"
            f", prox={k.get('proximity', '?')}, sameContinent={k.get('sameContinent')}"
            f", landReachable={k.get('landReachable')}, border={k.get('borderThreat', 'n/a')}"
            f"{', era=' + k['era'] if k.get('era') else ''})"
        )
        for k in slice_data.get("knownCivs", [])
    )
    cities = "; ".join(
        f"{c.get('name', '?')}(pop{c.get('population', 0)}, {c.get('currentConstruction', '?')})"
        for c in slice_data.get("cities", [])
    )
    borders = slice_data.get("borderThreats") or []
    border_txt = ", ".join(
        f"{b.get('civID')}={b.get('level')}({b.get('visibleEnemyBucket')})" for b in borders
    ) or "none visible"
    claims = trust_store.recent_claims(MEMORY_ROOT, game_id, civ_id)
    # M-content-1: tell the brain it may be in a modded ruleset, and what the resources are.
    ruleset_ref = slice_data.get("ruleset") or {}
    ruleset_txt = str(ruleset_ref.get("name") or "unknown")
    if ruleset_ref.get("mods"):
        ruleset_txt += " + mods: " + ", ".join(str(m) for m in ruleset_ref["mods"])
    resource_kinds = slice_data.get("resourceKinds") or {}
    user = (
        f"Turn {slice_data['turn']} of game {slice_data['gameId']}.\n"
        f"Ruleset: {ruleset_txt}.\n"
        f"Your era: {era or 'unknown'} (eraNumber={era_number}). "
        f"Navy ships: {slice_data.get('navyShipCount', 0)}. "
        f"Nearest explored hostile city: {slice_data.get('nearestHostileCity') or 'none'}.\n"
        f"Border threats (visible only, no coordinates): {border_txt}.\n"
        f"Your civ: gold={slice_data['gold']} (delta {slice_data['goldPerTurn']}/turn), "
        f"happiness={slice_data['happiness']}, resources={slice_data.get('resources', {})} "
        f"(kinds: {resource_kinds}). "
        f"Science invested in current techs: {slice_data.get('science', {})}. "
        f"Culture stored: {slice_data.get('cultureStored', slice_data.get('culture', {}))}. "
        f"Faction balance: {slice_data.get('factionBalance', 'n/a')}.\n"
        f"{_trade_state_line(game_id, civ_id, slice_data=slice_data)}\n"
        f"{_agent_actions_line(game_id, civ_id, slice_data=slice_data)}\n"
        f"Known civs: {known or 'none'}.\n"
        f"Your cities: {cities or 'none'}.\n"
        f"Recent claims: {' | '.join(claims) or 'none'}.\n"
        f"Recent memory: {' | '.join(memory_lines) or 'empty (early game).'}\n"
        "Decide this turn's intents as JSON."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------- chat (W3)

INTENT_FENCE = re.compile(r"```intent\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)

# key: (gameId, playerCiv) -> {"seq": int, "events": [event dicts]}
_chat_events: Dict[Tuple[str, str], Dict[str, Any]] = {}
_chat_events_guard = threading.Lock()


def _chat_events_for(game_id: str, player_civ: str) -> Dict[str, Any]:
    with _chat_events_guard:
        return _chat_events.setdefault((str(game_id), str(player_civ).lower()), {"seq": 0, "events": []})


def parse_intent_fences(text: str) -> Tuple[str, List[Intent]]:
    """Split ```intent fences out of a chat reply: (display_text, draft_intents).

    Reasoning comes off first: a local thinking model drafts plenty of intents it decides
    against, and a fence in the scratchpad is not a promise to the player.
    """
    drafts: List[Intent] = []
    text = local_models.strip_reasoning(text)

    def _collect(match: re.Match) -> str:
        try:
            intent = Intent.model_validate_json(match.group(1))
            if intent.is_whitelisted():
                drafts.append(intent)
        except Exception:
            pass  # an unparsable fence is dropped, the chat text still stands
        return ""

    clean = INTENT_FENCE.sub(_collect, text)
    return clean.strip(), drafts


def _chat_prompt(
    game_id: str,
    from_civ: str,
    to_civ: str,
    text: str,
    language: str,
    era: str,
    era_number: int,
) -> List[Dict[str, str]]:
    history: List[str] = []
    path = chat_memory_path(game_id, from_civ, to_civ)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as file:
            for line in file.readlines()[-8:]:
                try:
                    entry = json.loads(line)
                    history.append(f"{entry.get('from', '?')} -> {entry.get('to', '?')}: {entry.get('text', '')}")
                except Exception:
                    continue
    trust = trust_store.get_trust(MEMORY_ROOT, game_id, to_civ, from_civ)
    share_active = "share_intel" in text.lower() or "情报" in text
    system = CHAT_SYSTEM_PROMPT.format(
        civ_id=to_civ,
        personality=seat_personality(to_civ),
        voice=seat_voice_for(to_civ),
        era_lock=era_lock_instructions(era, era_number),
        third_party_rule=trust_store.third_party_rule_text(trust, share_active),
        language=language or "English",
        intent_types=intent_type_list(),
    )
    claims = trust_store.recent_claims(MEMORY_ROOT, game_id, to_civ, limit=3)
    user = (
        f"Your era: {era or 'unknown'} (eraNumber={era_number}). Trust with {from_civ}: {trust:.2f}.\n"
        f"{_economy_line(game_id, to_civ, from_civ)}\n"
        f"{_trade_state_line(game_id, to_civ, from_civ)}\n"
        f"{_agent_actions_line(game_id, to_civ)}\n"
        f"{_trading_rules(to_civ, from_civ)}\n"
        f"Recent claims: {' | '.join(claims) or 'none'}.\n"
        f"Recent dialogue:\n" + ("\n".join(history) or "(none yet)") +
        f"\n\n{from_civ} says to you: {text}\nReply as {to_civ}."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _record_share_intel_claims(game_id: str, from_civ: str, to_civ: str, drafts: List[Intent]) -> None:
    for intent in drafts:
        if intent.type != "share_intel" or not intent.intel:
            continue
        claim = intent.intel.claim.strip()
        if not claim:
            continue
        # Player told agent (from->to) or agent shares with player (actor=to)
        trust_store.append_claim(
            MEMORY_ROOT, game_id,
            from_civ=intent.actorCivID or to_civ,
            to_civ=intent.targetCivID or from_civ,
            claim=claim,
            source="share_intel",
            kind=trust_store.classify_claim_kind(claim, intent.intel.kind),
            about_civ=intent.intel.aboutCiv,
        )
        # Listening to a claim bumps trust slightly; verification comes later (W5).
        trust_store.bump_trust(MEMORY_ROOT, game_id, to_civ, from_civ, 0.03)


def _process_chat_message(
    game_id: str,
    from_civ: str,
    to_civ: str,
    text: str,
    language: str,
    era: str,
    era_number: int,
    era_count: int,
    turn: int,
    share_intel_claim: str = "",
    share_intel_about: str = "",
    attachment_path: str = "",
) -> None:
    try:
        if share_intel_claim.strip():
            about = share_intel_about.strip()
            claim_text = share_intel_claim.strip()
            trust_store.append_claim(
                MEMORY_ROOT, game_id,
                from_civ=from_civ,
                to_civ=to_civ,
                claim=claim_text,
                source="player_share_intel",
                kind=trust_store.classify_claim_kind(claim_text),
                about_civ=about,
                turn=int(turn),
            )
            trust_store.bump_trust(
                MEMORY_ROOT, game_id, to_civ, from_civ, 0.03,
                reason="claim received",
                evidence=f"claim: {claim_text}",
            )
            text = (
                f"{text}\n(Player submitted share_intel claim about {about or 'unknown'}"
                + (f", attachment={attachment_path}" if attachment_path else "")
                + " — treat as rumour, do not open fog.)"
            )
        elif attachment_path:
            text = f"{text}\n(Player attached image file: {attachment_path}. Describe only if relevant; never invent map tiles.)"

        # Allocate the event seq before the LLM call: the transcript carries it, and streaming
        # `partial` events share it so they can be replaced in place rather than accumulating.
        events = _chat_events_for(game_id, from_civ)
        with _chat_events_guard:
            events["seq"] += 1
            seq = events["seq"]

        with seat_lock(game_id, to_civ):
            payload = {"messages": _chat_prompt(
                game_id, from_civ, to_civ, text, language, era, era_number
            )}
            _remember_prompt(game_id, to_civ, "chat", payload["messages"])
            if llm_router.streaming_enabled():
                # Plan A: plain prose streamed token-by-token; the ```intent fence is parsed out
                # of the final text, so no JSON wrapper is involved.
                def _emit_partial(accumulated: str, _seq: int = seq) -> None:
                    with _chat_events_guard:
                        buf = events["events"]
                        if buf and buf[-1].get("kind") == "partial" and buf[-1].get("seq") == _seq:
                            buf[-1]["text"] = accumulated  # one live bubble, updated in place
                        else:
                            buf.append({
                                "seq": _seq, "kind": "partial",
                                "fromCiv": to_civ, "toCiv": from_civ,
                                "text": accumulated, "status": "streaming",
                            })

                reply, _raw = llm_router.chat_stream_text(
                    payload, country=to_civ, on_delta=_emit_partial
                )
            else:
                output = llm_router.chat(payload, country=to_civ, llm_config={"response_model": ChatOutput})
                reply = ChatOutput.model_validate_json(output[0]["content"]).reply
        clean_text, drafts = parse_intent_fences(reply)
        clean_text = scrub_anachronisms(clean_text, era_number, language, era, era_count)
        drafts = scrub_intent_chat_text(drafts, era_number, language, era, era_count)
        _record_share_intel_claims(game_id, from_civ, to_civ, drafts)
        append_jsonl(chat_memory_path(game_id, from_civ, to_civ), {
            "from": from_civ, "to": to_civ, "text": text,
            "seq": seq,
            "shareIntelClaim": share_intel_claim or None,
            "attachmentPath": attachment_path or None,
        })
        append_jsonl(chat_memory_path(game_id, from_civ, to_civ), {
            "from": to_civ, "to": from_civ, "text": clean_text,
            "seq": seq,
            "draftIntents": [intent.model_dump() for intent in drafts],
        })
        with _chat_events_guard:
            events["events"].append({
                "seq": seq,
                "kind": "reply",
                "fromCiv": to_civ,
                "toCiv": from_civ,
                "text": clean_text,
                "intents": [intent.model_dump() for intent in drafts],
                "status": "stable",
            })
    except Exception as exc:  # a chat failure must surface as an error event, not vanish
        events = _chat_events_for(game_id, from_civ)
        with _chat_events_guard:
            events["seq"] += 1
            events["events"].append({
                "seq": events["seq"], "kind": "error", "fromCiv": to_civ, "toCiv": from_civ,
                "text": f"(chat failed: {llm_router.redact(str(exc))})", "intents": [], "status": "stable",
            })


@app.errorhandler(Exception)
def on_error(exc: Exception):
    # Real HTTP errors (404/405/...) must keep their status: the catch-all is for LLM/validation
    # failures, which surface as a JSON 500 instead of silently returning empty intents.
    if isinstance(exc, HTTPException):
        return exc
    return {"error": str(exc)}, 500


def _local_state(provider: str, country: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """What the resolved local server actually is - the answer to "why is every seat native?".

    Only meaningful for loopback providers; cloud slots report nothing extra.
    """
    if provider not in ("ollama", "openai_compatible"):
        return None
    model_provider, model_id = llm_router.model_id_for(country, None)
    base_url = llm_router.get_provider_config(model_provider)["base_url"]
    if not local_models.is_local_base_url(base_url):
        return {"base_url": base_url, "model": model_id, "local": False}
    profile = local_models.profile_for(base_url, model_id)
    return {
        "base_url": base_url,
        "model": model_id,
        "model_resolved": profile.get("model_resolved"),
        "flavor": profile.get("flavor"),
        "reachable": profile.get("reachable"),
        "context_length": profile.get("context_length"),
        "context_capacity": profile.get("context_capacity"),
        "context_note": profile.get("context_note"),
        "quantization": profile.get("quantization"),
        "structured": profile.get("structured"),
        "models": profile.get("models", [])[:12],
        "reason": profile.get("reason"),
    }


@app.route("/healthz")
def healthz():
    provider = llm_router.default_provider()
    reason = llm_router.provider_missing_reason(provider)
    if reason:
        return {"llm": "missing", "provider": provider, "reason": reason}, 503
    # The key can be present while the client is unbuildable (a missing pip package).
    # Probe it here so a broken brain reports unhealthy at startup/probe time instead of
    # failing silently on the first chat message.
    broken = llm_router.client_dependency_problem(provider)
    if broken:
        return {"llm": "broken", "provider": provider, "reason": broken}, 503
    # ... and the client can build while the GPU box is switched off: that is the single most
    # common local deployment state, and it must not report ok.
    down = llm_router.local_server_problem(provider)
    if down:
        return {"llm": "offline", "provider": provider, "reason": down,
                "local": _local_state(provider)}, 503
    return {"llm": "ok", "provider": provider, "local": _local_state(provider)}, 200


def _is_localhost() -> bool:
    addr = (request.remote_addr or "").strip().lower()
    return addr in {"127.0.0.1", "::1", "localhost"} or addr.startswith("127.")


@app.route("/v1/config", methods=["GET", "POST"])
def config_endpoint():
    """Local-only secrets bridge for Unciv Options → CivAgent.

    GET  → public status (no raw key). POST → write ~/.civagent/secrets.json and hot-reload.
    Never accepts remote clients — keys must not leave the player's machine.
    """
    if not _is_localhost():
        return {"error": "forbidden: /v1/config is localhost-only"}, 403
    if request.method == "GET":
        return secrets_store.public_status(), 200

    body = request.get_json(force=True, silent=True) or {}
    previous = secrets_store.load_secrets()
    provider = str(body.get("provider") or previous.get("provider") or "deepseek").strip()
    # Blank apiKey keeps the previously saved key (UI password field left empty).
    incoming_key = body.get("apiKey")
    if incoming_key is None:
        incoming_key = body.get("api_key")
    if incoming_key is None:
        api_key = str(previous.get("apiKey") or previous.get("api_key") or "")
    else:
        api_key = str(incoming_key)
    base_url = body.get("baseUrl")
    if base_url is None:
        base_url = body.get("base_url", previous.get("baseUrl") or previous.get("base_url") or "")
    ai_server = body.get("aiServerUrl") or body.get("ai_server_url") or previous.get("aiServerUrl") or "http://127.0.0.1:2335"
    repo_root = body.get("repoRoot")
    if repo_root is None:
        repo_root = body.get("repo_root", previous.get("repoRoot") or "")
    model = body.get("model")
    if model is None:
        model = previous.get("model") or ""
    streaming = body.get("streaming")
    if streaming is None:
        streaming = bool(previous.get("streaming") or False)
    sampling = body.get("sampling")
    if not isinstance(sampling, dict):
        previous_sampling = previous.get("sampling")
        sampling = previous_sampling if isinstance(previous_sampling, dict) else {}

    saved = {
        "provider": provider,
        "apiKey": api_key,
        "baseUrl": str(base_url or ""),
        "aiServerUrl": str(ai_server),
        "repoRoot": str(repo_root or ""),
        "model": str(model or ""),
        "streaming": bool(streaming),
        "sampling": sampling,
    }
    path = secrets_store.save_secrets(saved)
    secrets_store.apply_secrets_to_environ(saved, force=True)
    civagent_config.reload_config_data()
    status = secrets_store.public_status(saved)
    status["ok"] = True
    status["path"] = str(path)
    return status, 200


@app.route("/v1/decision", methods=["POST"])
def decision():
    global _last_jev_report
    slice_data = request.get_json(force=True, silent=True) or {}
    missing = [field for field in REQUIRED_SLICE_FIELDS if field not in slice_data]
    if missing:
        return {"error": f"ObservationSlice missing required fields: {missing}"}, 400
    _remember_slice(slice_data)  # chat has no slice of its own; this is what lets it quote real numbers
    game_id = str(slice_data["gameId"])
    civ_id = str(slice_data["civID"])

    with seat_lock(game_id, civ_id):
        # P-memory event stream: native engine notifications, fog-safe by construction
        for event in slice_data.get("newEvents", []) or []:
            append_memory(game_id, civ_id, {
                "kind": "engine_event",
                "turn": event.get("turn", slice_data.get("turn", 0)),
                "category": str(event.get("category", "General")),
                "text": str(event.get("text", ""))[:300],
            })
        # W5.7 notification hook: engine truth (the slice) verifies/falsifies open claims
        verdicts = trust_store.verify_pending_claims(MEMORY_ROOT, game_id, civ_id, slice_data)
        for verdict in verdicts:
            append_memory(game_id, civ_id, {"kind": "claim_" + verdict["verdict"], **verdict})
        memory_lines = select_memory(game_id, civ_id, turn=int(slice_data.get("turn", 0)))
        payload = {"messages": build_prompt(slice_data, memory_lines)}
        _remember_prompt(game_id, civ_id, "decision", payload["messages"])
        output = llm_router.chat(payload, country=civ_id, llm_config={"response_model": DecisionOutput})
        intents = sanitize_intents(DecisionOutput.model_validate_json(output[0]["content"]))
        era, era_number, era_count = _era_from_slice(slice_data)
        intents = scrub_intent_chat_text(intents, era_number, "English", era, era_count)
        # Optional jevos gate: the LLM proposes, the CPU decision model keeps what is worth
        # doing now. Disabled by default; unreachable/misconfigured = pass through unchanged.
        jev_report: Optional[Dict[str, Any]] = None
        if jev_decider.enabled():
            intents, jev_report = jev_decider.gate_intents(
                jev_decider.state_from_slice(slice_data), intents)
            _last_jev_report = jev_report
        for intent in intents:
            if intent.type == "share_intel" and intent.intel and intent.intel.claim.strip():
                trust_store.append_claim(
                    MEMORY_ROOT, game_id,
                    from_civ=intent.actorCivID or civ_id,
                    to_civ=intent.targetCivID or civ_id,
                    claim=intent.intel.claim.strip(),
                    source="decision",
                    kind=trust_store.classify_claim_kind(intent.intel.claim.strip(), intent.intel.kind),
                    about_civ=intent.intel.aboutCiv,
                    turn=int(slice_data.get("turn", 0)),
                )
    append_memory(game_id, civ_id, {
        "kind": "decision", "turn": slice_data["turn"],
        "intents": [intent.model_dump() for intent in intents],
        "jev": jev_report,
    })
    return {"intents": [intent.model_dump() for intent in intents]}, 200


@app.route("/v1/trust")
def trust_view():
    """信任可追溯: current trust table + the change log with reasons/evidence."""
    game_id = request.args.get("gameId", "")
    civ_id = request.args.get("civID", "")
    if not game_id or not civ_id:
        return {"error": "gameId and civID are required"}, 400
    table = trust_store.load_trust_table(MEMORY_ROOT, game_id)
    mine = {
        key.split("|", 1)[1]: value
        for key, value in table.items()
        if key.split("|", 1)[0] == civ_id.lower()
    }
    log_path = trust_store.trust_log_path(MEMORY_ROOT, game_id)
    log_entries: List[Dict[str, Any]] = []
    if os.path.isfile(log_path):
        with open(log_path, "r", encoding="utf-8") as file:
            for line in file:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("self", "").lower() == civ_id.lower():
                    log_entries.append(entry)
    return {"trust": mine, "log": log_entries[-30:]}, 200


@app.route("/v1/applied", methods=["POST"])
def applied():
    body = request.get_json(force=True, silent=True) or {}
    missing = [field for field in ("gameId", "civID", "applied") if field not in body]
    if missing:
        return {"error": f"applied report missing fields: {missing}"}, 400
    append_memory(str(body["gameId"]), str(body["civID"]), {
        "kind": "applied", "applied": body["applied"],
        "fellBackToNative": bool(body.get("fellBackToNative", False)),
    })
    return {"ok": True}, 200


@app.route("/message", methods=["POST"])
def message():
    """200 = 已开始 (coder/agentapi semantics): validation passed, processing runs async."""
    body = request.get_json(force=True, silent=True) or {}
    missing = [field for field in ("gameId", "fromCiv", "toCiv", "text") if field not in body]
    if missing:
        return {"error": f"message missing fields: {missing}"}, 400
    game_id = str(body["gameId"])
    from_civ = str(body["fromCiv"])
    to_civ = str(body["toCiv"])
    text = str(body["text"])
    language = str(body.get("language") or "English")
    era = str(body.get("era") or "")
    try:
        era_number = int(body.get("eraNumber", 0) or 0)
    except (TypeError, ValueError):
        era_number = 0
    try:
        era_count = int(body.get("eraCount", 0) or 0)
    except (TypeError, ValueError):
        era_count = 0
    try:
        turn = int(body.get("turn", 0) or 0)
    except (TypeError, ValueError):
        turn = 0
    share_claim = str(body.get("shareIntelClaim") or "")
    share_about = str(body.get("shareIntelAboutCiv") or "")
    attachment = str(body.get("attachmentPath") or "")
    if not text.strip() and not share_claim.strip():
        return {"error": "empty message"}, 400
    if not text.strip() and share_claim.strip():
        text = f"[share_intel] {share_claim}"
    thread = threading.Thread(
        target=_process_chat_message,
        args=(game_id, from_civ, to_civ, text, language, era, era_number, era_count, turn, share_claim, share_about, attachment),
        daemon=True,
        name=f"chat-{from_civ}-{to_civ}",
    )
    thread.start()
    return {"started": True}, 200


@app.route("/upload", methods=["POST"])
def upload():
    """Accept a local image as base64; store under deployment/data/uploads/. Never enters GameInfo."""
    body = request.get_json(force=True, silent=True) or {}
    file_name = str(body.get("fileName") or "upload.bin")
    content_b64 = str(body.get("contentBase64") or "")
    if not content_b64:
        return {"ok": False, "error": "contentBase64 required"}, 400
    safe_name = "".join(c for c in file_name if c.isalnum() or c in "._-") or "upload.bin"
    if len(safe_name) > 80:
        safe_name = safe_name[-80:]
    try:
        import base64
        import uuid
        raw = base64.b64decode(content_b64, validate=False)
    except Exception:
        return {"ok": False, "error": "invalid base64"}, 400
    if len(raw) > 4_000_000:
        return {"ok": False, "error": "file too large (max 4MB)"}, 400
    upload_dir = os.path.join(MEMORY_ROOT, "_uploads")
    os.makedirs(upload_dir, exist_ok=True)
    stored = f"{uuid.uuid4().hex}_{safe_name}"
    path = os.path.join(upload_dir, stored)
    with open(path, "wb") as file:
        file.write(raw)
    return {"ok": True, "path": path}, 200


def _shutdown_process() -> None:
    """Exit the brain. Module-level so tests can patch it instead of killing pytest."""
    time.sleep(0.3)  # let the HTTP response flush first
    os._exit(0)


@app.route("/v1/prompt")
def prompt_view():
    """Last assembled prompt - the in-game viewer shows exactly what the LLM saw.

    With no args it returns the most recent prompt across seats, so the viewer also works from
    the Options tab where there is no current game.
    """
    game_id = request.args.get("gameId", "")
    civ_id = request.args.get("civID", "")
    with _last_prompts_guard:
        if game_id and civ_id:
            payload = _last_prompts.get((str(game_id), str(civ_id).lower()))
        else:
            payload = max(_last_prompts.values(), key=lambda r: str(r.get("time") or ""), default=None)
    if payload is None:
        return {"error": "no prompt captured yet"}, 404
    return payload, 200


@app.route("/v1/shutdown", methods=["POST"])
def shutdown():
    """Localhost-only: ask this brain to exit so the game can relaunch it.

    Used by Options -> CivAgent and the dialogue "+" menu after code/config changes: the brain
    runs from source, so a stale process keeps old behaviour until it is restarted.
    """
    if not _is_localhost():
        return {"error": "forbidden: /v1/shutdown is localhost-only"}, 403
    threading.Thread(target=_shutdown_process, daemon=True, name="shutdown").start()
    return {"ok": True, "stopping": True}, 200


@app.route("/events")
def events():
    """SSE stream of chat events for one player. ?gameId=&civID=&after=<seq>"""
    game_id = request.args.get("gameId", "")
    civ_id = request.args.get("civID", "")
    try:
        after = int(request.args.get("after", "0"))
    except ValueError:
        after = 0
    if not game_id or not civ_id:
        return {"error": "gameId and civID are required"}, 400

    def stream():
        # Held open for 20s, then the client reconnects: this lets streaming `partial` events
        # arrive as they are produced instead of one batch per reconnect. Consumed by cursor,
        # not by seq, because a streaming reply's partials deliberately share its final seq.
        deadline = time.time() + 20.0
        store = _chat_events_for(game_id, civ_id)
        with _chat_events_guard:
            cursor = len(store["events"])
            for index, event in enumerate(store["events"]):
                if event["seq"] > after:
                    cursor = index
                    break
        while time.time() < deadline:
            store = _chat_events_for(game_id, civ_id)
            with _chat_events_guard:
                pending = store["events"][cursor:]
                cursor = len(store["events"])
            for event in pending:
                yield f"id: {event['seq']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            time.sleep(0.2)

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/messages")
def messages():
    """Transcript for one civ pair. ?gameId=&civ1=&civ2=&after=<index>"""
    game_id = request.args.get("gameId", "")
    civ_a = request.args.get("civ1", "")
    civ_b = request.args.get("civ2", "")
    try:
        after = int(request.args.get("after", "0"))
    except ValueError:
        after = 0
    if not game_id or not civ_a or not civ_b:
        return {"error": "gameId, civ1, civ2 are required"}, 400
    path = chat_memory_path(game_id, civ_a, civ_b)
    if not os.path.isfile(path):
        return {"messages": []}, 200
    with open(path, "r", encoding="utf-8") as file:
        lines = [json.loads(line) for line in file if line.strip()]
    return {"messages": lines[after:]}, 200


@app.route("/status")
def status():
    provider = llm_router.default_provider()
    with _seat_locks_guard:
        active_seats = len(_seat_locks)
    with _chat_events_guard:
        pending_events = sum(
            len(store["events"]) for store in _chat_events.values()
        )
    local = _local_state(provider) or {}
    # A shrunken n_ctx (the classic llama.cpp default of 2048) truncates the observation slice
    # mid-prompt, and the model then answers about a game it cannot see - say so loudly.
    context_length = local.get("context_length")
    with _last_prompts_guard:
        biggest = max((int(record.get("promptTokens") or 0) for record in _last_prompts.values()),
                      default=0)
    over_budget = bool(context_length) and biggest >= int(context_length * 0.8)
    problem = llm_router.local_server_problem(provider)
    jev_on = jev_decider.enabled()
    return {
        "provider": provider,
        "llm": "offline" if problem else ("ok" if llm_router.provider_missing_reason(provider) is None else "missing"),
        "reason": problem,
        "active_seats": active_seats,
        "chat_events_total": pending_events,
        "local": local,
        "discovered": local_models.discover_cached(),
        "prompt_tokens_biggest": biggest,
        "prompt_over_80pct_context": over_budget,
        "jev": {
            "enabled": jev_on,
            "url": jev_decider.url(),
            "reachable": jev_decider.health()["ok"] if jev_on else None,
            "threshold": jev_decider.threshold(),
            "last": _last_jev_report,
        },
    }, 200


def _serve(app, port: int) -> None:
    """Server preference: waitress (production-grade, pip install waitress) -> gevent ->
    werkzeug dev server (last resort). Logs which one is serving.

    Binds localhost only: this process holds API keys and accepts /v1/config and /v1/shutdown.
    """
    host = os.environ.get("AGENT_API_HOST", "127.0.0.1")
    try:
        from waitress import serve
        print(f"[brain] serving with waitress on {host}:{port}")
        serve(app, host=host, port=port, threads=4)
        return
    except ImportError:
        pass
    if WSGIServer is not None:
        print(f"[brain] serving with gevent on {host}:{port}")
        WSGIServer((host, port), app).serve_forever()
        return
    print(f"[brain] WARNING: werkzeug dev server on {host}:{port} (pip install waitress for production)")
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    port = int(os.environ.get("AGENT_API_PORT", "2335"))
    host = os.environ.get("AGENT_API_HOST", "127.0.0.1")
    # Singleton lock. A connect probe alone is not enough: on Windows SO_REUSEADDR lets a second
    # dev server bind the same port (that is how 29 brains with only 2 listeners accumulated), so
    # also require that *this* process can take the port exclusively before serving.
    import socket
    _alive = socket.socket()
    try:
        already_listening = _alive.connect_ex((host, port)) == 0
    finally:
        _alive.close()
    _claim = socket.socket()
    try:
        try:
            _claim.bind((host, port))
            claimed = True
        except OSError:
            claimed = False
    finally:
        _claim.close()
    if already_listening or not claimed:
        print(f"brain already listening on {host}:{port} - singleton lock, exiting")
        sys.exit(0)
    _serve(app, port)
