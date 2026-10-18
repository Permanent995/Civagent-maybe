"""W1b acceptance tests (EXECUTION.md M1.5) - all mocked, no network."""

import importlib
import os
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_BASE_URL",
        "LLM_DEFAULT_PROVIDER", "OLLAMA_BASE_URL", "CIVAGENT_LOCAL_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    # Port scanning must not depend on what the developer happens to have running.
    import civagent.utils.local_models as local_models

    monkeypatch.setattr(local_models, "discover_cached", lambda ttl=0: [])
    local_models.clear_cache()


def _reload_router():
    import civagent.config as config_module
    importlib.reload(config_module)
    import civagent.utils.llm_router as module
    importlib.reload(module)
    return module


@pytest.fixture()
def router(monkeypatch):
    monkeypatch.setenv("CIVAGENT_CONFIG_PATH", os.path.join("tests", "test_config.yaml"))
    return _reload_router()


def test_chat_stream_text_accumulates_plain_text_deltas(router, monkeypatch):
    """Plan A: streaming bypasses instructor/JSON and hands back plain prose."""
    import openai

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")

    class _Delta:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    class _Completions:
        def create(self, **kwargs):
            assert kwargs.get("stream") is True, "must ask the provider to stream"
            assert "response_model" not in kwargs, "streaming must not use instructor's JSON mode"
            return [_Chunk("Hello"), _Chunk(" there")]

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, **kwargs):
            self.chat = _Chat()

    monkeypatch.setattr(openai, "OpenAI", _Client)
    seen: list = []
    text, raw = router.chat_stream_text({"messages": []}, country="rome", on_delta=seen.append)
    assert text == "Hello there"
    assert seen[-1] == "Hello there"   # the caller can render a live bubble
    assert raw.get("streamed") is True


