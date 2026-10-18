"""Pydantic models for the frozen intent contract (docs/protocol/intent.schema.json, v1).

The whitelist is the human-player action space. addstat / tile edits / friendship-value
edits are structurally impossible here - anything outside INTENT_TYPES is dropped.
"""

from typing import List, Optional

from pydantic import BaseModel

INTENT_TYPES = {
    "declare_war", "make_peace", "seek_peace", "propose_trade", "buy_luxury", "open_border",
    "research_agreement", "form_ally", "mutual_defense",
    "share_intel", "denounce", "chat",
}


class TradeOffer(BaseModel):
    type: str
    amount: float = 0
    ref: Optional[str] = None


class TradePayload(BaseModel):
    ourOffers: List[TradeOffer] = []
    theirOffers: List[TradeOffer] = []


class IntelPayload(BaseModel):
    claim: str
    """Optional structured kind for the verification hook (W5.7): war | peace | other."""
    kind: str = "other"
    aboutCiv: str = ""


class Intent(BaseModel):
    type: str
    actorCivID: str
    targetCivID: Optional[str] = None
    chatText: Optional[str] = None
    trade: Optional[TradePayload] = None
    intel: Optional[IntelPayload] = None
    warFocus: Optional[str] = None

    def is_whitelisted(self) -> bool:
        return self.type in INTENT_TYPES


class DecisionOutput(BaseModel):
    intents: List[Intent] = []


class ChatOutput(BaseModel):
    """Free-chat reply (W3): plain text; intents ride in ```intent fences and are parsed out."""
    reply: str


def sanitize_intents(output: DecisionOutput) -> List[Intent]:
    """Keep only whitelisted intents; share_intel must carry a non-empty claim."""
    kept: List[Intent] = []
    for intent in output.intents:
        if not intent.is_whitelisted():
            continue
        if intent.type == "share_intel":
            claim = (intent.intel.claim if intent.intel else "") or ""
            if not claim.strip():
                continue
        kept.append(intent)
    return kept
