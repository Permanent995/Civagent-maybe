# CivAgent 冻结契约（W1 末冻结，W2 接真值）

> 依据 [`EXECUTION.md`](../../EXECUTION.md)「冻结契约」一节。两端实现：
> 游戏（Kotlin）：`Unciv-Source/core/src/com/unciv/logic/civagent/`；大脑（Python）：`Civagent_repo`（W2 实现 `/v1/decision`）。

## 端点总表

| 端点 | 方向 | 说明 |
|------|------|------|
| `POST /v1/decision` | 游戏 → 大脑 | 请求体 = [ObservationSlice](observation_slice.schema.json)；响应 = `Intent[]`（[intent.schema.json](intent.schema.json)），**禁止 CLI 字符串** |
| `POST /v1/applied` | 游戏 → 大脑 | `{ ok, error, applied[], fellBackToNative }`：每批意图的落地回执 |
| `GET /healthz` | 大脑 | `{"llm":"ok","provider":…}`（W1b）；密钥不进响应 |
| `POST /message` | ChatUI → 大脑 | `{gameId, fromCiv, toCiv, text, language}`；200 = 已开始（异步处理，coder/agentapi 语义） |
| `GET /events` | ChatUI → 大脑 | SSE（`?gameId=&civID=&after=seq`）；事件 `{seq, kind, fromCiv, toCiv, text, intents[], status:"stable"}`；`status=stable` 才算说完 |
| `GET /messages` | ChatUI → 大脑 | `?gameId=&civ1=&civ2=&after=` → 成对对话记录 JSON |
| `GET /status` | ChatUI → 大脑 | LLM 状态、活跃席位、事件计数 |

**草稿意图（W3）**：回复文本里的 ```` ```intent {…} ```` 围栏由两端各自剥离——大脑端解析进事件的 `intents[]`（白名单外丢弃），游戏端展示为草稿并入 `AgentMod` 草稿队列，**只在该席位下一回合**经 G4 执行；聊天进行中棋盘零变动（M2.5）。语言锁：客户端随消息传游戏语言，大脑 system prompt 锁死（W3.4）。

**W5 情报**：`share_intel` 必须带 `intel.claim` 文字主张，只写 `claims.jsonl` + 信任 sidecar，**不**写迷雾/科技树。切片含 `navyShipCount`、`borderThreats`（可见敌军分桶，无坐标）、`proximity`/`sameContinent`/`landReachable`。聊天默认禁议第三国细节，除非信任 ≥0.65 或本轮情报交换。

不要发明第三套 `/v1/chat`。ChatUI 是 AgentAPI 客户端。

## 游戏内网关（W1 已实现，127.0.0.1 only）

Kotlin `AgentGateway`（默认端口 **8081**）暴露：

| 端点 | 说明 |
|------|------|
| `POST /console/gm` `{"command":"…"}` | 全指令树，仅本机开发者 |
| `POST /console/agent` `{"command":"…"}` | 同一指令树过 `AgentWhitelist`；W1 只放行 `diplomacy list`（读）与 `diplomacy declare-war <本席> <对方>`（本席必须是登记的 CivAgent 席，真人席一律拒绝） |
| `GET /v1/slice?civ=<civID>` | 最小 [ObservationSlice](observation_slice.schema.json) |
| `GET/POST /seats` | 查看/登记 CivAgent 席位（`{"agentSeats":["Rome",…]}`） |
| `GET /healthz` | `{"status":"ok","gateway":"agent","gameId":…,"turn":…}` |

回包 JSON：`{"status":"ok|hint|error|rejected","message":…,"isOk":bool}`；rejected 返回 HTTP 403。

W2 时 `TurnHook`（G4）将拦截 `automateCivMoves`，按席位调 `POST /v1/decision`，把 `Intent[]` 经中间件（G3 白名单 → W4 确认弹窗/贸易槽）翻译成引擎调用——网关的 console 通道届时只保留 gm 档做调试。

## Intent 白名单（活人）

`declare_war` / `make_peace` / `propose_trade` / `buy_luxury` / `open_border` / `research_agreement` / `seek_peace` / `form_ally` / `mutual_defense` / `share_intel` / `denounce` / `chat`

禁止：`addstat`、点格子、改好感字段、直接写存档。物理不变量（amount ≤ treasury 等）由引擎/工具校验，不靠提示词。

## 版本

- v1（2026-09-06，W1）：`ObservationSlice` 最小切片（金/增速/快乐/资源存量/已知文明/关系/性格占位）；`Intent` 白名单冻结，W1 中间件只执行 `declare_war`。
