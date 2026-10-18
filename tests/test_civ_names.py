"""Mod-safe civ display names (D2): a non-vanilla nation must never raise KeyError.

Mods such as Emperors and Deities ship dozens of custom nations; the Chinese display
table only covers the six vanilla ones, so lookups must fall back to the in-game name.

The leaf module is loaded directly because ``civagent.action_space.__init__`` pulls the
legacy civsim/llama_index stack, which is absent from the light test environment.
"""

import importlib.util
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parents[1] / "civagent" / "action_space"
_spec = importlib.util.spec_from_file_location("civ_names_under_test", _PKG_DIR / "civ_names.py")
assert _spec and _spec.loader
civ_names = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(civ_names)
civ_display_name = civ_names.civ_display_name


def test_vanilla_names_are_translated():
    assert civ_display_name("china") == "中国"
    assert civ_display_name("China") == "中国"
    assert civ_display_name("  rome  ") == "罗马"


def test_modded_nation_falls_back_to_raw_name():
    assert civ_display_name("Etruria") == "Etruria"
    assert civ_display_name("Dai Viet") == "Dai Viet"
    assert civ_display_name("Srivijaya") == "Srivijaya"


def test_missing_or_none_is_safe():
    assert civ_display_name(None) == ""
    assert civ_display_name("") == ""


def test_action_space_package_exports_the_helper():
    init_text = (_PKG_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "civ_display_name" in init_text
