"""Local server discovery / capability probing - transport faked, no ports touched."""

import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _Response:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _Transport:
    """URL -> response, with call counting so cache behaviour is observable."""

    def __init__(self, gets=None, posts=None):
        self.gets = gets or {}
        self.posts = posts or {}
        self.get_calls: list = []
        self.post_calls: list = []

    def install(self, monkeypatch, module):
        def fake_get(url, timeout=None):
            self.get_calls.append(url)
            if url not in self.gets:
                raise ConnectionError("refused: " + url)
            return self.gets[url]

        def fake_post(url, payload, timeout=None):
            self.post_calls.append((url, payload))
            if url not in self.posts:
                raise ConnectionError("refused: " + url)
            return self.posts[url]

        monkeypatch.setattr(module, "_get", fake_get)
        monkeypatch.setattr(module, "_post", fake_post)


@pytest.fixture()
def lm():
    import civagent.utils.local_models as module

    importlib.reload(module)
    return module


OLLAMA = "http://127.0.0.1:11434/v1"
LMSTUDIO = "http://127.0.0.1:1234/v1"
OPEN_TAG = "<" + "think>"
CLOSE_TAG = "<" + "/think>"


class TestUrlRules:
    def test_loopback_only_counts_as_local(self, lm):
        assert lm.is_local_base_url(OLLAMA) is True
        assert lm.is_local_base_url("http://localhost:1234/v1") is True
        assert lm.is_local_base_url("https://api.deepseek.com") is False
        assert lm.is_local_base_url("") is False
        assert lm.is_local_base_url(None) is False

    def test_native_api_lives_above_the_v1_suffix(self, lm):
        assert lm.root_of(OLLAMA) == "http://127.0.0.1:11434"
        assert lm.root_of("http://127.0.0.1:8000/v1/") == "http://127.0.0.1:8000"


class TestStripReasoning:
    @pytest.mark.parametrize("raw,expected", [
        (OPEN_TAG + "private" + CLOSE_TAG + "peace offer", "peace offer"),
        ("I agree" + OPEN_TAG + "truncated by max_tokens", "I agree"),
        ("plain prose", "plain prose"),
        ("", ""),
        ("Reasoning:\nso much thought\nAnswer:\nwe accept", "we accept"),
    ])
    def test_cases(self, lm, raw, expected):
        assert lm.strip_reasoning(raw) == expected


class TestModelListing:
    def test_openai_and_ollama_shapes(self, lm):
        assert lm.model_ids({"data": [{"id": "qwen3:32b"}, {"name": "gemma3n"}]}) == ["qwen3:32b", "gemma3n"]
        assert lm.model_ids({"models": [{"name": "x"}, {"nope": 1}]}) == ["x"]
        assert lm.model_ids(None) == []

    def test_namespaced_context_and_quantization_keys(self, lm):
        """Ollama prefixes its GGUF metadata with the architecture name."""
        body = {"model_info": {"qwen3.context_length": 40960, "general.file_type": "Q4_K_M"}}
        assert lm._walk_numbers(body, lm._CONTEXT_KEYS) == 40960
        assert lm._walk_strings(body, lm._QUANT_KEYS) == "Q4_K_M"
        assert lm._walk_numbers({"n_ctx": 0}, lm._CONTEXT_KEYS) is None  # zero is not a budget