class TestKeySlots:
    def test_env_overrides_config(self, router, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env-12345678")
        assert router.get_provider_config("deepseek")["api_key"] == "sk-from-env-12345678"

    def test_config_fallback_and_env_wins(self, router, monkeypatch, tmp_path):
        cfg_file = tmp_path / "cfg.yaml"
        cfg_file.write_text("LLM:\n  deepseek_api_key: sk-from-config-12345\n", encoding="utf-8")
        monkeypatch.setenv("CIVAGENT_CONFIG_PATH", str(cfg_file))
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        router = _reload_router()
        assert router.get_provider_config("deepseek")["api_key"] == "sk-from-config-12345"
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env-99999")
        router = _reload_router()
        assert router.get_provider_config("deepseek")["api_key"] == "sk-from-env-99999"

    def test_empty_key_means_unconfigured(self, router):
        assert router.get_provider_config("anthropic")["api_key"] == ""
        assert router.provider_missing_reason("anthropic") == "no api key configured (set ANTHROPIC_API_KEY)"

    def test_gemini_dual_env_slot(self, router, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
        assert router.get_provider_config("gemini")["api_key"] == "google-key"
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        monkeypatch.delenv("GOOGLE_API_KEY")
        assert router.get_provider_config("gemini")["api_key"] == "gemini-key"

    def test_openai_compatible_needs_base_url(self, router, monkeypatch):
        reason = router.provider_missing_reason("openai_compatible")
        assert reason is not None and reason.startswith("OPENAI_BASE_URL is not set")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example/v1")
        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        assert router.provider_missing_reason("openai_compatible") is None

    def test_loopback_base_url_needs_no_key(self, router, monkeypatch):
        """vLLM / LM Studio / Ollama do not authenticate: requiring a key blocked every local user."""
        monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
        assert router.provider_missing_reason("openai_compatible") is None

    def test_discovered_local_server_supplies_the_base_url(self, router, monkeypatch):
        import civagent.utils.local_models as local_models

        monkeypatch.setattr(
            local_models, "discover_cached",
            lambda ttl=0: [{"kind": "vllm", "base_url": "http://127.0.0.1:8000/v1", "models": ["qwen3"]}],
        )
        assert router.get_provider_config("openai_compatible")["base_url"] == "http://127.0.0.1:8000/v1"

    def test_ollama_needs_no_key(self, router):
        assert router.provider_missing_reason("ollama") is None

    def test_default_provider_env_wins(self, router, monkeypatch):
        monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "gemini")
        assert router.default_provider() == "gemini"
        monkeypatch.delenv("LLM_DEFAULT_PROVIDER")
        assert router.default_provider() == "deepseek"


class TestProviderResolution:
    @pytest.mark.parametrize("model,expected", [
        ("deepseek", "deepseek"),
        ("deepseek-chat", "deepseek"),
        ("gpt-4o", "openai"),
        ("gpt-4-1106-preview", "openai"),
        ("o1-preview", "openai"),
        ("claude-3-5-sonnet-latest", "anthropic"),
        ("gemini-1.5-flash", "gemini"),
        ("mistral", "ollama"),
        ("llama3", "ollama"),
        ("qwen2.5-32b", "ollama"),
        ("some-vendor-model", "openai_compatible"),
        # local checkpoints: the vendor substring must not win
        ("qwen3:32b-instruct-q4_K_M", "ollama"),
        ("Qwen/Qwen2.5-32B-Instruct", "openai_compatible"),
        ("deepseek-ai/DeepSeek-R1-Distill-Qwen-14b", "openai_compatible"),
        ("deepseek-r1-distill-qwen-14b", "ollama"),
        ("llama3.2-vision:11b-textual-instruct-fp16", "ollama"),
        ("vllm/Qwen/Qwen2.5-32B-Instruct", "openai_compatible"),
    ])
    def test_mapping(self, router, model, expected):
        assert router.resolve_provider(model) == expected

    def test_chosen_provider_beats_name_guessing(self, router, monkeypatch):
        """The old bug: every `qwen*` was pinned to Ollama's :11434, so a vLLM box silently lost."""
        monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "vllm")
        assert router.resolve_provider("qwen3-32b") == "openai_compatible"
        monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "ollama")
        assert router.resolve_provider("Qwen/Qwen2.5-32b") == "ollama"

    def test_explicit_prefix_beats_the_chosen_provider(self, router, monkeypatch):
        monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "ollama")
        assert router.resolve_provider("vllm/qwen3-32b") == "openai_compatible"
        assert router.strip_server_prefix("vllm/Qwen/Qwen2.5-32B") == "Qwen/Qwen2.5-32B"
        assert router.strip_server_prefix("Qwen/Qwen2.5-32B") == "Qwen/Qwen2.5-32B"

    def test_cloud_product_names_stay_cloud(self, router, monkeypatch):
        monkeypatch.setenv("LLM_DEFAULT_PROVIDER", "ollama")
        assert router.resolve_provider("deepseek-chat") == "deepseek"
        assert router.resolve_provider("gpt-4o") == "openai"

    def test_provider_only_config_uses_what_the_server_has(self, router, monkeypatch):
        """`model: ollama` must not ask for llama3 on a box that only holds qwen3."""
        import civagent.utils.local_models as local_models

        monkeypatch.setattr(
            local_models, "discover_cached",
            lambda ttl=0: [{"kind": "ollama", "base_url": "http://127.0.0.1:11434/v1",
                            "models": ["qwen3:32b"]}],
        )
        provider, model_id = router.model_id_for("rome", model="ollama")
        assert (provider, model_id) == ("ollama", "qwen3:32b")

    def test_seat_overrides_model_id_only(self, router):
        provider, model_id = router.model_id_for("china")  # config: model: deepseek
        assert (provider, model_id) == ("deepseek", "deepseek-chat")
        provider, model_id = router.model_id_for("china", model="gpt-4o")
        assert (provider, model_id) == ("openai", "gpt-4o")
        provider, model_id = router.model_id_for("rome", model="claude-3-5-sonnet-latest")
        assert (provider, model_id) == ("anthropic", "claude-3-5-sonnet-latest")


