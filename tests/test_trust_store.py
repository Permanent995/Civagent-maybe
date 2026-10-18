"""W5 trust sidecar + third-party intel gate."""

import pytest
from civagent.utils import trust_store
from civagent.utils.intent_models import DecisionOutput, Intent, IntelPayload, sanitize_intents


def test_trust_defaults_and_bump(tmp_path):
    root = str(tmp_path)
    assert trust_store.get_trust(root, "g1", "china", "rome") == trust_store.TRUST_DEFAULT
    trust_store.bump_trust(root, "g1", "china", "rome", 0.3)
    assert abs(trust_store.get_trust(root, "g1", "china", "rome") - 0.7) < 1e-6
    assert trust_store.third_party_intel_allowed(0.7, False) is True
    assert trust_store.third_party_intel_allowed(0.4, False) is False
    assert trust_store.third_party_intel_allowed(0.4, True) is True


def test_claims_append_and_read(tmp_path):
    root = str(tmp_path)
    trust_store.append_claim(root, "g1", from_civ="rome", to_civ="china", claim="Rome massing north")
    lines = trust_store.recent_claims(root, "g1", "china")
    assert any("Rome massing north" in line for line in lines)


def test_share_intel_without_claim_dropped():
    out = DecisionOutput(intents=[
        Intent(type="share_intel", actorCivID="rome", targetCivID="china"),
        Intent(type="share_intel", actorCivID="rome", targetCivID="china",
               intel=IntelPayload(claim="Egypt researching iron")),
        Intent(type="declare_war", actorCivID="rome", targetCivID="egypt"),
    ])
    kept = sanitize_intents(out)
    assert len(kept) == 2
    assert kept[0].type == "share_intel"
    assert kept[0].intel.claim.startswith("Egypt")


def test_ban_text_mentions_threshold():
    ban = trust_store.third_party_rule_text(0.2, False)
    assert "BAN" in ban
    allow = trust_store.third_party_rule_text(0.8, False)
    assert "allowed" in allow.lower()


# ---- W5.7 verification hook ----

def _slice(turn, wars):
    return {"turn": turn, "knownCivs": [{"civID": civ, "atWar": war} for civ, war in wars.items()]}


def test_war_claim_verified_bumps_trust(tmp_path):
    root = str(tmp_path)
    trust_store.append_claim(root, "g1", from_civ="rome", to_civ="china",
                             claim="Egypt will attack", kind="war", about_civ="egypt", turn=5)
    before = trust_store.get_trust(root, "g1", "china", "rome")
    verdicts = trust_store.verify_pending_claims(root, "g1", "china", _slice(6, {"egypt": True}))
    assert len(verdicts) == 1 and verdicts[0]["verdict"] == "verified"
    assert trust_store.get_trust(root, "g1", "china", "rome") == pytest.approx(before + 0.05)


def test_war_claim_falsified_when_window_closes(tmp_path):
    root = str(tmp_path)
    trust_store.append_claim(root, "g1", from_civ="rome", to_civ="china",
                             claim="Egypt will attack", kind="war", about_civ="egypt", turn=5)
    before = trust_store.get_trust(root, "g1", "china", "rome")
    verdicts = trust_store.verify_pending_claims(root, "g1", "china", _slice(16, {"egypt": False}))
    assert verdicts[0]["verdict"] == "falsified"
    assert trust_store.get_trust(root, "g1", "china", "rome") == pytest.approx(before - 0.05)


def test_claim_stays_open_inside_window_and_resolves_once(tmp_path):
    root = str(tmp_path)
    trust_store.append_claim(root, "g1", from_civ="rome", to_civ="china",
                             claim="Egypt will attack", kind="war", about_civ="egypt", turn=5)
    assert trust_store.verify_pending_claims(root, "g1", "china", _slice(8, {"egypt": False})) == []
    # window closes -> falsified once...
    verdicts = trust_store.verify_pending_claims(root, "g1", "china", _slice(16, {"egypt": False}))
    assert len(verdicts) == 1
    # ...and never scored again
    assert trust_store.verify_pending_claims(root, "g1", "china", _slice(30, {"egypt": False})) == []


def test_peace_claim_verifies_on_war_end(tmp_path):
    root = str(tmp_path)
    trust_store.append_claim(root, "g1", from_civ="rome", to_civ="china",
                             claim="Egypt seeks peace", kind="peace", about_civ="egypt", turn=5)
    # still at war -> open; war ends -> verified
    assert trust_store.verify_pending_claims(root, "g1", "china", _slice(6, {"egypt": True})) == []
    verdicts = trust_store.verify_pending_claims(root, "g1", "china", _slice(7, {"egypt": False}))
    assert verdicts[0]["verdict"] == "verified"


