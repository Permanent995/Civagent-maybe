# 本地模型部署（Ollama / vLLM / LM Studio / llama.cpp / SGLang / MLX / LMDeploy）

> 结论先行：大脑**不再靠模型名字猜它在哪儿跑**，也不再靠端口认定是哪家的服务器。
> 以前 `qwen*` 一律被当成 Ollama，于是 vLLM、LM Studio、llama.cpp 的用户不改
> `OPENAI_BASE_URL` 就必然连不上 `:11434`，症状是「大脑健康、游戏里每个席位却都是原生 AI」。
> 现在本地服务靠探测，探测结果写在 `/healthz` 与 `/status` 里。
>
> 本文的端口与字段都对着官方文档/发布说明核过，出处见文末「参考」。本地部署这块变化很快，
> 遇到不符的现象**以你本机 `/status` 报的为准**，不要以本文的年份为准。

---

## 1. 三个层次，优先级从高到低

| 优先级 | 写法 | 例子 |
|--------|------|------|
| 1 显式前缀 | `<server>/<model>`，前缀只用于选服务器，会剥掉再发出去 | `vllm/Qwen/Qwen2.5-32B-Instruct`、`ollama/qwen3:32b` |
| 2 用户选定的 provider | `LLM_DEFAULT_PROVIDER` / config 的 `LLM.default_provider` / 游戏内 Options→Agent 的 Provider | `LLM_DEFAULT_PROVIDER=vllm` ⇒ `qwen3-32b` 走 OpenAI 兼容口 |
| 3 名字启发 | 只有前两者都没写时才生效 | `deepseek-chat`→云端、`gpt-4o`→云端、`qwen2.5-32b`→Ollama |

**云端产品名不会被本地规则抢走**：`deepseek-chat`、`gpt-4o` 这类照旧走厂商 API；而带
`distill / q4_k_m / awq / gptq / 32b / :tag / 仓库名/` 特征的，一律判为本地 checkpoint。

什么都没写、但本机有服务在跑时，大脑会扫描下面的端口，挑第一个活着的服务器，
并且**用它实际加载的模型**（配置只写 `model: ollama` 时不会再固执地要 `llama3`）。

## 2. 端口：候选清单与它们的坑

| 服务 | 默认 OpenAI 兼容口 | 启动 | 要不要 key |
|------|-------------------|------|-----------|
| Ollama | `:11434/v1` | `ollama serve` + `ollama pull qwen3:32b` | 不要 |
| LM Studio | `:1234/v1`（另有原生 REST `/api/v0/*`） | 界面 Start Server，或无界面的 `llmster` 守护进程 | 不要 |
| vLLM | `:8000/v1` | `python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-32B-Instruct --max-model-len 16384` | 不要 |
| SGLang | `:30000/v1` | `python -m sglang.launch_server --model-path deepseek-ai/DeepSeek-R1-Distill-Qwen-14B` | 不要 |
| llama.cpp | **`:8080/v1`，官方已开始把默认口迁到 `:9931`** | `llama-server -m qwen2.5-32b-instruct-q4_k_m.gguf -c 16384 --port 8080`（**自己钉住端口**） | 不要 |
| LMDeploy | `:23333/v1` | `lmdeploy serve api_server <model>`（Swagger 在 `:23333`） | 不要 |
| KoboldCpp | `:5001` | 界面里 Enable API（长期默认，本文未重新核） | 不要 |

三个必须知道的点：

1. **端口不能用来认身份**：llama.cpp 的历史默认口和 mlx-lm 的默认口都是 8080。
   所以 `discover()` 里 `kind` 只是「哪个候选端口应答了」，真正的身份看 `flavor`，
   它由端点签名判定：`/api/tags`→Ollama，`/props` 或 `/admin/server/props`→llama.cpp，
   `/api/v0/models`→LM Studio，`/metrics` 里的 `vllm:` / `sglang:` 前缀→vLLM / SGLang，
   最后再读首页 HTML 兜底；都不像就老实报 `openai_compatible`。
2. llama.cpp 的 `/props` **默认是关的**（老 POST 端点已标 deprecated，要 `--props` 才开），
   所以下面那个上下文数字可能拿不到，这不是 bug。识别也照样只能落到 `openai_compatible`——
   不影响调用，只影响报告显示。
3. 端口不是这几个时的显式接法：