class TestProfile:
    def test_ollama_reports_allocated_context_not_the_checkpoint_max(self, lm, monkeypatch):
        """/api/show 是模型上限，/api/ps 才是这次真正分配到的预算 - 截断发生在后者。"""
        transport = _Transport(
            gets={
                OLLAMA + "/models": _Response(200, {"data": [{"id": "qwen3:32b"}]}),
                "http://127.0.0.1:11434/api/tags": _Response(200, {"models": [{"name": "qwen3:32b"}]}),
                "http://127.0.0.1:11434/api/ps": _Response(200, {"models": [{
                    "name": "qwen3:32b", "context_length": 4096,
                    "details": {"family": "qwen3", "parameter_size": "32B",
                                "quantization_level": "Q4_K_M"}, "size_vram": 19000000000,
                }]}),
            },
            posts={
                "http://127.0.0.1:11434/api/show": _Response(
                    200, {"model_info": {"qwen3.context_length": 40960}}),
                OLLAMA + "/chat/completions": _Response(200, {"choices": [{"message": {"content": "{}"}}]}),
            },
        )
        transport.install(monkeypatch, lm)
        profile = lm.profile_for(OLLAMA, "qwen3:32b")
        assert profile["flavor"] == "ollama"
        assert profile["context_capacity"] == 40960
        assert profile["context_length"] == 4096
        assert profile["quantization"] == "Q4_K_M"
        assert profile["structured"] == "json_schema"
        assert any("chat/completions" in url for url, _ in transport.post_calls)

    def test_ollama_model_loaded_later_falls_back_to_capacity(self, lm, monkeypatch):
        """还没加载时 /api/ps 是空的，只能给上限，但不能报成实际预算。"""
        transport = _Transport(
            gets={
                OLLAMA + "/models": _Response(200, {"data": [{"id": "qwen3:32b"}]}),
                "http://127.0.0.1:11434/api/tags": _Response(200, {"models": [{"name": "qwen3:32b"}]}),
                "http://127.0.0.1:11434/api/ps": _Response(200, {"models": []}),
            },
            posts={
                "http://127.0.0.1:11434/api/show": _Response(
                    200, {"model_info": {"qwen3.context_length": 40960}}),
                OLLAMA + "/chat/completions": _Response(200, {"choices": [{"message": {"content": "{}"}}]}),
            },
        )
        transport.install(monkeypatch, lm)
        profile = lm.profile_for(OLLAMA, "qwen3:32b")
        assert profile["context_length"] == 40960  # capacity is the only number available

    def test_server_that_publishes_no_context_says_so(self, lm, monkeypatch):
        """vLLM 的 /v1/models 常常只有 id/object/created/owned_by，不能编一个数出来。"""
        transport = _Transport(
            gets={"http://127.0.0.1:8000/v1/models": _Response(
                200, {"data": [{"id": "Qwen/Qwen2.5-32B-Instruct", "object": "model",
                                "created": 1, "owned_by": "vllm"}]})},
            posts={"http://127.0.0.1:8000/v1/chat/completions": _Response(200, {"choices": []})},
        )
        transport.install(monkeypatch, lm)
        profile = lm.profile_for("http://127.0.0.1:8000/v1", "Qwen/Qwen2.5-32B-Instruct")
        assert profile["context_length"] is None
        assert profile["context_note"] and "--max-model-len" in profile["context_note"]

    def test_lmstudio_context_comes_from_its_native_rest(self, lm, monkeypatch):
        """LM Studio 的 OpenAI 层不报上下文，原生 /api/v0/models 才带每个模型的状态。"""
        transport = _Transport(
            gets={
                LMSTUDIO + "/models": _Response(200, {"data": [{"id": "qwen3-32b"}]}),
                "http://127.0.0.1:1234/api/v0/models": _Response(200, {"data": [
                    {"id": "qwen3-32b", "state": "loaded", "max_context_length": 32768}]}),
            },
            posts={LMSTUDIO + "/chat/completions": _Response(200, {"choices": []})},
        )
        transport.install(monkeypatch, lm)
        profile = lm.profile_for(LMSTUDIO, "qwen3-32b")
        assert profile["flavor"] == "lmstudio"
        assert profile["context_length"] == 32768

    def test_schema_rejection_downgrades_to_json_then_none(self, lm, monkeypatch):
        transport = _Transport(
            gets={OLLAMA + "/models": _Response(200, {"data": [{"id": "phi4-mini"}]})},
            posts={OLLAMA + "/chat/completions": _Response(400, {"error": "bad response_format"})},
        )
        transport.install(monkeypatch, lm)
        assert lm.profile_for(OLLAMA, "phi4-mini")["structured"] == "none"

    def test_model_absent_from_the_server(self, lm, monkeypatch):
        transport = _Transport(gets={OLLAMA + "/models": _Response(200, {"data": [{"id": "llama3"}]})})
        transport.install(monkeypatch, lm)
        profile = lm.profile_for(OLLAMA, "qwen3:32b")
        assert profile["reachable"] is False
        assert "not loaded" in profile["reason"]
        assert profile["available"] == ["llama3"]

    def test_nothing_answering_names_the_commands_to_start(self, lm, monkeypatch):
        transport = _Transport()  # every port raises ConnectionError
        transport.install(monkeypatch, lm)
        problem = lm.server_problem("http://127.0.0.1:8000/v1", "Qwen/Qwen2.5-32B-Instruct")
        assert problem is not None and "nothing answered" in problem
        assert "vllm.entrypoints.openai.api_server" in problem

    def test_cloud_urls_are_never_probed(self, lm, monkeypatch):
        transport = _Transport()
        transport.install(monkeypatch, lm)
        assert lm.server_problem("https://api.deepseek.com", "deepseek-chat") is None
        assert transport.get_calls == []

    def test_profile_is_cached_until_expiry(self, lm, monkeypatch):
        transport = _Transport(gets={OLLAMA + "/models": _Response(200, {"data": [{"id": "llama3"}]})})
        transport.install(monkeypatch, lm)
        lm.profile_for(OLLAMA, "llama3")
        calls = len(transport.get_calls)
        lm.profile_for(OLLAMA, "llama3")
        assert len(transport.get_calls) == calls
        lm.clear_cache()
        lm.profile_for(OLLAMA, "llama3")
        assert len(transport.get_calls) > calls


