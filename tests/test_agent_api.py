"""W2-Python acceptance tests - decision loop mocked, no network, no Redis, no JVM."""

import importlib
import json
import os

import pytest


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("CIVAGENT_CONFIG_PATH", os.path.join("tests", "test_config.yaml"))
    monkeypatch.setenv("CIVAGENT_MEMORY_ROOT", str(tmp_path / "data"))
    # civagent.config caches config_path/config_data at import time; without this reload the
    # machine's ~/.civagent/secrets.json would leak a real key into 'unconfigured' tests.
    import civagent.config as config_module
    importlib.reload(config_module)
    # llm_router binds config_data at import time; reload it or it keeps a stale (real) config.
    import civagent.utils.llm_router as llm_router_module
    importlib.reload(llm_router_module)
    import deployment.ai.agent_api as module
    importlib.reload(module)
    return module


@pytest.fixture()
def client(api):
    api.app.config["TESTING"] = True
    return api.app.test_client()


def valid_slice():
    return {
        "schemaVersion": 2,
        "gameId": "g-123",
        "civID": "rome",
        "turn": 12,
        "gold": 100,
        "goldPerTurn": 5.0,
        "happiness": 3,
        "resources": {"Iron": 2},
        "knownCivs": [{"civID": "egypt", "relationship": "Neutral", "atWar": False, "era": "Classical era"}],
        "cities": [],
        "era": "Classical era",
        "eraNumber": 1,
    }