```bash
export CIVAGENT_LOCAL_BASE_URL=http://127.0.0.1:9090/v1
```

**回环地址不再要求填 key**（`OPENAI_BASE_URL` 指向 `127.0.0.1`/`localhost`/`::1` 时免
`OPENAI_API_KEY`）；指向局域网或公网的 base url 仍然要 key。

## 3. config.yaml 示例

```yaml
LLM:
  default_provider: ollama          # 也可写 vllm / lmstudio / llamacpp / sglang / openai_compatible
  model: qwen3:32b-instruct-q4_K_M  # 全局覆盖（游戏内 Options→Agent 的 Model 写的就是它）
  ollama_base_url: http://127.0.0.1:11434/v1   # 端口非默认时才需要
  # local_base_url: http://127.0.0.1:8000/v1   # 给 OpenAI 兼容口用，等价于上面的环境变量
  streaming: false
  max_tokens: 1024                  # 推理模型要留够，见 §6

# 席位级覆盖：每国可以换模型，前缀决定它落在哪台机器
mongolia:
  model: vllm/Qwen/Qwen2.5-32B-Instruct
china:
  model: ollama/deepseek-r1-distill-qwen:14b
```

游戏内等价的填法：Options → CivAgent，Provider 选 `ollama`（或 `openai_compatible`）、
Base URL 填 `http://127.0.0.1:8000/v1`、Model 填模型 id。Brain URL 保持 `http://127.0.0.1:2335`
不动——那是大脑自己的端口，不是模型的。

## 4. 探测出来的三件事

`local_models.profile_for()` 每个 `base_url + model` 缓存 10 分钟（失败只缓存 30 秒，
这样「先起大脑、后起 Ollama」不用重启大脑）：

| 字段 | 从哪来 | 注意 |
|------|--------|------|
| `context_capacity` | Ollama `/api/show` 的 `*.context_length`（键带架构前缀）、llama.cpp `/props`、LM Studio 原生 `/api/v0/models`、通用服务器 `/models` 条目 | 这是**模型上限** |
| `context_length` | **Ollama `/api/ps` 的 `context_length`**（本次真正分配的），拿不到才退回上限 | `OLLAMA_CONTEXT_LENGTH` / `num_ctx` / 默认 2048 都会让它远小于上限，**截断发生在这一层** |
| `quantization` | Ollama `/api/ps` 的 `details.quantization_level`、`/api/show` 元数据 | 只用于报告，一眼看出跑的是 Q4 还是 FP16 |
| `structured` | **行为探测**：拿 `json_schema`→`json_object` 各发一次 `max_tokens=4` 的补全，看服务器收不收 | 见 §5 的局限 |

字段名各家不同（Ollama 把 GGUF 元数据加了架构前缀），所以是递归找键名而不是硬写路径。
**vLLM 的 `/v1/models` 可能只返回 `id/object/created/owned_by` 而没有 `max_model_len`**
（外部集成方就为此崩过），llama.cpp 的 `/props` 又可能没开——这时 `context_length` 是 `null`
并给出 `context_note`，让你自己按 `--max-model-len` / `-c` 设的值来对，**不编一个数字**。

## 5. 结构化输出的降级阶梯

```
json_schema（instructor Mode.JSON_SCHEMA）
   ↓ 报错就降
json（instructor Mode.JSON，现状默认）
   ↓ 还不行就降
plain（纯文本补全 + 自己剥 thinking、抠 JSON、pydantic 校验，失败再补一条指令重试一次）
```

局限要说清：行为探测只能证明「**服务器接受这个字段**」，证明不了「**这个 checkpoint 用得好**」。
LM Studio 自己的文档就写着约束效果取决于模型与运行时；也有服务器对不认识的 `response_format`
直接忽略并返回 200。所以阶梯的第二层保险是必须的，`/status` 里的 `structured` 只是提示不是保证。

云端路径一个字没改，仍然是单次 `Mode.JSON`。

## 6. 推理模型（R1 / Qwen3 thinking）的专属坑

- `max_tokens` 不够时模型全部花在思考上，正文为空。错误信息会直接写
  `empty completion (raise max_tokens: the model spent them thinking)`，别猜。
