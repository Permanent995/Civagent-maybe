"""W3 chat acceptance tests (M2.5 Python side) - LLM mocked, no network."""

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
    module.app.config["TESTING"] = True
    return module


@pytest.fixture()
def client(api):
    return api.app.test_client()


def _parse_sse(response):
    events = []
    if response.status_code != 200:
        return events
    for block in response.get_data(as_text=True).split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _wait_for_reply(api, client, game_id="g-1", civ="player1", tries=50):
    import time
    for _ in range(tries):
        events = _parse_sse(client.get(f"/events?gameId={game_id}&civID={civ}&after=0"))
        if events:
            return events
        time.sleep(0.1)
    return []


def _status(response):
    return response.status_code, response.get_json()


class TestMessageEndpoint:
    def test_missing_fields_rejected(self, client):
        status, body = _status(client.post("/message", json={"text": "hi"}))
        assert status == 400
        assert "missing fields" in body["error"]

    def test_empty_text_rejected(self, client):
        status, _ = _status(client.post("/message", json={
            "gameId": "g", "fromCiv": "rome", "toCiv": "egypt", "text": "  "}))
        assert status == 400

    def test_message_returns_started_immediately(self, client):
        status, body = _status(client.post("/message", json={
            "gameId": "g", "fromCiv": "player1", "toCiv": "rome", "text": "hello there"}))
        assert status == 200
        assert body == {"started": True}


class TestChatFlow:
    def _mock_llm(self, api, monkeypatch, reply):
        captured = {}

        def fake_chat(payload, **kwargs):
            captured["payload"] = payload
            captured["kwargs"] = kwargs
            return {"role": "user", "content": json.dumps({"reply": reply})}, {}

        monkeypatch.setattr(api.llm_router, "chat", fake_chat)
        return captured

    def test_reply_stable_and_fence_extracted_as_draft(self, api, client, monkeypatch, tmp_path):
        reply = (
            "Peace? Only if you pay. ```intent\n"
            '{"type": "propose_trade", "actorCivID": "rome", "targetCivID": "player1"}\n```'
        )
        captured = self._mock_llm(api, monkeypatch, reply)
        status, _ = _status(client.post("/message", json={
            "gameId": "g-1", "fromCiv": "player1", "toCiv": "rome",
            "text": "Give me peace", "language": "English"}))
        assert status == 200

        events = _wait_for_reply(api, client)
        assert len(events) == 1
        event = events[0]
        assert event["status"] == "stable"
        assert "```" not in event["text"]
        assert "Peace? Only if you pay." in event["text"]
        assert len(event["intents"]) == 1
        assert event["intents"][0]["type"] == "propose_trade"  # DRAFT - not executed here

        # transcript contains both sides, fence stripped from the display text
        status, body = _status(client.get("/messages?gameId=g-1&civ1=player1&civ2=rome"))
        assert status == 200
        texts = [m["text"] for m in body["messages"]]
        assert "Give me peace" in texts
        assert any("Peace? Only if you pay." in t for t in texts)
        assert not any("```" in t for t in texts)

        # language is locked into the prompt (W3.4)
        system = captured["payload"]["messages"][0]["content"]
        assert "LANGUAGE RULE: reply ONLY in English" in system
        assert "ERA LOCK" in system
        assert "VOICE:" in system
        assert "THIRD-PARTY INTEL" in system

    def test_classical_anachronism_scrubbed_from_reply(self, api, client, monkeypatch):
        reply = "Surrender or I nuke your capital. Trade ivory instead."
        self._mock_llm(api, monkeypatch, reply)
        client.post("/message", json={
            "gameId": "g-era", "fromCiv": "player1", "toCiv": "rome",
            "text": "Threaten me", "language": "English",
            "era": "Classical era", "eraNumber": 1,
        })
        events = _wait_for_reply(api, client, game_id="g-era")
        assert events
        assert "nuke" not in events[0]["text"].lower()
        assert "ivory" in events[0]["text"].lower() or "beyond our age" in events[0]["text"].lower()

    def test_non_whitelisted_fence_dropped(self, api, client, monkeypatch):
        reply = 'No. ```intent\n{"type": "civ addstat", "actorCivID": "rome"}\n```'
        self._mock_llm(api, monkeypatch, reply)
        client.post("/message", json={
            "gameId": "g-2", "fromCiv": "player1", "toCiv": "rome", "text": "gold pls"})
        events = _wait_for_reply(api, client, game_id="g-2")
        assert events[0]["intents"] == []

    def test_llm_failure_becomes_error_event(self, api, client, monkeypatch):
        def boom(payload, **kwargs):
            raise RuntimeError("LLM provider 'deepseek' not configured: no api key")
        monkeypatch.setattr(api.llm_router, "chat", boom)
        client.post("/message", json={
            "gameId": "g-3", "fromCiv": "player1", "toCiv": "rome", "text": "hi"})
        events = _wait_for_reply(api, client, game_id="g-3")
        assert events[0]["kind"] == "error"
        assert "sk-" not in events[0]["text"]


