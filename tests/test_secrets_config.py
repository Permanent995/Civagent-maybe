"""Sidecar secrets + localhost /v1/config (never GameInfo)."""

import importlib
import json
import os

import pytest


@pytest.fixture()
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("CIVAGENT_CONFIG_PATH", os.path.join("tests", "test_config.yaml"))
    monkeypatch.setenv("CIVAGENT_MEMORY_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("CIVAGENT_SECRETS_PATH", str(tmp_path / "secrets.json"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("LLM_DEFAULT_PROVIDER", raising=False)
    import deployment.ai.agent_api as module
    importlib.reload(module)
    return module


@pytest.fixture()
def client(api):
    api.app.config["TESTING"] = True
    return api.app.test_client()


def _status(resp):
    return resp.status_code, json.loads(resp.data.decode("utf-8") or "{}")


class TestSecretsConfig:
    def test_get_config_localhost(self, client, tmp_path):
        status, body = _status(client.get("/v1/config"))
        assert status == 200
        assert body["configured"] is False
        assert "apiKey" not in body
        assert str(tmp_path / "secrets.json") in body["path"]

    def test_post_saves_sidecar_and_healthz(self, client, monkeypatch, tmp_path):
        status, body = _status(client.post("/v1/config", json={
            "provider": "deepseek",
            "apiKey": "sk-test-abcdefgh",
            "baseUrl": "",
        }))
        assert status == 200
        assert body["ok"] is True
        assert body["configured"] is True
        assert body["keyHint"] == "••••efgh"
        assert "sk-test" not in json.dumps(body)
        saved = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
        assert saved["apiKey"] == "sk-test-abcdefgh"
        assert os.environ.get("DEEPSEEK_API_KEY") == "sk-test-abcdefgh"

        hz_status, hz = _status(client.get("/healthz"))
        assert hz_status == 200
        assert hz["llm"] == "ok"

    def test_explicit_empty_key_clears(self, client, tmp_path):
        client.post("/v1/config", json={"provider": "deepseek", "apiKey": "sk-keep-1234"})
        status, body = _status(client.post("/v1/config", json={"provider": "deepseek", "apiKey": ""}))
        assert status == 200
        saved = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
        assert saved["apiKey"] == ""
        assert body["configured"] is False

    def test_omit_key_keeps_previous(self, client, tmp_path):
        client.post("/v1/config", json={"provider": "deepseek", "apiKey": "sk-keep-9999"})
        status, body = _status(client.post("/v1/config", json={"provider": "openai"}))
        assert status == 200
        saved = json.loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
        assert saved["apiKey"] == "sk-keep-9999"
        assert saved["provider"] == "openai"
        assert body["keyHint"].endswith("9999")