- 思考过程要么在 `` / `<thinking>` 标签里，要么在 `reasoning_content` 字段里。
  `strip_reasoning()` 两者都处理，**包括被 max_tokens 截断、只有起始标签没有结束标签的情况**
  （那种保留标签之前的内容，也就是真正要说的那句话）。
- 流式对话会在每个增量上先剥一遍，所以思考不会画进气泡里；
  `parse_intent_fences()` 也是先剥再解析围栏——模型在草稿里写过「宣战」不等于它对玩家许诺过。

## 7. 出问题时看这两个端点，别看游戏

```bash
curl -s http://127.0.0.1:2335/healthz | python -m json.tool   # 起不来时的第一站
curl -s http://127.0.0.1:2335/status   | python -m json.tool
```

| 看到 | 含义 | 怎么办 |
|------|------|--------|
| `llm: offline` + `reason: nothing answered at ...` | 服务没起 | 按 §2 起服务 |
| `reason: model 'x' is not loaded` + `models: [...]` | 端口对、模型名不对 | 用列表里的名字，或 `ollama pull` |
| `discovered: []` | 候选端口都没人应 | 填 `CIVAGENT_LOCAL_BASE_URL` |
| `local.flavor` 与你装的服务器不符 | 签名端点没开（如 llama.cpp 未加 `--props`） | 只是识别不到，不影响调用；`--port`/URL 自己钉准 |
| `context_length: null` + `context_note` | 服务器不发这个数 | 按你启动时的 `--max-model-len`/`-c` 自己对 |
| `context_length` 远小于 `context_capacity` | 分配被 `OLLAMA_CONTEXT_LENGTH`/`num_ctx` 压小了 | 调大，注意 KV cache 吃显存 |
| `prompt_over_80pct_context: true` | 切片已逼近实际预算 | 加上下文或减席位 |
| `structured: none` | 服务器不收约束字段 | 正常，自动走 plain |
| `llm: ok` 但游戏里全是原生 AI | 多半是**回合超时**，见下 | — |

游戏侧 `AgentConfig.decisionTimeoutMs` 目前是 **15 秒硬编码**
（Unciv-Source 的 `core/src/com/unciv/logic/civagent/AgentTurnHook.kt:17`），Options→Agent
里没有这一项。32B Q4 在单卡上带两千多 token 的切片很容易超过它，一旦超时就静默回退原生 AI。
这是当前本地部署最主要的失败原因，也是下一步该做成可调项的地方。

大脑侧另有一条 **120 秒**的自身超时（`llm_router.LOCAL_REQUEST_TIMEOUT_S`，只作用于本地路径）：
游戏 15 秒放弃后，请求线程不会抱着席位锁一直等到 OpenAI SDK 默认的 600 秒，最多两分钟就释放。

## 8. 格式与选型

模型**文件格式**（GGUF / MLX / EXL3 / AWQ / GPTQ / FP8 / NVFP4 等）不影响 CivAgent——
我们只认 OpenAI 兼容口；格式决定的是「哪个 runner 能跑它」：GGUF 走 llama.cpp·Ollama·LM Studio，
MLX 走 Apple Silicon 的 mlx-lm，AWQ/GPTQ/FP8/NVFP4 主要走 vLLM·SGLang。
所以别人给你一个 `.gguf`，你要做的不是改 CivAgent，是起一个能读它的服务器再填 URL。

外交对话 + 意图抽取同时要「长上下文、严格格式、中多人格」，小模型掉分很明显：

- 下限：`Qwen2.5-14B-Instruct`、`DeepSeek-R1-Distill-Qwen-14B`（Q4，12 GB 显存级别）
- 质量拐点：`Qwen2.5-32B-Instruct` / `qwen3:32b`（Q4 约 20 GB）
- 想省上下文：选不带 thinking 的 instruct 版；推理版会把 token 花在草稿上
- 席位多时优先加显存而不是加模型：每席每回合一次 `/v1/decision` 是串行预算

## 9. jevos：可选的决策闸门（CPU）