class TestHealthz:
    def test_missing_key_is_explicit_503(self, api, client, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        status, body = _status(client.get("/healthz"))
        assert status == 503
        assert body["llm"] == "missing"
        assert "DEEPSEEK_API_KEY" in body["reason"]

    def test_configured_reports_ok(self, api, client, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")
        status, body = _status(client.get("/healthz"))
        assert status == 200
        assert body == {"llm": "ok", "provider": "deepseek", "local": None}

    def test_local_server_down_is_503_not_ok(self, api, client, monkeypatch):
        """GPU box off / model not pulled: the old code reported ok and the game saw native AI."""
        monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "ollama")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
        monkeypatch.setattr(api.llm_router, "local_server_problem",
                            lambda provider=None, country=None: "nothing answered at http://127.0.0.1:11434/v1")
        status, body = _status(client.get("/healthz"))
        assert status == 503
        assert body["llm"] == "offline"
        assert "nothing answered" in body["reason"]

    def test_unbuildable_client_reports_broken(self, api, client, monkeypatch):
        """A valid key with a missing package must not report healthy (the `instructor` bug)."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")

        def boom(base_url, api_key):
            raise ModuleNotFoundError("No module named 'instructor'")

        monkeypatch.setattr(api.llm_router, "_make_instructor_client", boom)
        status, body = _status(client.get("/healthz"))
        assert status == 503
        assert body["llm"] == "broken"
        assert "instructor" in body["reason"]


class TestDecision:
    def test_missing_fields_rejected_loudly(self, client):
        status, body = _status(client.post("/v1/decision", json={"civID": "rome", "gold": 1}))
        assert status == 400
        assert "missing required fields" in body["error"]
        assert "gameId" in body["error"]

    def test_decision_returns_whitelisted_intents(self, api, client, monkeypatch, tmp_path):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")
        canned = {"intents": [
            {"type": "declare_war", "actorCivID": "rome", "targetCivID": "egypt", "chatText": "Enough."},
            {"type": "civ addstat", "actorCivID": "rome"},  # must be dropped by whitelist
        ]}
        monkeypatch.setattr(
            api.llm_router, "chat",
            lambda payload, **kwargs: ({"role": "user", "content": json.dumps(canned)}, {}),
        )
        status, body = _status(client.post("/v1/decision", json=valid_slice()))
        assert status == 200
        assert len(body["intents"]) == 1
        assert body["intents"][0]["type"] == "declare_war"
        # memory file written for the seat
        memory = tmp_path / "data" / "g-123" / "rome.jsonl"
        assert memory.exists()
        assert "declare_war" in memory.read_text(encoding="utf-8")

    def test_llm_failure_is_a_500_not_silent_empty(self, api, client, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")

        def boom(payload, **kwargs):
            raise RuntimeError("LLM provider 'deepseek' not configured")

        monkeypatch.setattr(api.llm_router, "chat", boom)
        status, body = _status(client.post("/v1/decision", json=valid_slice()))
        assert status == 500


class TestApplied:
    def test_applied_logged_to_seat_memory(self, api, client, tmp_path):
        status, body = _status(client.post("/v1/applied", json={
            "gameId": "g-123", "civID": "rome",
            "applied": [{"type": "declare_war", "ok": True}],
            "fellBackToNative": False,
        }))
        assert status == 200
        memory = tmp_path / "data" / "g-123" / "rome.jsonl"
        assert memory.exists()
        assert '"kind": "applied"' in memory.read_text(encoding="utf-8")

    def test_applied_missing_fields_rejected(self, client):
        status, _ = _status(client.post("/v1/applied", json={"gameId": "g"}))
        assert status == 400


def _status(response):
    return response.status_code, response.get_json()


class TestPromptViewer:
    """The in-game viewer must show exactly what the LLM saw, plus cost hints."""

    def test_decision_prompt_is_captured_with_tokens_and_sampling(self, api, client, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")
        monkeypatch.setattr(
            api.llm_router, "chat", lambda payload, **kw: ({"content": '{"intents": []}'}, {})
        )
        status, _ = _status(client.post("/v1/decision", json=valid_slice()))
        assert status == 200

        status, body = _status(client.get("/v1/prompt?gameId=g-123&civID=rome"))
        assert status == 200
        assert body["kind"] == "decision"
        assert body["promptTokens"] > 0
        assert body["system"] and body["user"]
        assert "sampling" in body and "model" in body

    def test_missing_prompt_is_a_404(self, client):
        status, _ = _status(client.get("/v1/prompt?gameId=nope&civID=nobody"))
        assert status == 404


def test_sampling_params_forwards_only_what_is_set(api, tmp_path):
    import civagent.config as config_module

    llm = config_module.config_data.setdefault("LLM", {})
    llm["temperature"] = 0.7
    llm["max_tokens"] = 512
    llm["frequency_penalty"] = 0.3
    params = api.llm_router.sampling_params()
    assert params == {"temperature": 0.7, "max_tokens": 512, "frequency_penalty": 0.3}
    # unset keys are omitted so the provider default still applies
    llm.pop("temperature")
    assert "temperature" not in api.llm_router.sampling_params()


def test_estimate_tokens_is_cjk_aware(api):
    assert api.llm_router.estimate_tokens("") == 0
    # CJK is roughly one token per character, ASCII roughly four characters per token
    assert api.llm_router.estimate_tokens("太宗近来可好") >= 6
    assert api.llm_router.estimate_tokens("a" * 400) == 100


class TestTradeState:
    """Q: pass the inter-civ trade statistics/deals too. (Notifications are AI-ignored by Unciv,
    so this reads the live structures: DiplomacyManager.trades / diplomaticModifiers / tradeRequests.)"""

    def _slice(self):
        return {
            "gameId": "g-deal", "civID": "rome", "gold": 100, "goldPerTurn": 3, "happiness": 2,
            "resources": {"Iron": 2}, "resourceKinds": {"Iron": "strategic"},
            "knownCivs": [{
                "civID": "player1", "atWar": False, "gold": 50,
                "hasOpenBorders": True,
                "agreements": ["we give [Gold_Per_Turn x5 (20 turns left)] / they give [Research_Agreement]"],
                "modifiers": ["Our open borders have brought us closer together. (5)"],
                "resources": {"Dyes": 1},
            }],
            "incomingTradeRequests": ["from egypt: gives [Gold x20] / wants [Iron x1]"],
        }

    def test_trade_state_renders_deals_modifiers_and_offers(self, api):
        line = api._trade_state_line("g-deal", "rome", slice_data=self._slice())
        assert "open borders" in line
        assert "signed deal" in line and "Research_Agreement" in line
        assert "closer together" in line
        assert "offers awaiting our answer" in line and "egypt" in line

    def test_trade_state_is_honest_without_a_slice(self, api):
        assert "not observed" in api._trade_state_line("nope", "rome")

    def test_chat_prompt_carries_the_trade_state(self, api):
        api._remember_slice(self._slice())
        user = api._chat_prompt("g-deal", "player1", "rome", "let us trade", "English", "Classical era", 1)[-1]["content"]
        assert "TRADE/DEAL STATE" in user
        assert "Research_Agreement" in user
        assert "hasOpenBorders" not in user  # rendered as prose, not raw JSON keys


class TestChatEconomy:
    """Q: the model only said "trade ivory", never amounts - the chat prompt had no economy at all."""

    def test_chat_prompt_quotes_the_real_economy(self, api):
        api._remember_slice({
            "gameId": "g-eco", "civID": "rome", "gold": 250, "goldPerTurn": 7.0, "happiness": 4,
            "resources": {"Iron": 3}, "resourceKinds": {"Iron": "strategic"},
            "knownCivs": [{"civID": "player1", "atWar": False, "gold": 90}],
        })
        user = api._chat_prompt("g-eco", "player1", "rome", "let us trade", "English", "Classical era", 1)[-1]["content"]
        assert "gold=250" in user
        assert "Iron=3" in user and "strategic" in user
        assert "player1 treasury" in user and "90" in user
        assert "TRADING RULES" in user
        assert "propose_trade" in user  # exact payload shape with amounts

    def test_economy_line_is_honest_without_a_slice(self, api):
        line = api._economy_line("nope", "rome")
        assert "not observed" in line and "do NOT invent" in line


class TestShutdown:
    """The dialogue "+" menu / Options restart the brain through this endpoint."""

    def test_shutdown_asks_the_brain_to_stop(self, api, client, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(api, "_shutdown_process", lambda: called.__setitem__("n", called["n"] + 1))
        status, body = _status(client.post("/v1/shutdown", json={}))
        assert status == 200
        assert body.get("ok") is True

    def test_shutdown_rejects_non_localhost(self, api, client, monkeypatch):
        monkeypatch.setattr(api, "_shutdown_process", lambda: None)
        monkeypatch.setattr(api, "_is_localhost", lambda: False)
        status, _ = _status(client.post("/v1/shutdown", json={}))
        assert status == 403


class TestModdedRulesetPrompt:
    """M-content-1: the brain must know it may be in a modded ruleset."""

    def test_prompt_names_the_modded_ruleset_and_resource_kinds(self, api):
        slice_data = valid_slice()
        slice_data["ruleset"] = {
            "name": "Emperors and Deities",
            "isBaseRuleset": True,
            "mods": ["Emperors and Deities"],
            "fingerprint": "deadbeefdeadbeef",
        }
        slice_data["resourceKinds"] = {"Jade": "luxury", "Bronze": "strategic"}
        user = api.build_prompt(slice_data, [])[-1]["content"]
        assert "Emperors and Deities" in user
        assert "Jade" in user and "luxury" in user

    def test_prompt_is_safe_without_ruleset_section(self, api):
        user = api.build_prompt(valid_slice(), [])[-1]["content"]
        assert "Ruleset:" in user


class TestPersonalityLock:
    """P4: the engine's nation personality (slice) wins; config only overrides."""

    def test_slice_personality_wins(self, api):
        slice_data = valid_slice()
        slice_data["personality"] = "declarewar 8, loyal 3"
        system = api.build_prompt(slice_data, [])[0]["content"]
        assert "declarewar 8, loyal 3 (engine nation personality)" in system

    def test_config_fallback_when_slice_neutral_or_absent(self, api, monkeypatch):
        slice_data = valid_slice()
        slice_data["personality"] = "neutral"
        system = api.build_prompt(slice_data, [])[0]["content"]
        assert "pragmatic ruler" in system  # config default, not the neutral marker

        slice_data["personality"] = None
        system = api.build_prompt(slice_data, [])[0]["content"]
        assert "pragmatic ruler" in system
