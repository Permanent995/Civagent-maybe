"""Era voice + anachronism scrub tests."""

from civagent.utils.leader_voice import (
    ERA_ATOMIC,
    ERA_CLASSICAL,
    banned_hits,
    era_lock_instructions,
    normalize_era_tier,
    scrub_anachronisms,
    seat_voice,
)


def test_china_voice_is_distinct():
    assert "帝王" in seat_voice("china") or "文言" in seat_voice("china")
    assert seat_voice("rome") != seat_voice("china")


def test_any_nation_gets_its_own_voice():
    # Mod nations ("Sweden", "Sheng Tang") are absent from the vanilla table; they must still get
    # a nation-specific voice instead of a generic court tone.
    sweden = seat_voice("Sweden")
    assert "Sweden" in sweden
    assert sweden != seat_voice("Mongolia")


def test_every_voice_refuses_to_mimic_the_interlocutor():
    # The Swedish leader answered the Tang emperor in Classical Chinese - the voice card must
    # forbid copying the other side's register.
    for civ in ("Sweden", "Sheng Tang", "china", "rome"):
        assert "never adopt" in seat_voice(civ).lower()


def test_era_lock_mentions_number():
    text = era_lock_instructions("Classical era", ERA_CLASSICAL)
    assert "Classical era" in text
    assert "eraNumber=1" in text
    assert "nuclear" in text.lower() or "Forbidden" in text


def test_classical_bans_nukes_keeps_safe_line():
    raw = "你们再不听话，我就扔核弹炸你们。还是先谈通商吧。"
    assert banned_hits(raw, ERA_CLASSICAL)
    cleaned = scrub_anachronisms(raw, ERA_CLASSICAL, "Simplified_Chinese")
    assert "核弹" not in cleaned
    assert "通商" in cleaned or "时运未至" in cleaned


def test_atomic_allows_nukes_word():
    raw = "Our nuclear arsenal is ready."
    assert not banned_hits(raw, ERA_ATOMIC)
    assert scrub_anachronisms(raw, ERA_ATOMIC, "English") == raw


def test_full_anachronism_falls_back():
    cleaned = scrub_anachronisms("I will nuke your capital.", ERA_CLASSICAL, "English")
    assert "nuke" not in cleaned.lower()
    assert "beyond our age" in cleaned.lower() or "armies" in cleaned.lower()


def test_normalize_era_tier_recognizes_modded_era_names():
    # Emperors and Deities prepends "Neolithic era", shifting every vanilla index by one.
    assert normalize_era_tier("Neolithic era", 0) == 0
    assert normalize_era_tier("Atomic era", 7) == ERA_ATOMIC
    assert normalize_era_tier("Information era", 8) == 7


def test_normalize_era_tier_scales_unknown_names_by_count():
    # 10 custom eras with unknown names: index 7 of 9 maps onto the vanilla 0..8 tier.
    assert normalize_era_tier("Age of Iron", 7, 10) == 6
    assert normalize_era_tier("Age of Iron", 9, 10) == 8


def test_normalize_era_tier_falls_back_to_raw_index():
    assert normalize_era_tier("", ERA_CLASSICAL) == ERA_CLASSICAL


def test_scrub_uses_normalized_tier_on_modded_eras():
    raw = "We will nuke you."
    # A mod index of 2 is still the medieval tier even though its own list is longer.
    assert "nuke" not in scrub_anachronisms(raw, 2, "English", "Medieval era", 10).lower()
    # ...but once the name says atomic, the word is legal regardless of index.
    assert scrub_anachronisms(raw, 2, "English", "Atomic era", 10) == raw