[jevos](https://github.com/feder-cr/jev) 是 MIT 许可的 **CPU 决策模型**（不是聊天模型）：
给它一段文本 + 若干问题，返回"是/否概率、最可能的选项、分数"，实测 28–130 ms，上下文 8192。
它不会写意图，所以 CivAgent 的接法是**让 LLM 提意图、jevos 决定这一轮值不值得做**。

启用（默认关闭，不下载任何模型，需要用户自己按它的 release 起服务）：

```bash
# 1) 按 jev 的 release 说明解包（Windows: jev.exe serve）
./jev serve              # 默认 127.0.0.1:8017，GET /health 可探活；JEV_API_KEY 可开 Bearer
# 2) 打开闸门
export CIVAGENT_JEV_GATE=1
export CIVAGENT_JEV_URL=http://127.0.0.1:8017
export CIVAGENT_JEV_THRESHOLD=0.5     # 概率低于它的意图被丢弃
```

等价 config.yaml（`LLM` 段）：`jev_gate: true` / `jev_url: ...` / `jev_threshold: 0.5` / `jev_key: ...`。

工作方式：每个意图问一道 `noul` 题（"Should this civilization carry out the diplomatic action
'make_peace' toward 'Sheng Tang' right now, given the situation?"），**一次请求带走全部问题**（状态只读一遍），
每次最多 6 个意图（超出的原样保留）。任何失败——服务没起、超时、返回缺答案、HTTP 非 200——都**原样放行**，
席位行为与没装 jevos 完全一致。判词写进该席位的 decision 记忆（`"jev": {...}`），
并可通过 `GET /status` 的 `jev` 块查看（`reachable` / `threshold` / `last`）。

线格式（TypeSafe Jev）：

```json
POST /v1/systemone
{"model": "jev-latest", "state": "Turn 12. Civilization: mongolia. ...",
 "questions": {"i0": {"type": "noul", "instructions": "Should this civilization carry out ...?"}}}
→ {"answers": {"i0": {"type": "noul", "noul": 0.87}}, "usage": {...}}
```

注意：这是**闸门不是裁判**。它自己的七项基准在 0.36–0.92 之间，外交局势这种多步推理不在其中；
默认阈值 0.5 只是起点，建议先跑一段对照（关/开各若干局）再调，别把它的概率当收益。

## 参考

- Ollama 上下文（`/api/ps` 查实际分配、`/api/show` 查上限、`OLLAMA_CONTEXT_LENGTH`、`num_ctx`）：<https://docs.ollama.com/context-length>
- Ollama API 响应字段（`name / details.quantization_level / context_length / size_vram`）：<https://docs.ollama.com/api/ps>
- vLLM OpenAI 兼容服务器（支持的 API 与 `/v1/models`）：<https://docs.vllm.ai/en/latest/serving/online_serving/>
- `/v1/models` 可能不含 `max_model_len` 导致的集成崩溃：<https://github.com/letta-ai/letta/issues/3384>
- llama.cpp server README（`/props` 需 `--props`、OpenAI 兼容口、`response_format` 的 json_schema）：<https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>
- llama-server 部署与 `--port`（默认口正在从 8080 迁到 9931）：<https://jacar.es/en/llama-server-openai-compatible-api-docker/> 、<https://llama.app/docs/api>
- llama.cpp 模型管理 / router 模式：<https://huggingface.co/blog/ggml-org/model-management-in-llamacpp>
- LM Studio 结构化输出（`response_format.type = "json_schema"`，效果取决于模型与运行时）：<https://lmstudio.ai/docs/developer/openai-compat/structured-output>
- LM Studio 原生 REST v0（`GET /api/v0/models` 带每个模型的加载状态）：<https://lmstudio.ai/docs/developer/rest/endpoints>
- LM Studio 以 llama.cpp(GGUF) 或 MLX 运行、可作 `llmster` 守护进程：<https://docs.openclaw.ai/zh-CN/providers/lmstudio>
- LMDeploy OpenAI 兼容服务默认 `:23333`：<https://github.com/InternLM/lmdeploy/blob/main/docs/zh_cn/llm/api_server.md>
- mlx-lm 参考服务器默认 `:8080`：<https://asiai.dev/engines/mlxlm/>
- GGUF 与本地推理格式总览：<https://www.datacamp.com/zh/tutorial/gguf-format-a-complete-guide>
- jevos（CPU 决策模型，`/v1/systemone`、`/health`、`JEV_API_KEY`、MIT）：<https://github.com/feder-cr/jev>
- jevos 模型与 release（需自行下载，CivAgent 不会自动拉取）：<https://github.com/feder-cr/jev/releases/tag/jevos-v5> 、<https://huggingface.co/feder-cr/jev>
