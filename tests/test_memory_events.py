"""P-memory event stream + three-layer retrieval + trust traceability."""

import importlib
import json
import os

import pytest


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("CIVAGENT_CONFIG_PATH", os.path.join("tests", "test_config.yaml"))
    monkeypatch.setenv("CIVAGENT_MEMORY_ROOT", str(tmp_path / "data"))
    import deployment.ai.agent_api as module
    importlib.reload(module)
    module.app.config["TESTING"] = True
    return module


@pytest.fixture()
def client(api):
    return api.app.test_client()


def _slice(turn=10, new_events=None, civ="rome", egypt_at_war=False):
    return {
        "schemaVersion": 2, "gameId": "g-1", "civID": civ, "turn": turn,
        "gold": 50, "goldPerTurn": 2.0, "happiness": 1, "resources": {},
        "knownCivs": [{"civID": "egypt", "relationship": "Neutral", "atWar": egypt_at_war}],
        "cities": [], "newEvents": new_events or [],
    }


def _canned_llm(api, monkeypatch, intents=None):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")
    canned = {"intents": intents or []}
    monkeypatch.setattr(
        api.llm_router, "chat",
        lambda payload, **kwargs: ({"role": "user", "content": json.dumps(canned)}, {}),
    )


class TestEngineEventIngestion:
    def test_new_events_stored_as_engine_event_records(self, api, client, monkeypatch, tmp_path):
        _canned_llm(api, monkeypatch)
        events = [
            {"turn": 9, "category": "War", "text": "[Egypt] has declared war on [Rome]!"},
            {"turn": 10, "category": "Trade", "text": "[Rome] accepted a trade from [Egypt]"},
        ]
        status, _ = _status(client.post("/v1/decision", json=_slice(turn=10, new_events=events)))
        assert status == 200
        memory = tmp_path / "data" / "g-1" / "rome.jsonl"
        records = [json.loads(line) for line in memory.read_text(encoding="utf-8").splitlines()]
        engine_events = [r for r in records if r.get("kind") == "engine_event"]
        assert len(engine_events) == 2
        assert engine_events[0]["turn"] == 9 and engine_events[0]["category"] == "War"

    def test_no_events_field_is_fine(self, api, client, monkeypatch):
        _canned_llm(api, monkeypatch)
        status, _ = _status(client.post("/v1/decision", json=_slice()))
        assert status == 200


class TestSelectMemory:
    def test_salient_layer_survives_long_games(self, api):
        """A war from turn 9 must surface at turn 100 - the fixed window would have dropped it."""
        for turn in range(1, 60):
            api.append_memory("g-1", "rome", {"kind": "decision", "turn": turn, "intents": []})
        api.append_memory("g-1", "rome", {
            "kind": "engine_event", "turn": 9, "category": "War",
            "text": "[Egypt] has declared war on [Rome]!",
        })
        lines = api.select_memory("g-1", "rome", turn=100)
        assert any("declared war" in line and "turn 9" in line for line in lines)

    def test_layers_deduplicated_and_recent_included(self, api):
        api.append_memory("g-1", "rome", {
            "kind": "engine_event", "turn": 50, "category": "War",
            "text": "border clash",
        })
        api.append_memory("g-1", "rome", {"kind": "decision", "turn": 51, "intents": []})
        lines = api.select_memory("g-1", "rome", turn=51)
        assert len(lines) == len(set(lines))  # no duplicates
        assert any("turn 51" in line for line in lines)  # recent layer present
        assert any("border clash" in line for line in lines)  # salient layer present

    def test_interlocutor_layer(self, api):
        api.append_memory("g-1", "rome", {"kind": "chat_note", "turn": 2, "about": "egypt said hello"})
        api.append_memory("g-1", "rome", {"kind": "chat_note", "turn": 2, "about": "greece said hi"})
        # push both old records out of the recent window
        for turn in range(3, 25):
            api.append_memory("g-1", "rome", {"kind": "decision", "turn": turn, "intents": []})
        lines = api.select_memory("g-1", "rome", turn=24, interlocutor="egypt")
        assert any("egypt said hello" in line for line in lines)  # interlocutor layer
        assert not any("greece said hi" in line for line in lines)  # not recent, not interlocutor


class TestTrustTraceability:
    def test_bump_trust_writes_reason_and_evidence(self, api, tmp_path):
        api.trust_store.bump_trust(
            api.MEMORY_ROOT, "g-1", "rome", "egypt", 0.05,
            reason="claim verified", evidence="claim @t5: Egypt will attack (turn 5)",
        )
        log_path = tmp_path / "data" / "g-1" / "trust_log.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        assert entry["reason"] == "claim verified"
        assert "Egypt will attack" in entry["evidence"]
        assert entry["delta"] == 0.05

    def test_verification_evidence_lands_in_log(self, api, client, monkeypatch, tmp_path):
        """The W5.7 verified claim now carries engine evidence into the trust log."""
        api.trust_store.append_claim(
            api.MEMORY_ROOT, "g-1", from_civ="egypt", to_civ="rome",
            claim="Egypt will attack someone", kind="war", about_civ="egypt", turn=5,
        )
        _canned_llm(api, monkeypatch)
        events = [{"turn": 8, "category": "War", "text": "[Egypt] has declared war on [Rome]!"}]
        client.post("/v1/decision", json=_slice(turn=8, new_events=events, egypt_at_war=True))
        log_path = tmp_path / "data" / "g-1" / "trust_log.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        assert entry["reason"] == "claim verified"
        assert entry["delta"] == 0.05

    def test_trust_endpoint_exposes_table_and_log(self, api, client, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")
        api.trust_store.bump_trust(api.MEMORY_ROOT, "g-1", "rome", "egypt", 0.05, reason="claim verified")
        status, body = _status(client.get("/v1/trust?gameId=g-1&civID=rome"))
        assert status == 200
        assert body["trust"] == {"egypt": pytest.approx(0.45)}
        assert body["log"][-1]["reason"] == "claim verified"

    def test_trust_endpoint_requires_params(self, client):
        status, _ = _status(client.get("/v1/trust"))
        assert status == 400


def _status(response):
    return response.status_code, response.get_json()
