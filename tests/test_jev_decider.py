"""jevos adapter: real HTTP sockets, no model download, no network beyond loopback."""

import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest


@pytest.fixture()
def jev(monkeypatch):
    for key in ("CIVAGENT_JEV_GATE", "CIVAGENT_JEV_URL", "CIVAGENT_JEV_KEY",
                "CIVAGENT_JEV_THRESHOLD", "CIVAGENT_JEV_TIMEOUT", "JEV_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    import civagent.utils.jev_decider as module

    importlib.reload(module)
    return module


class _FakeJeos:
    """Minimal /v1/systemone + /health implementation with request capture."""

    def __init__(self, probabilities, health_status=200, omit_answers=()):
        self.requests: list = []
        self._probabilities = probabilities
        self._health_status = health_status
        self._omit = set(omit_answers)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health":
                    self._send(outer._health_status, {"ok": outer._health_status == 200, "model": "jevos-v5"})
                else:
                    self._send(404, {})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append({
                    "path": self.path, "body": body,
                    "auth": self.headers.get("Authorization"),
                })
                answers = {}
                for index, qid in enumerate(sorted(body.get("questions", {}))):
                    if qid in outer._omit:
                        continue
                    answers[qid] = {"type": "noul", "noul": outer._probabilities[index]}
                self._send(200, {"model": "jevos-v5", "answers": answers, "usage": {}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake_jev():
    servers = []

    def start(probabilities, **kwargs):
        server = _FakeJeos(probabilities, **kwargs)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def _intent(kind, target=None):
    return SimpleNamespace(type=kind, targetCivID=target)


SLICE = {
    "turn": 12, "civID": "mongolia", "gold": 100, "goldPerTurn": 5.0,
    "sciencePerTurn": 4.0, "culturePerTurn": 2.0, "happiness": 3,
    "era": "Classical era", "eraNumber": 1, "factionBalance": 20,
    "cities": [{"name": "Karakorum", "population": 5,
                "currentConstruction": "Temple", "constructionTurnsLeft": 3}],
    "knownCivs": [
        {"civID": "Sheng Tang", "relationship": "Neutral", "atWar": True},
        {"civID": "Egypt", "relationship": "Friendly", "atWar": False},
    ],
}


class TestConfig:
    def test_disabled_by_default(self, jev):
        assert jev.enabled() is False
        assert jev.url() == "http://127.0.0.1:8017"

    def test_env_switches(self, jev, monkeypatch):
        monkeypatch.setenv("CIVAGENT_JEV_GATE", "1")
        monkeypatch.setenv("CIVAGENT_JEV_THRESHOLD", "0.7")
        monkeypatch.setenv("CIVAGENT_JEV_URL", "http://127.0.0.1:9001/")
        assert jev.enabled() is True
        assert jev.threshold() == 0.7
        assert jev.url() == "http://127.0.0.1:9001"

    def test_threshold_is_parsed_defensively(self, jev, monkeypatch):
        monkeypatch.setenv("CIVAGENT_JEV_THRESHOLD", "nonsense")
        assert jev.threshold() == jev.DEFAULT_THRESHOLD
        monkeypatch.setenv("CIVAGENT_JEV_THRESHOLD", "3")
        assert jev.threshold() == 1.0


class TestState:
    def test_contains_the_facts_a_decision_needs(self, jev):
        state = jev.state_from_slice(SLICE)
        for fragment in ("Turn 12", "mongolia", "Classical era", "Gold 100",
                         "Karakorum", "Temple", "At war with: Sheng Tang",
                         "Faction balance"):
            assert fragment in state

    def test_missing_fields_do_not_crash(self, jev):
        state = jev.state_from_slice({"turn": 1, "civID": "rome"})
        assert "Turn 1" in state and "rome" in state
        assert "nobody" in state


class TestGate:
    def test_keeps_intents_above_threshold(self, jev, fake_jev, monkeypatch):
        server = fake_jev([0.9, 0.2, 0.6])
        monkeypatch.setenv("CIVAGENT_JEV_GATE", "1")
        monkeypatch.setenv("CIVAGENT_JEV_URL", server.url)
        monkeypatch.setenv("CIVAGENT_JEV_THRESHOLD", "0.5")
        intents = [_intent("make_peace", "Sheng Tang"), _intent("declare_war", "Egypt"),
                   _intent("share_intel", "Egypt")]
        kept, report = jev.gate_intents(jev.state_from_slice(SLICE), intents)
        assert [i.type for i in kept] == ["make_peace", "share_intel"]
        assert report["asked"] == 3 and report["dropped"] == 1
        assert report["probabilities"]["make_peace->Sheng Tang"] == 0.9
        # one request, all questions, TypeSafe-Jev wire format
        assert len(server.requests) == 1
        sent = server.requests[0]["body"]
        assert sent["model"] == "jev-latest"
        assert "Turn 12" in sent["state"]
        assert all(q["type"] == "noul" for q in sent["questions"].values())

    def test_unreachable_server_passes_everything_through(self, jev, monkeypatch):
        monkeypatch.setenv("CIVAGENT_JEV_GATE", "1")
        monkeypatch.setenv("CIVAGENT_JEV_URL", "http://127.0.0.1:59999")
        intents = [_intent("declare_war", "Egypt")]
        kept, report = jev.gate_intents("state", intents)
        assert kept == intents
        assert "not reachable" in report["skipped"]

    def test_unhealthy_server_passes_everything_through(self, jev, fake_jev, monkeypatch):
        server = fake_jev([0.0], health_status=503)
        monkeypatch.setenv("CIVAGENT_JEV_GATE", "1")
        monkeypatch.setenv("CIVAGENT_JEV_URL", server.url)
        intents = [_intent("declare_war", "Egypt")]
        kept, report = jev.gate_intents("state", intents)
        assert kept == intents
        assert not server.requests  # /health only, never /v1/systemone

    def test_missing_verdict_is_not_a_rejection(self, jev, fake_jev, monkeypatch):
        server = fake_jev([0.0], omit_answers=("i0",))
        monkeypatch.setenv("CIVAGENT_JEV_GATE", "1")
        monkeypatch.setenv("CIVAGENT_JEV_URL", server.url)
        intents = [_intent("make_peace", "Egypt")]
        kept, report = jev.gate_intents("state", intents)
        assert kept == intents
        assert report["probabilities"] == {}

    def test_intents_beyond_the_cap_are_untouched(self, jev, fake_jev, monkeypatch):
        server = fake_jev([0.9] * jev.MAX_QUESTIONS)
        monkeypatch.setenv("CIVAGENT_JEV_GATE", "1")
        monkeypatch.setenv("CIVAGENT_JEV_URL", server.url)
        intents = [_intent("share_intel", f"civ{i}") for i in range(jev.MAX_QUESTIONS + 2)]
        kept, report = jev.gate_intents("state", intents)
        assert len(kept) == len(intents)
        assert report["asked"] == jev.MAX_QUESTIONS
        assert len(server.requests[0]["body"]["questions"]) == jev.MAX_QUESTIONS

    def test_api_key_is_sent_as_bearer(self, jev, fake_jev, monkeypatch):
        server = fake_jev([0.9])
        monkeypatch.setenv("CIVAGENT_JEV_GATE", "1")
        monkeypatch.setenv("CIVAGENT_JEV_URL", server.url)
        monkeypatch.setenv("CIVAGENT_JEV_KEY", "jev-secret")
        jev.gate_intents("state", [_intent("make_peace", "Egypt")])
        assert server.requests[0]["auth"] == "Bearer jev-secret"