def test_unobservable_about_civ_stays_open(tmp_path):
    root = str(tmp_path)
    trust_store.append_claim(root, "g1", from_civ="rome", to_civ="china",
                             claim="Mongolia will attack", kind="war", about_civ="mongolia", turn=5)
    # mongolia not met -> not observable -> no verdict even past the window
    assert trust_store.verify_pending_claims(root, "g1", "china", _slice(30, {})) == []


def test_denounce_in_whitelist():
    out = DecisionOutput(intents=[Intent(type="denounce", actorCivID="rome", targetCivID="egypt")])
    kept = sanitize_intents(out)
    assert kept[0].type == "denounce"


def test_classify_claim_kind_from_free_text():
    assert trust_store.classify_claim_kind("罗马北部在囤兵") == "massing_troops"
    assert trust_store.classify_claim_kind("Egypt is massing troops on our border") == "massing_troops"
    assert trust_store.classify_claim_kind("Egypt will declare war") == "war"
    assert trust_store.classify_claim_kind("Rome seeks peace") == "peace"
    assert trust_store.classify_claim_kind("Egypt is researching nuclear weapons") == "nuke_research"
    assert trust_store.classify_claim_kind("nice weather today") == "other"
    # an explicit kind from the intent always wins over classification
    assert trust_store.classify_claim_kind("Rome seeks peace", explicit="war") == "war"


def _slice_borders(turn, wars, borders):
    return {
        "turn": turn,
        "knownCivs": [{"civID": civ, "atWar": war} for civ, war in wars.items()],
        "borderThreats": [
            {"civID": civ, "level": level, "visibleEnemyBucket": "1-2"}
            for civ, level in borders.items()
        ],
    }


def test_massing_troops_verified_by_border_threats(tmp_path):
    root = str(tmp_path)
    trust_store.append_claim(root, "g1", from_civ="rome", to_civ="china",
                             claim="Egypt massing north", kind="massing_troops",
                             about_civ="egypt", turn=5)
    before = trust_store.get_trust(root, "g1", "china", "rome")
    verdicts = trust_store.verify_pending_claims(
        root, "g1", "china", _slice_borders(7, {"egypt": False}, {"egypt": "medium"})
    )
    assert len(verdicts) == 1 and verdicts[0]["verdict"] == "verified"
    assert trust_store.get_trust(root, "g1", "china", "rome") == pytest.approx(before + 0.05)


def test_massing_troops_falsified_and_flags_denounce(tmp_path):
    root = str(tmp_path)
    trust_store.append_claim(root, "g1", from_civ="rome", to_civ="china",
                             claim="Egypt massing north", kind="massing_troops",
                             about_civ="egypt", turn=5)
    before = trust_store.get_trust(root, "g1", "china", "rome")
    # window closes with nothing ever seen on the border -> falsified
    verdicts = trust_store.verify_pending_claims(
        root, "g1", "china", _slice_borders(16, {"egypt": False}, {"egypt": "none"})
    )
    assert verdicts[0]["verdict"] == "falsified"
    assert verdicts[0]["denounceSuggested"] is True
    assert trust_store.get_trust(root, "g1", "china", "rome") == pytest.approx(before - 0.05)

    # a verified claim must never suggest denouncing
    root2 = str(tmp_path / "b")
    trust_store.append_claim(root2, "g1", from_civ="rome", to_civ="china",
                             claim="Egypt massing north", kind="massing_troops",
                             about_civ="egypt", turn=5)
    ok = trust_store.verify_pending_claims(
        root2, "g1", "china", _slice_borders(6, {"egypt": False}, {"egypt": "high"})
    )
    assert ok[0]["denounceSuggested"] is False


def test_unobservable_kind_stays_open_past_window(tmp_path):
    root = str(tmp_path)
    trust_store.append_claim(root, "g1", from_civ="rome", to_civ="china",
                             claim="Egypt researching nukes", kind="nuke_research",
                             about_civ="egypt", turn=5)
    # we never observe another civ's tech tree, so a nuke claim must not be window-falsified
    assert trust_store.verify_pending_claims(root, "g1", "china", _slice(40, {"egypt": False})) == []
