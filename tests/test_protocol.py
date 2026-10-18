"""Contract invariant I-2 (EXECUTION.md P2): the intent whitelist must be identical in
Python INTENT_TYPES, the frozen JSON schema, and the protocol README - enforced by test,
not by memory."""

import json
import os
import re

from civagent.utils.intent_models import INTENT_TYPES

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _schema_types():
    with open(os.path.join(REPO, "docs", "protocol", "intent.schema.json"), encoding="utf-8") as f:
        return set(json.load(f)["properties"]["type"]["enum"])


def _readme_types():
    with open(os.path.join(REPO, "docs", "protocol", "README.md"), encoding="utf-8") as f:
        text = f.read()
    match = re.search(r"## Intent 白名单（活人）\n\n(.*?)\n\n", text, re.DOTALL)
    assert match, "README Intent whitelist section not found"
    return set(re.findall(r"`([a-z_]+)`", match.group(1)))


def test_intent_types_match_schema():
    assert INTENT_TYPES == _schema_types(), (
        f"Python INTENT_TYPES vs intent.schema.json diverged: "
        f"only-python={set(INTENT_TYPES) - _schema_types()} only-schema={_schema_types() - set(INTENT_TYPES)}"
    )


def test_intent_types_match_readme():
    assert set(INTENT_TYPES) == _readme_types(), (
        f"Python INTENT_TYPES vs README diverged: "
        f"only-python={set(INTENT_TYPES) - _readme_types()} only-readme={_readme_types() - set(INTENT_TYPES)}"
    )


def test_common_enemy_removed_from_contract():
    assert "common_enemy" not in INTENT_TYPES
    assert "common_enemy" not in _schema_types()
    assert "common_enemy" not in _readme_types()