class TestDiscover:
    def test_only_answering_servers_are_listed(self, lm, monkeypatch):
        transport = _Transport(gets={
            "http://127.0.0.1:1234/v1/models": _Response(200, {"data": [{"id": "DeepSeek-R1-Distill-Qwen-14B"}]}),
        })
        transport.install(monkeypatch, lm)
        found = lm.discover()
        assert found[0]["base_url"] == "http://127.0.0.1:1234/v1"
        assert found[0]["kind"] == "lmstudio"          # which candidate port answered
        assert found[0]["flavor"] == "openai_compatible"  # what the signatures actually proved
        assert found[0]["models"] == ["DeepSeek-R1-Distill-Qwen-14B"]
        assert lm.first_local_base_url() == "http://127.0.0.1:1234/v1"
        assert lm.first_local_base_url("vllm") is None

    def test_ollama_without_the_openai_compat_layer(self, lm, monkeypatch):
        transport = _Transport(gets={
            OLLAMA + "/models": _Response(404),
            "http://127.0.0.1:11434/api/tags": _Response(200, {"models": [{"name": "qwen3:32b"}]}),
        })
        transport.install(monkeypatch, lm)
        assert lm.discover()[0]["models"] == ["qwen3:32b"]

    def test_llama_cpp_new_default_port_is_scanned(self, lm):
        """8080 是历史默认，9931 是新默认，两个都必须在候选里。"""
        ports = {url.rsplit(":", 1)[1].split("/")[0] for _, url in lm.LOCAL_CANDIDATES}
        assert {"8080", "9931", "23333", "11434", "1234", "8000", "30000"} <= ports


class TestFlavorSignatures:
    """端口会骗人（llama.cpp 与 mlx-lm 都用 8080），所以按端点签名识别。"""

    def _run(self, lm, monkeypatch, gets, metrics_text=""):
        transport = _Transport(gets=gets)
        transport.install(monkeypatch, lm)
        if metrics_text:
            def fake_get(url, timeout=None):
                if url.endswith("/metrics"):
                    return _Response(200, {})
                return transport.gets.get(url, _Response(404, {}))

            monkeypatch.setattr(lm, "_get", fake_get)
            monkeypatch.setattr(lm, "_text", lambda response: metrics_text)
        lm._flavor_cache.clear()
        return lm.flavor_for("http://127.0.0.1:8080/v1")

    def test_ollama(self, lm, monkeypatch):
        assert self._run(lm, monkeypatch, {"http://127.0.0.1:8080/api/tags": _Response(200, {"models": []})}) == "ollama"

    def test_llamacpp(self, lm, monkeypatch):
        got = self._run(lm, monkeypatch, {"http://127.0.0.1:8080/props": _Response(200, {"n_ctx": 4096})})
        assert got == "llamacpp"

    def test_lmstudio(self, lm, monkeypatch):
        got = self._run(lm, monkeypatch, {
            "http://127.0.0.1:8080/api/v0/models": _Response(200, {"data": [{"id": "qwen3-32b"}]}),
            "http://127.0.0.1:8080/api/tags": _Response(404),
        })
        assert got == "lmstudio"

    def test_vllm_and_sglang_by_metric_prefix(self, lm, monkeypatch):
        assert self._run(lm, monkeypatch, {}, "# HELP vllm:cache_config_info\n") == "vllm"
        assert self._run(lm, monkeypatch, {}, "# HELP sglang:num_running_reqs\n") == "sglang"

    def test_unknown_server_stays_generic(self, lm, monkeypatch):
        assert self._run(lm, monkeypatch, {}) == "openai_compatible"

    def test_llamacpp_by_metric_prefix_even_with_props_disabled(self, lm, monkeypatch):
        """llama.cpp 的 /props 默认关闭，但 /metrics 的 llamacpp: 前缀一直都在。"""
        got = self._run(lm, monkeypatch, {}, "# HELP llamacpp:prompt_tokens_total\n")
        assert got == "llamacpp"


class TestRealSocket:
    """真 socket 端到端：探测拒掉 json_schema -> 降到 json -> instructor 解析出对象。

    临时端口、不加载任何模型；这是除 fake transport 之外唯一的真实网络证据。
    """

    @pytest.fixture()
    def fake_server(self):
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
                if self.path == "/v1/models":
                    self._send(200, {"data": [{"id": "fake/qwen3-32b", "object": "model",
                                               "max_model_len": 16384}]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                response_format = body.get("response_format") or {}
                if response_format.get("type") == "json_schema":
                    self._send(400, {"error": "response_format json_schema is not supported"})
                    return
                content = '{"answer": 41}' if response_format.get("type") == "json_object" else "hello"
                self._send(200, {
                    "id": "chatcmpl-fake", "object": "chat.completion", "created": 1,
                    "model": body.get("model", "fake"),
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
                })

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        yield base_url
        server.shutdown()
        server.server_close()

    def test_probe_and_ladder_over_http(self, fake_server):
        from pydantic import BaseModel

        import civagent.utils.local_models as lm
        from civagent.utils import llm_router

        lm.clear_cache()
        profile = lm.profile_for(fake_server, "fake/qwen3-32b")
        assert profile["reachable"] is True
        assert profile["context_length"] == 16384
        assert profile["structured"] == "json"  # json_schema was rejected, json_object accepted

        class Out(BaseModel):
            answer: int

        message, raw = llm_router._chat_with_ladder(
            {"base_url": fake_server, "api_key": ""}, "fake/qwen3-32b",
            {"messages": [{"role": "user", "content": "give 41"}]}, Out, "json")
        assert json.loads(message["content"]) == {"answer": 41}
        assert raw["structured_mode"] == "json"