class TestDispatch:
    def test_deepseek_structured_completion(self, router, monkeypatch):
        """只配 DeepSeek 能结构化补全 (M1.5) - client construction seam is faked."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")
        captured = {}

        from pydantic import BaseModel

        class Out(BaseModel):
            answer: int

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return Out(answer=42)

        class FakeInstructorClient:
            chat = SimpleNamespace(completions=FakeCompletions())

        def fake_make_instructor_client(base_url, api_key):
            captured["base_url"] = base_url
            captured["api_key"] = api_key
            return FakeInstructorClient()

        monkeypatch.setattr(router, "_make_instructor_client", fake_make_instructor_client)
        payload = {"messages": [{"role": "user", "content": "give 42"}]}
        message, raw = router.chat(payload, model="deepseek-chat", llm_config={"response_model": Out})
        assert "42" in message["content"]
        assert captured["base_url"] == "https://api.deepseek.com"
        assert captured["api_key"] == "sk-test-12345678"
        assert captured["model"] == "deepseek-chat"

    def _dispatch_target(self, router, monkeypatch):
        calls = {}

        def fake_openai_compatible(provider_config, model_id, payload, llm_config):
            calls.update(provider_config)
            calls["model_id"] = model_id
            return {"role": "user", "content": "ok"}, {}

        def fake_anthropic(provider_config, model_id, payload, llm_config, timeout):
            calls.update(provider_config)
            calls["model_id"] = model_id
            return {"role": "user", "content": "ok"}, {}

        monkeypatch.setattr(router, "_chat_openai_compatible", fake_openai_compatible)
        monkeypatch.setattr(router, "_chat_anthropic", fake_anthropic)
        return calls

    def test_four_vendors_route_to_their_key_slots(self, router, monkeypatch):
        calls = self._dispatch_target(router, monkeypatch)

        monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
        router.chat({"messages": []}, model="gpt-4o")
        assert calls["api_key"] == "openai-key"
        assert calls["base_url"] == "https://api.openai.com/v1"
        assert calls["model_id"] == "gpt-4o"

        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example/v1")
        router.chat({"messages": []}, model="some-vendor-model")
        assert calls["base_url"] == "https://relay.example/v1"
        monkeypatch.delenv("OPENAI_BASE_URL")

        monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
        router.chat({"messages": []}, model="gemini-1.5-flash")
        assert calls["api_key"] == "gemini-key"
        assert calls["base_url"].startswith("https://generativelanguage.googleapis.com")
        assert calls["model_id"] == "gemini-1.5-flash"

        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
        router.chat({"messages": []}, model="claude-3-5-sonnet-latest")
        assert calls["api_key"] == "anthropic-key"
        assert calls["model_id"] == "claude-3-5-sonnet-latest"

    def test_unconfigured_provider_raises_instead_of_fake_model(self, router):
        with pytest.raises(RuntimeError, match="not configured"):
            router.chat({"messages": []}, model="claude-3-5-sonnet-latest")

    def test_anthropic_json_mode_builds_schema_prompt(self, router, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
        posted = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"content": [{"text": '{"answer": 7}'}], "usage": {"output_tokens": 9}}

        def fake_post(url, headers=None, json=None, timeout=None):
            posted.update(url=url, headers=headers, body=json)
            return FakeResponse()

        monkeypatch.setattr("httpx.post", fake_post)

        from pydantic import BaseModel

        class Out(BaseModel):
            answer: int

        message, raw = router.chat(
            {"messages": [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]},
            model="claude-3-5-sonnet-latest",
            llm_config={"response_model": Out},
        )
        assert posted["headers"]["x-api-key"] == "anthropic-key"
        assert "JSON Schema" in posted["body"]["system"]
        assert "7" in message["content"]


class TestLocalOutputLadder:
    """探测只问得出「服务器收不收 response_format」，模型用得好不好必须靠降级重试兜住。"""

    def _profile(self, monkeypatch, structured):
        import civagent.utils.local_models as local_models

        def fake_profile(base_url, model_id):
            return {"base_url": base_url, "model": model_id, "model_resolved": model_id,
                    "reachable": True, "context_length": 32768, "quantization": "Q4_K_M",
                    "structured": structured, "models": [model_id], "reason": None}

        monkeypatch.setattr(local_models, "profile_for", fake_profile)

    def _fake_instructor(self, monkeypatch, router, reject):
        import instructor
        from pydantic import BaseModel

        class Out(BaseModel):
            answer: int

        calls: list = []

        def fake_client(base_url, api_key, mode=None):
            mode = mode or instructor.Mode.JSON

            class _Completions:
                def create(self, **kwargs):
                    calls.append(mode.name)
                    if mode.name in reject:
                        raise RuntimeError("400: response_format is not supported")
                    return Out(answer=42)

            return SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

        monkeypatch.setattr(router, "_make_instructor_client", fake_client)
        return Out, calls

    def test_schema_mode_rejected_falls_back_to_json(self, router, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
        self._profile(monkeypatch, "json_schema")
        Out, calls = self._fake_instructor(monkeypatch, router, reject={"JSON_SCHEMA"})
        message, raw = router.chat(
            {"messages": [{"role": "user", "content": "x"}]},
            model="Qwen/Qwen2.5-32B", llm_config={"response_model": Out},
        )
        assert calls == ["JSON_SCHEMA", "JSON"]
        assert "42" in message["content"]
        assert raw["structured_mode"] == "json"

    def test_unparsable_json_falls_back_to_plain_completion(self, router, monkeypatch):
        """小模型最常见的失败：字段都收、内容不是合法 JSON。plain 那一档自己解析。"""
        monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
        self._profile(monkeypatch, "json")
        Out, calls = self._fake_instructor(monkeypatch, router, reject={"JSON"})

        class _Message:
            content = 'Sure! Here it is:\n{"answer": 7}\n hope that helps'

        def fake_openai_client(base_url, api_key):
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=_Message())])
            )))

        monkeypatch.setattr(router, "_openai_client", fake_openai_client)
        message, raw = router.chat(
            {"messages": [{"role": "user", "content": "x"}]},
            model="Qwen/Qwen2.5-32B", llm_config={"response_model": Out},
        )
        assert calls == ["JSON"]
        assert raw["structured_mode"] == "plain"
        assert "7" in message["content"]

    def test_server_without_any_json_support_goes_straight_to_plain(self, router, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8080/v1")
        self._profile(monkeypatch, "none")
        Out, calls = self._fake_instructor(monkeypatch, router, reject=set())

        class _Message:
            content = '{"answer": 3}'

        monkeypatch.setattr(router, "_openai_client", lambda base_url, api_key: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(message=_Message())])
            ))))
        message, raw = router.chat(
            {"messages": [{"role": "user", "content": "x"}]},
            model="llama3.2:3b", llm_config={"response_model": Out},
        )
        assert calls == []  # instructor never touched
        assert "3" in message["content"]

    def test_empty_completion_names_max_tokens(self, router, monkeypatch):
        """推理模型把 token 花在 thinking 上时，报的必须是能照着修的原因。"""
        from pydantic import BaseModel

        monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
        self._profile(monkeypatch, "none")

        class Out(BaseModel):
            answer: int

        open_tag = "<" + "think>"
        monkeypatch.setattr(router, "_openai_client", lambda base_url, api_key: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(
                        content=open_tag + "still thinking"))])
            ))))
        with pytest.raises(RuntimeError, match="raise max_tokens"):
            router.chat({"messages": [{"role": "user", "content": "x"}]},
                        model="Qwen/Qwen2.5-32B", llm_config={"response_model": Out})


class TestCloudPathUnchanged:
    def test_cloud_base_url_skips_the_ladder(self, router, monkeypatch):
        """云端的 key 检查、base_url 与结构化路径都不该被本地探测改动。"""
        from pydantic import BaseModel

        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-12345678")
        seen = {}

        class Out(BaseModel):
            answer: int

        def fake_client(base_url, api_key, mode=None):
            seen["base_url"] = base_url
            seen["mode"] = mode

            class _C:
                def create(self, **kwargs):
                    return Out(answer=1)

            return SimpleNamespace(chat=SimpleNamespace(completions=_C()))

        monkeypatch.setattr(router, "_make_instructor_client", fake_client)
        router.chat({"messages": []}, model="deepseek-chat", llm_config={"response_model": Out})
        assert seen["base_url"] == "https://api.deepseek.com"
        assert seen["mode"] is None  # the historical single-mode call


class TestLocalTimeouts:
    """游戏 15 秒就放弃，大脑线程不能抱着席位锁等 OpenAI 默认的 600 秒。"""

    def test_plain_client_is_bounded(self, router, monkeypatch):
        import openai

        captured = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
        router._openai_client("http://127.0.0.1:8000/v1", "")
        assert captured["timeout"] == router.LOCAL_REQUEST_TIMEOUT_S

    def test_instructor_client_bounded_only_on_the_local_path(self, router, monkeypatch):
        import instructor
        import openai

        captured = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
        monkeypatch.setattr(instructor, "from_openai", lambda client, mode=None: object())
        router._make_instructor_client("https://api.deepseek.com", "k")
        assert captured["timeout"] is None  # cloud path unchanged
        router._make_instructor_client("http://127.0.0.1:8000/v1", "", instructor.Mode.JSON)
        assert captured["timeout"] == router.LOCAL_REQUEST_TIMEOUT_S


class TestRedaction:
    def test_sk_keys_scrubbed(self, router):
        text = "key=sk-abc123defghij ok"
        assert "sk-abc123defghij" not in router.redact(text)
        assert "sk-***" in router.redact(text)

    def test_bearer_scrubbed(self, router):
        scrubbed = router.redact("Authorization: Bearer abcdef1234567890")
        assert "abcdef1234567890" not in scrubbed
        assert "Bearer ***" in scrubbed