class TestClaimVerificationHook:
    """W5.7: player claims are recorded with a structured kind/about and later verified by the slice."""

    def _mock_decision(self, api, monkeypatch):
        monkeypatch.setattr(
            api.llm_router, "chat",
            lambda payload, **kwargs: ({"content": json.dumps({"intents": []})}, {}),
        )

    def _decision_slice(self, turn, known, borders):
        return {
            "schemaVersion": 2, "gameId": "g-claim", "civID": "rome", "turn": turn,
            "gold": 100, "goldPerTurn": 5, "happiness": 3, "resources": {},
            "knownCivs": known, "borderThreats": borders,
        }

    def test_player_claim_kind_recorded_and_verified(self, api, client, monkeypatch, tmp_path):
        import time

        self._mock_decision(api, monkeypatch)
        client.post("/message", json={
            "gameId": "g-claim", "fromCiv": "player1", "toCiv": "rome",
            "text": "intel for you", "language": "English", "turn": 5,
            "shareIntelClaim": "埃及在北边囤兵", "shareIntelAboutCiv": "egypt",
        })
        claims_path = tmp_path / "data" / "g-claim" / "claims.jsonl"
        for _ in range(50):
            if claims_path.exists() and "player_share_intel" in claims_path.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        records = [json.loads(l) for l in claims_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        claim = next(r for r in records if r.get("source") == "player_share_intel")
        assert claim["kind"] == "massing_troops"  # classified from free text
        assert claim["aboutCiv"] == "egypt"       # structured, not baked into the text
        assert claim["turn"] == 5                  # window anchor

        before = api.trust_store.get_trust(api.MEMORY_ROOT, "g-claim", "rome", "player1")
        status, _ = _status(client.post("/v1/decision", json=self._decision_slice(
            7,
            [{"civID": "egypt", "atWar": False}],
            [{"civID": "egypt", "level": "medium", "visibleEnemyBucket": "1-2"}],
        )))
        assert status == 200
        after = api.trust_store.get_trust(api.MEMORY_ROOT, "g-claim", "rome", "player1")
        assert after > before  # seeing the troops verifies the player's claim


class TestStatusAndGreeting:
    def test_status_reports_llm_state(self, api, client, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")
        status, body = _status(client.get("/status"))
        assert status == 200
        assert body["llm"] == "ok"
        assert body["provider"] == "deepseek"
        assert "active_seats" in body

    def test_greeting_needs_no_llm(self, api, client):
        """M2.5: the opening line is the native neutralHello - served with the LLM down."""
        status, body = _status(client.get("/messages?gameId=none&civ1=a&civ2=b"))
        assert status == 200
        assert body == {"messages": []}
        # and healthz still works with no key configured
        status, body = _status(client.get("/healthz"))
        assert status == 503  # LLM missing, but the greeting path never calls it
