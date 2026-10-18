# CivAgent 计划：架构 · 不变式 · 决策 · 偏差

> 大脑仓：`C:\Users\<user>\Desktop\Civagent_repo`
> 游戏仓：`C:\Users\<user>\Desktop\Unciv-Source`（fork 自 [Permanent995/Unicv-RC](https://github.com/Permanent995/Unicv-RC)，基线 **4.21.4-patch1**）
> 玩家客户端：`C:\Users\<user>\Desktop\Unciv-SongDynasty`（jpackage）
> 重写日期：**2026-09-11**（基于两仓代码全量审计；上一版存档于 `PLAN.backup-20260911-130519.md`）

**两份文档的分工**

| 文档 | 回答什么 | 变化频率 |
|------|----------|----------|
| **本文 `PLAN.md`** | 为什么这样设计、什么绝不能违反、未建机制的规格 | 慢 |
| [`EXECUTION.md`](EXECUTION.md) | 现在做什么、怎么验收、做过什么 | 快 |

改代码前读 [§2 不变式](#2-不变式违反即回归) 和 [§3 已知偏差](#3-已知偏差代码与决策不一致)。领任务读 `EXECUTION.md`。

---

## 0. 一屏现状

**能玩了。** 单人开局勾「Fill empty seats with CivAgent」→ 游戏自动拉起本地 Python 大脑 → AI 席位每回合由 LLM 出外交意图 → 玩家确认后落地 → 顶栏聊天图标可与任意已接触的 CivAgent 席位实时对话（流式）。密钥存本机 sidecar，不进存档、不进联机。

| 维度 | 现状 |
|------|------|
| 游戏侧 | `logic/civagent/` 15 个模块 + 4 个 UI 弹窗；**48** 个单测；已提交为 `a44305038 feat(civagent): v0.1.0` |
| 大脑侧 | `deployment/ai/agent_api.py`（Flask :2335，11 条路由）；**90** 个 pytest 用例 |
| 分发 | `Unciv-SongDynasty/app/Unciv.jar` = 55,997,986 B（2026-09-11 12:45）；`Unicv-RC-1.0-win64-fixed.zip` 98.1 MB |
| 契约 | `docs/protocol/`：ObservationSlice **v2**、Intent **v1** |
| 已完成 | 网关/白名单/切片/回合钩子/确认弹窗/贸易槽/聊天/情报信任/模组兼容/内容桥/密钥隔离/自动起脑 |
| 未完成 | 见 [§3 偏差](#3-已知偏差代码与决策不一致) 与 `EXECUTION.md` 任务板 |

**基线已锁，不再讨论**：玩家端 = RC 4.21.4-patch1 jpackage；不跟上游 4.21.16+ rebase；Python 只解析 4.21.4 schema；执行入口 = 游戏内网关，不是改存档、不是 `NEED_POST`。

---

## 1. 架构

### 1.1 四个进程，不合并

```
CivAgent Host（脚本 / AgentBrainLauncher）
    └─ ai_server (Python, :2335)      大脑：只出「说」与「意图 JSON」

Unciv-RC 游戏进程（LibGDX，持有活的 GameInfo）
    ├─ AgentGateway (:8081, 127.0.0.1)  控制台外部化 + 只读查询
    ├─ ObservationSlicer                按本席裁迷雾
    ├─ AgentTurnHook + IntentExecutor   收意图 → 审查 → GL 线程执行
    ├─ NativeAI（原生）                 单位/工人/城市/科技微观
    └─ LeaderDialoguePopup              聊天 UI

UncivServer.jar (:8080)                 仅联机：PUT/GET 存档文件
```

**为什么不合并**：存档仓库没有棋盘；只有游戏进程持有活的 `GameInfo` 且必须在 GL 线程改状态；Python 碰 LibGDX 会把 GL 线程、生命周期、崩溃域绑死。跨进程只传 JSON，不做对象互操作（不用 JPype）。

### 1.2 三层：看 / 说 / 做

| 层 | 谁 | 何时 | 边界 |
|----|----|------|------|
| **看** | `ObservationSlicer`（Kotlin） | 每次决策/聊天前按席位裁切 | 只给引擎算好的具名字段，不给全图、不给上帝视野 |
| **说** | `agent_api` + `LeaderDialoguePopup` | 玩家打字后**立刻** | 打开界面零次 LLM（用原版 `neutralHello`）；说不改棋盘 |
| **做** | `IntentExecutor` + 原生日程 | 仅该席位的 `automateTurn` | 白名单意图；玩家确认；拒绝则回原生 |

**「说立刻、做等回合」** 是产品硬约束：Unciv 是 I-go-you-go 回合制，外交语言可以即时，条约和军队必须等该席位的回合。不要做「聊一句就当场宣战」。

### 1.3 一次 CivAgent 回合的真实时序

```
玩家点下一回合
  → NextTurnAutomation.automateCivMoves(该席位)
      → respondToTradeRequests（原生）
      → AgentTurnHook.applyIntents：
           ObservationSlicer.slice() → POST /v1/decision（15s 超时）
           大脑：verify_pending_claims（信任证伪）→ LLM → Intent[]
           + 排队的聊天草稿意图
           → IntentNarrator 人话清单 → 确认弹窗（30s，拒绝/超时 = 整批不落地）
           → IntentExecutor 逐条执行（规则集闸门 / 席位 / 国库保险丝 / 贸易单槽）
           → AgentMod.recordAgentActions（AI 席位没有引擎通知，自建行动流水）
           → POST /v1/applied 回执
      → ✅ Gap-1 已修：成功落地的意图类别当回合跳过对应原生调用（2026-09-11）
      → 原生微观：科技 / 建造 / 单位 / 政策
```

---

## 2. 不变式（违反即回归）

这 8 条是从真实事故里提炼的。每条都有过违反记录，改动涉及时必须逐条对照。

### I-1 密钥只活在大脑进程，游戏零密钥

解析序：**进程环境变量 > 本机 sidecar `~/.civagent/secrets.json` > `config.yaml` / `config.local.yaml`**。

- 游戏请求体**永不**带 `Authorization` 或 key；`/v1/config` 只回 `keyHint`（后四位）。
- **禁止**写 `gameParameters.llm_api_key`——联机会把整份 `GameInfo` gzip 后 `PUT /files`，单人分享存档同样带走。伏羲踩过这个坑。
- 日志经 `redact()` 清洗 `sk-*` 与 `Bearer *`。

> 现状：已守住。`grep llm_api_key` 全仓 0 匹配；`AgentSecretsStoreTest.roundTripDoesNotTouchGameParameters` 常驻回归。

### I-2 广告集 == 可执行集

`INTENT_TYPES`（Python）/ `intent.schema.json` / `intent_type_list()` / `IntentExecutor`（Kotlin）**四处必须一致**。做不到的意图不广告。

> 事故：模型发 `seek_peace`（旧词汇），`IntentExecutor` 只认 `make_peace` → 落到 `else` → 玩家确认了、界面说议和了、**实际没议和**。修法有三条：同义归一（`seek_peace`→`make-peace`）、删掉做不到的（`common_enemy`）、补齐能做的（`open_border` / `research_agreement` / `mutual_defense` / `form_ally`）。
> 现状：Kotlin 与 Python 已对齐，**`intent.schema.json` 仍残留 `common_enemy`**（Gap-3）。

### I-3 失败必须可见，不假装成功

确认弹窗在**执行之前**，所以执行结果必须回流：`ok:false` + 明确理由 → `AgentIntentReporter` Toast + `recentAgentActions` + `/v1/applied` 回执 + 下次提示词里的「不得自相矛盾」。引擎拒绝（未相遇、规则集禁止、国库不足）一律如实报，不写模糊成功。

### I-4 Agent 是玩家，不是 GM

- 白名单之外一律拒绝：`civ addstat`、`unit add`、点格子、改 `diplomaticModifiers`。
- 钱不能无中生有：贸易执行前校验 `gold ≤ 国库`、`gold_per_turn ≤ 本回合收入`、资源 ≤ 持有量。
- 规则集说不行就不行：`DiplomaticRelationshipsCannotChange` 命中则拒绝宣战/议和/谴责（D1）。
- `console_gm` 全指令档**只给开发者本机**，Agent 运行时永不使用。

### I-5 迷雾在游戏进程里裁，裁完才给模型

`ObservationSlicer` 以该席位为 `viewingCiv`：只输出 `isExplored` 的地块摘要、`isVisible` 的敌军分桶（**无坐标**）、已接触文明。`DebugUtils.VISIBLE_MAP` 禁止给 Agent。他国数据遵守 `gameParameters.hideOtherCivilizationStats`。

Python 读整份存档可以（旁路调试），**送进 LLM 前必须经过同一裁切**。

### I-6 玩家主张是谣言，不是真相

`share_intel` / 上传的截图**只写 `claims.jsonl` + 信任 sidecar**，绝不写迷雾、科技树、`exploredBy`。信任分 `(gameId, 我, 对方) → [0,1]` 独立存放，**不改 Unciv 的 `opinionOfOtherCiv`**（好感驱动贸易定价与宣战门槛，改了旧档和原生 AI 全坏）。

证实/证伪只认引擎能观测的类型（`war` / `peace` / `massing_troops`）；不可观测的（如核弹研究）永远保持 open，不用窗口误判。**第一次报信禁止默认「你想骗我」**；只有证伪之后谴责才合法。

### I-7 一席一上下文，串行调用

会话键 `(gameId, civId)`，每次请求无状态组装：身份 → 性格 → 本席迷雾 → 当前对手 → 这对文明最近 N 句。

- **禁止**一次补全里同时演多国（人设必丢，还会泄漏他国迷雾）。
- 席位之间**串行**（跟着 `nextTurn` 的文明表），不 `asyncio.gather`——并行会让 `tradeRequests` 预扣打架。
- 聊天与 `/v1/decision` 共用**同一把席位锁**。
- 每国一把钥匙 / 每国一个模型进程：**不做**（费用 ×N，人设不会更稳）。

### I-8 棋盘微操永远归原生 AI

LLM 只出**战略意图**（宣战/议和/贸易/情报/态度）。单位走位、攻城选目标、造什么、研究什么、采纳什么政策，全部调用 `UnitAutomation` / `ConstructionAutomation` / `WorkerAutomation` / `chooseTechToResearch`。

这条同时是**模组兼容的地基**：原生 AI 读 `ruleset`，模组改什么都自动跟上；意图契约里没有任何单位名/科技名/建筑名。

Agent 失败或超时 → 整段回原生，保证玩家永远能点下一回合。

---

## 3. 已知偏差：代码与决策不一致

审计（2026-09-11）逐行比对得到。按修复优先级排序，任务见 [`EXECUTION.md`](EXECUTION.md)。

### Gap-1 外交是「叠加」不是「覆盖」— 严重 ✅ 已修（2026-09-11，方案 A）

> **已落地**：`AgentTurnHook.applyIntents` 返回覆盖类别（DIPLOMACY/TRADE），`nativeSkipSet` 门控 `NextTurnAutomation`——本批成功落地的外交类别（宣战/议和/谴责）与贸易（`exchangeLuxuries`）当回合跳过原生，未覆盖类别照走原生；脑失联/意图失败 = 零覆盖 = 完整原生。回归测试三路径全绿。

**决策**：CivAgent 席位仍标 `PlayerType.AI`，但在 `automateCivMoves` 入口拦截，**外交/宣战/议和/贸易由 Agent 意图覆盖**，只有微观留给原生。

**代码**（`NextTurnAutomation.kt:51-68`）：`applyIntents` 之后，原生外交全套照跑——

```
AgentTurnHook.applyIntents(civInfo)
DiplomacyAutomation.declareWar / offerPeaceTreaty / askForHelp / offerDeclarationOfFriendship
DiplomacyAutomation.denounce / offerToEstablishEmbassy / offerOpenBorders
DiplomacyAutomation.offerResearchAgreement / offerDefensivePact
TradeAutomation.exchangeLuxuries
```

**后果**：① 席位外交人格分裂——LLM 刚议和，同一回合原生 AI 可能又去宣战或谈别的；② `AgentTradeSlots` 保证的「每对文明一条挂起报价」被 `exchangeLuxuries` 直接破坏；③ 玩家在确认弹窗看到的清单**不等于**这一回合实际发生的外交，与 I-3 冲突。

**注意**：这不是简单地把原生外交删掉。需要先定产品语义——建议「意图批次成功落地的**那几类**外交，本回合跳过对应的原生调用」，未被 Agent 覆盖的仍走原生（否则 LLM 沉默时席位会完全不做外交）。

### Gap-2 性格锁从未接通 — 中 ✅ 已接通（2026-09-11）

> **已落地**：`ObservationSlicer.renderPersonality` 读 `civ.getPersonality()`（= `ruleset.personalities[nation.personality]`），偏离中性的维度渲染为短文本（如 "declarewar 8, loyal 3"）写入切片；大脑 `seat_personality` **优先切片值**，config 仅作覆盖，中性/缺失回退原逻辑。测试：引擎性格进切片 + 大脑优先级 + 人类席 neutral。

**决策 8.3 / 6**：开局锁定 `nation.personality`（规则集里的侵略/忠诚/外交等 0–10 系数），把同一份数值写进 Agent 系统提示，与原生 AI 用的是同一套。

**代码**：`ObservationSlicer.kt:32` 声明了 `val personality: String? = null`，`slice()` **从未赋值**；大脑 `agent_api.py:280 seat_personality()` 从 `config.yaml` 的席位块取，取不到就返回 `"pragmatic ruler"`。

**后果**：实际所有席位共用同一个默认人格。角色差异目前**只由 `leader_voice` 的说话风格提供**，战略倾向没有差异——「侵略 8 / 忠诚 3」这类系数完全没进模型。

### Gap-3 契约漂移：`common_enemy` — 低（但违反 I-2）✅ 已对齐（2026-09-11）

> **已落地**：schema 枚举删 `common_enemy`、`intel.kind/aboutCiv` 补进 schema、README 同步；`tests/test_protocol.py` 把「INTENT_TYPES == schema == README」固化为不变式测试（I-2 由 CI 守住，不再靠人记）。

`docs/protocol/intent.schema.json` 的 type 枚举仍含 `common_enemy`，Python `INTENT_TYPES` 与 Kotlin `IntentExecutor` 均已移除。schema 是给两端看的唯一契约，留着就是下一次 `seek_peace` 事故的种子。

同时 `IntelPayload` 在 Pydantic 里有 `kind` / `aboutCiv`，schema 只有 `claim`。

### Gap-4 两套围栏解析，其中一套是死代码 — 低 ✅ 已删（2026-09-11）

> **已落地**：`ChatReplyParser.kt` 及其测试删除；围栏解析只在大脑一端，游戏端直接消费 SSE 事件里的结构化 `intents[]`——两端解析规则不再可能漂移。

`ChatReplyParser.kt` 全仓只有测试调用；`LeaderDialoguePopup:493` 直接用 SSE 事件里大脑已解析好的 `intents[]`。保留一份未被调用的解析器，将来两端解析规则会漂。

### Gap-5b 记忆只有固定窗口，无事件流 — 中 ✅ 已解决（2026-09-11，P-memory）

> **已落地**：引擎原生通知（notificationsLog，天然带迷雾语义）经游标差分随 `/v1/decision` 上行，落盘为 `engine_event` 记录；`select_memory()` 三层检索（近因 12 + 全历史显著事件 8 + 对手相关 6，纯 jsonl 过滤）取代固定窗口——长局远期事件（如"第 9 回合被宣战"）重新可及。**仍未引入向量库/SQLite**，与决策 23 一致；语义检索继续留给 D1 飞轮阶段评估。
> **信任可追溯**：`bump_trust` 全部带 reason/evidence 落 `trust_log.jsonl`，`GET /v1/trust` 可查每个席位信任变化的完整证据链。

### Gap-5 贸易槽只在内存 — 中 ✅ 已对账（2026-09-11）

> **已落地**：`AgentTradeSlots.reconcile(gameId, civ)` 在每席回合开始（`applyIntents` 入口）与引擎对账——引擎侧 `tradeRequests` 已无对应挂起请求（被接受/拒绝/过期）即清槽，**引擎为唯一事实源**。重启/读档后槽为空也无害：空槽不撤旧、`put` 本就先撤引擎侧旧请求。另外 key 已改为文明对对称（`rome|egypt` ≡ `egypt|rome`）。

`AgentTradeSlots` 是进程内 `HashMap`。游戏重启 / 读档后，"这对文明已有一条挂起报价" 的记账丢失，可能重复投递。引擎侧 `tradeRequests` 本身在存档里，两者会不一致。

### Gap-6 多模态只有搬运，没有视觉 — 中

`POST /upload` 落盘（≤4MB）+ `/message` 把**路径字符串**拼进提示词。**没有任何 vision / OCR / 分类**。决策 16 要求的「无关 / 外交相关 / 疑似泄雾」三分类与「默认不把识别结果扔给玩家」全部未实现。现状等于让模型对着一个它看不见的文件名说话。

### Gap-7 伏羲旧栈整体是死代码，仍在仓库 — 低（认知负担）

活路径只有 `agent_api` 一条。以下**全部不被引用**：`deployment/ai/ai_server.py`（仍 `init_jvm()`）、`strategy.py`、`free_chat.py`、`deployment/chatbot/*`、`deployment/unciv_server/`、`redis_mq.py`、`mq_listener.py`、`civagent/civagent.py`、`search.py`、`skills.py`、`workflow.py`、旧 LLM utils、`civsim/`。7 个旧测试文件默认跳过（`CIVAGENT_LEGACY_TESTS=1` 唤起）。

文档里写 `ai_server`、代码里跑 `agent_api`，这个不一致已经误导过一次。

### Gap-8 测试盲区 — 中

Kotlin 48 个用例**不覆盖** `ObservationSlicer`（含 `hideOtherCivilizationStats` 裁切）、`AgentWhitelist`、`AgentTradeSlots`、`IntentNarrator`、`AgentBrainLauncher`。Python 90 个用例**不覆盖** `/upload`。切片是 I-5 迷雾不变式的唯一执行点，零测试很危险。

---

## 4. 决策索引

### 4.1 已固化，不再讨论（结论保留，论证已归档）

| # | 结论 |
|---|------|
| 1 | 在 Unciv-RC 源码上打补丁，jpackage 重打；控制台外部化 + 白名单两档 |
| 2 | 停在 4.21.4-patch1，不跟更新的上游；伏羲 4.8.17 只当参照 |
| 3 | 联机用官方 `UncivServer.jar`（同 patch），Python 不实现 `/files` |
| 4 | 继承伏羲的产品（LLM 数字玩家、存档即世界状态、动作空间），不继承其部署（四进程 Redis、Discord、JPype） |
| 5 | 席位互斥 Human / CivAgent / Native AI；全真人则 Agent 静默；对话记忆写本地文件 |
| 6 | 不微调基座，只改提示与技能；性格用规则集模板；工具管物理约束，提示管权衡 |
| 7 | UncivServer 是存档仓库，不执行 CLI；跨语言只传 JSON；CLI 由游戏内网关执行 |
| 8 | 单机不必起 UncivServer；原生 AI 按席位切开不删；性格读规则集；不让 Agent 假装成玩家 AutoPlay |
| 9 | 资源价值跟引擎 `TradeEvaluation`，不另训模型；玩家可选「默认执行」或「先确认」，拒绝则本回合回原生 |
| 10 | 仍是回合制：说立刻、做等回合；跨回合状态就是 `GameInfo` + 旁路对话日志，不需要额外数据库 |
| 11 | 禁止改写存档 JSON；中间件五道审查（schema → 白名单 → 席位 → 引擎合法性 → 本回合去重）；一回合一对文明一个贸易槽 |
| 12 | 一人设一上下文，共用同一个 API，不分进程子代理；按引擎席位串行 |
| 13 | Agent 只当战略大脑；闲置台词/开场白用原版 `Nation` 字段（零次 LLM）；语言跟 `GameSettings.language` |
| 14 | 纯聊天体验 HDS Interlude 更强，但**不 vendoring**（AGPL 传染），只借鉴分层与调度 |
| 15 | Interlude 特化为「持续治国的领导人」：幕间用 Unciv 回合，不用墙钟编生活 |
| 17 | 聊天入口 = 世界地图旁 Popup（非外交屏、非联机 ChatButton）；情报是谣言不是开雾；信任独立于好感 |
| 18 | Agent 不点单位；攻城只下到「对谁开战」，可选 `war_focus`，不可达则降级 |
| 19 | 地图按引擎算好的特征理解（距离档/同大陆/可达/海军数），不读像素、不给全图 |
| 20 | 聊天门面兼容 coder/agentapi 形状；落子走原生 `/v1/decision`；不嵌 `agentapi server`、不用 PTY |
| 21 | 密钥见 [I-1](#i-1-密钥只活在大脑进程游戏零密钥) |
| 23 | 不为切片上 SQLite / 向量库：棋盘就是世界库，对话用文件 |
| 26 | 内容桥同样不需要数据库 / 向量库：规则集在内存且是精确键查表 |
| 30 | 见 [I-2](#i-2-广告集--可执行集) 与 [I-3](#i-3-失败必须可见不假装成功) |

### 4.2 仍在指导日常改动

**决策 16 — 多模态先分类再进同一套「说」。** 图片不是第二条通道：先过体积/类型门禁 → 分类（无关 / 外交相关 / 疑似泄雾或作弊）→ 相关的转成 ≤N 字事实观察，与打字走同一逻辑。默认**不**把 OCR 结果整段扔给玩家（领袖用口吻说话，不当识图 API）；只有玩家明确问「你看见什么」才允许一句话描述，且仍受迷雾约束。无视觉模型则要求用文字说明，不猜测。二进制不进存档。**当前仅实现搬运，见 Gap-6。**

**决策 22 — 观察切片传什么。** 每科目**存量 + 增速成对**（金库/`goldPerTurn`、已投入科研/科研增速、`storedCulture`/文化增速、每城建造进度/产能、每城粮仓/食物增速、快乐当前值、资源 `getResourceAmount`）。加体量与排名、已知大国 6 字段 + `vsSelf` 档位、RC `factionBalance`、地图特征（距离档/同大陆/可达/海军真船数/边境威胁分桶）、`recentAgentActions`、`incomingTradeRequests`、`agreements`/`modifiers`、`ruleset` 指纹与 `resourceKinds`。

**禁止进切片**：整份 `tileMap`、每格产量、每单位 `getForceEvaluation`、`statsHistory` 全序列、完整建造队列与科技树、存档字母码、伏羲的假海军字段。

聊天包用同一份切片的**缩减版**（本席顶栏 + 当前对手），不比决策包大。

> 注：Unciv 对 `PlayerType.AI` 席位 `addNotification` 直接 return，所以 Agent 没有引擎通知流——行动记录必须自建（`AgentMod.actionLog` → `recentAgentActions`）。

**决策 24 — 模组兼容。** 棋盘微操归原生 AI（I-8）；意图契约只含外交，无内容名；该从规则集取的不写死（资源 `kind`、国家名、时代序）；未知一律降级回原生，绝不崩。缺陷矩阵与分期见 [docs/mod-compatibility.md](docs/mod-compatibility.md)。

**决策 25 — 非原版内容靠投影，不靠训练。** `GameInfo.ruleset` 是已合并的活对象；引擎自带自然语言（`getCivilopediaTextLines` + `Unique.text`，638 个模板且未知 unique 也有文本）。四层：L1 内容卡投影（字段 → 能力标签 `caps`）→ L2 增量摘要（vs 原版基线的集合差）→ L3 网关 `GET /v1/content` 按需查询 → L4 意图按规则集校验执行。详见 [docs/mod-content-bridge.md](docs/mod-content-bridge.md)。

### 4.3 未实施的机制规格（RC 自研，与 CivAgent 正交）

这三块是下一阶段主线，体量大于剩余的 CivAgent 收尾工作。**纯引擎逻辑，不依赖 LLM**。

#### 决策 27 — 控制力（Control）：城市第 8 个计数 + 补给奖惩

- **范围（全局）**：所有文明的每一座城市，含 AI 与城邦（城邦也是 `Civilization`、也有 `getUnitSupply()`）。
- **取值**：已建立城市初始 **50**，每回合 **+2**，上限 **100**（50→100 需 25 回合）。攻占后选**傀儡**的城市从 **1** 开始，同样 +2。
- **驻军**：有驻军额外 **+3/回合**（合计 +5）。
- **抵抗期**：攻占后的抵抗回合内**控制力不涨**，奖惩也不生效。**这是唯一的暂停条件。**
- **易主不中断（定案 2026-09-11）**：控制力是城市自身的属性，**不因换主人而重置或衰减**。交给代理人统辖的城邦期间照常增长，召回时原样带回（详见[决策 29](#决策-29--代理人傀儡城邦) 的「控制力衔接」）。城邦本来就在上面「范围（全局）」的作用域内，所以这条不是新规则，只是把推论写明。
- **补给奖惩**：Unciv 的 `getUnitSupply()` = base + 城市项 + 人口项，其中城市项 = 城数 × `unitSupplyPerCity`。本机制把**城市项改为逐城求和**（每城 = `unitSupplyPerCity` + 档位），**人口项不动**：

  | 控制力 | 该城补给贡献 |
  |--------|--------------|
  | ≥ 80 | **+4** |
  | [50, 80) | **+3** |
  | [40, 50) | **+2** |
  | < 40 | **−2** |

- **UI**：城市界面右上角在原版 7 个计数外加**第 8 个「控制力」**；城市统计界面加「影响力」；图标自设（拳头 / 天平 / 旌旗）。
- **实现要点**：`City` 新字段（带默认值、进存档）；`CityFounder` 初始化 50（傀儡 1）；回合处理累加并跳过抵抗期；`getUnitSupplyFromCities()` 改逐城求和。切片可选透出。
- **必须是存量字段，不能是推导值。** 别实现成 `gameInfo.turns - city.turnAcquired` 之类——`CityConquestFunctions.moveToCiv()`（`:318`）每次所有权转移都会重置 `turnAcquired`，推导式实现会在每次交出 / 召回时把控制力清零，直接违背上一条。

#### 决策 28 — 内阁：间谍图标改为内阁总入口

- 顶栏**间谍图标**从「直接开间谍屏」改为「内阁系统总入口」。
- **原间谍屏保留**：先开内阁，点「**间谍首脑**」才进入原间谍 UI（间谍屏成为内阁下的一个部门）。
- 数值设计参考 Unciv 原版既有系统（间谍/外交/政策）。
- **UI 与数值已定案**（2026-09-11，据参考图 `内阁系统.html`）：见本决策末尾「UI 与数值定案」。

**实现形态（2026-09-11 定案）：整体仿照原版间谍系统。**

间谍系统在引擎里是一套完整且自洽的「人事实体」模板，七个部件齐全。内阁与代理人（决策 29）**照抄这套骨架**，不要另起炉灶，也不要走单位管线（理由见决策 29）。

| 部件 | 间谍系统 | 内阁 / 代理人对应物 |
|------|----------|---------------------|
| 数据模型 | `core/src/com/unciv/models/Spy.kt`——普通 `IsPartOfGameInfoSerialization` 类，`name` / `rank` / `location: HexCoord?` / `action` 枚举带倒计时 / `clone()` / `setTransients()` | **一个模型**：`models/Official.kt`（官员）。字段见下「官员数据模型」；`assignment` 决定他此刻是阁臣、代理人还是在野（[决策 30](#决策-30--官员统一模型阁臣与代理人是同一种人的两种任命)） |
| 管理器 | `logic/civilization/managers/EspionageManager.kt`——挂 `Civilization`，持 `spyList`，`clone()` / `setTransients()` / `endTurn()` | `managers/CabinetManager.kt`——持**一个** `officials` 池；`endTurn()` 按 `assignment` 分流：在任阁臣结算国力五维，外派代理人结算忠心 / 任务 / 上贡 |
| 回合驱动 | `EspionageManager.endTurn()` 逐个调 `spy.endTurn()`，动作带倒计时 | 同构；忠诚度升降与上贡倒计时都在这里，不散落进 `TurnManager` |
| 入口按钮 | `ui/screens/worldscreen/TechPolicyDiplomacyButtons.kt` 的 `espionageButton` + `updateEspionageButton()` | 沿用**间谍图标**开内阁屏，但**不沿用该按钮的生命周期**（门控陷阱见下）；原 `EspionageOverviewScreen` 由内阁里的「间谍首脑」部门打开 |
| 总览屏 | `ui/screens/overviewscreen/EspionageOverviewScreen.kt` | `CabinetScreen`（部门列表 → 各部门屏） |
| 单位面板复用 | `ui/screens/worldscreen/unit/presenter/SpyPresenter.kt` 实现 `UnitTable.Presenter`，`position` 由所在城市推导 | **只有外派为代理人的官员需要**：`AgentPresenter` 同构——选中代理人像选中单位一样有信息面板，但它不是单位。**在阁 / 在野的官员不做 Presenter**：他们在地图上没有位置，只活在内阁屏 |
| 闲置提醒 | `status/NextTurnAction.kt` 的 `shouldShowMoveSpies()` / `dismissedShouldMoveSpies` | 「有代理人待分配 / 任务已完成」同款提醒 |

**命名池**：`Spy` 的名字取自 `nation.spyNames`，缺失时回退 `"Spy N"`。代理人同样**必须带兜底**，否则模组国家没有名字池就会崩（决策 24）。

**两处要改的门控**（已核 `ui/screens/worldscreen/TechPolicyDiplomacyButtons.kt`）

1. `updateEspionageButton()`（`:198`）在 `spyList.isEmpty()` 时把 `actor` 置空**隐藏**按钮。改成内阁入口后不能沿用——否则没间谍就进不了内阁。
2. **更致命的一层**：`:95` 与 `:120` 用 `game.gameInfo!!.isEspionageEnabled()` 把该按钮的**构造与更新整段**包住，而它就是开局参数 `gameParameters.espionageEnabled`（`GameInfo.kt:338`）。**关掉间谍的对局里这个按钮根本不会被创建**，内阁将完全不可达。

**结论：内阁入口不复用间谍按钮的生命周期。** 改照同文件里 RC 自己的 `factionBalanceButton`（`:107-111`、`:213-221`）——无条件构造，只在旁观者 / 蛮族时隐藏。间谍图标仍可作为内阁入口的图标；`isEspionageEnabled()` **下沉**为内阁屏里「间谍首脑」那一格的可用性条件：关了间谍，内阁照开，只是这一格灰掉。

**内阁入口一共三道门，别混在一起写**：① `gameParameters.cabinetEnabled`——本局有没有这套系统（[决策 32](#决策-32--可选启用开局开关不是模组)）；② 科技「文官制度」——本局什么时候解锁（[决策 31](#决策-31--官员招募政策触发遴选)）；③ 旁观者 / 蛮族——这个席位该不该有。三者语义不同，`isEspionageEnabled()` 则**不在其列**，它只管「间谍首脑」那一格。

**UI 与数值定案（2026-09-11，据参考图 `内阁系统.html`）**

*布局*：采用参考图三种布局中的「**二三排列**」——朝堂场景，后排两席立于陛阶之上，前排三席在阶下，点人物本体或其脚下姓名牌开详情弹窗。参考图另有「竖排」与「五宫格」：**竖排保留为窄屏兜底**（理由见下「移植代价」），五宫格不做。

*五职位*（`CabinetPost` 枚举，声明顺序即排位）

| 排 | id | 职位 | 参考图形象 | 对应引擎面 |
|----|----|------|------------|------------|
| 后 | `marshal` | 军事统帅 | 赤袍・盔・剑 | 军事 / 单位 |
| 后 | `spy` | 间谍首脑 | 青灰袍・兜帽・匕首 | **原版 `EspionageOverviewScreen` 的入口格** |
| 前 | `diplomat` | 外交大臣 | 紫袍・幞头・笏 | 外交 / 城邦 |
| 前 | `treasurer` | 财政管家 | 金袍・圆帽・算盘 | 金币 / 贸易 |
| 前 | `priest` | 教化祭司 | 绿袍・冕・简 | 快乐 / 科技 / 宗教 |

*官员数据模型*（`models/Official.kt`；全国**一个池子**，五个内阁席位与 N 个代理人外派位都从池里取人，见[决策 30](#决策-30--官员统一模型阁臣与代理人是同一种人的两种任命)）

| 字段 | 含义 | 备注 |
|------|------|------|
| `name` / `zi` | 姓名 / 字 | `zi` 可缺省 |
| `rank` | 品级 | 参考图仅「正一品 / 从一品」，**纯展示** |
| `faction` | 出身 | 世家 / 寒门 / 宗室 / 宦官 / 方外，**参考图中不参与结算** |
| `attrs` | 四维**天资** | `war` 武力 / `int` 智谋 / `loy` 忠诚 / `prs` 威望，各 0–100。**静态，终生不变** |
| `talent` | 天赋 | `{name, desc, cond:{k,v}, nat:{...}}`，见下触发规则 |
| `stance` | 政见倾向 | **简化为「文臣 / 武将」粗略二分**（参考图的四轴武将 / 文官 / 外戚 / 百姓收成两类），**纯展示、不参与结算** |
| `bio` | 生平 | 纯文本，允许缺省 |
| `relations` | 关系网 | 对其余四职 `pos` / `neg`，**参考图中不参与结算** |
| `assignment` | 当前任命 | `Idle`（在野）/ `Cabinet(post)`（阁臣）/ `Agent(cityStateName)`（外派代理人）。**决策 30 新增** |
| `morale` | 运行时**忠心** | 0–100，**只在 `Agent` 任命下有意义**；受命时以 `attrs.loy` 为初值，之后按决策 29 三条件升降。**决策 30 新增** |

> **`attrs.loy` 与 `morale` 是两回事，不要合并。** 前者是静态天资，进 `S.loy` 参与内阁团结与 ctrl/gold/happy 的加权；后者是外派后每回合变动的状态，跌破阈值触发独立战争。用一个字段会导致「代理人闹独立顺带把内阁团结拉垮」这种谁都没设计过的耦合。

*结算公式*（照抄参考图 `updateSummary()`，勿自行改动否则与原型对不上）

- **属性综合** `S[k] = Σ` 五卿的 `attrs[k]`，每项满值 500。
- **天赋触发**：`S[talent.cond.k] ≥ talent.cond.v` 才生效。参考图样本阈值落在 250–460。**注意这是对全内阁求和后判定**——所以天赋触发与否取决于**内阁整体搭配**而非单人挑选，这是该设计的核心趣味点，别改成按人判定。
- **内阁团结** `unity = clamp(20, 99, round(S.loy/5×0.60 + S.war/5×0.15 + S.int/5×0.15 + S.prs/5×0.10))`。
- **国力五维** `value_k = clamp(1, 99, round(base_k + Σ 已触发天赋的 nat[k]))`，其中 `base_k = (Σ_j (S[j]/5) × w_k[j]) × 0.80`（`S/5` = 五卿均值 0–100；`×0.80` 压到 0–80，给天赋留余量）。权重 `w_k`：

| 维 | 名称 | 权重 |
|----|------|------|
| `ctrl` | 控制力（**国家级全局**，非城市级） | `loy` .55 + `war` .45 |
| `gold` | 金币收入 | `int` .60 + `loy` .40 |
| `happy` | 快乐度 | `prs` .50 + `loy` .50 |
| `tech` | 科技增加 | `int` .70 + `prs` .30 |
| `fav` | 好感度 | `prs` .70 + `int` .30 |

*详情弹窗*（点人物 / 姓名牌 / 卡片进入）：立绘 + 姓名・字 + 「职位 · 品级」标签 + 四维条 + 出身标签 → 生平 → 天赋（含「触发条件：X 综合 ≥ N」一行）→ 政见倾向（文臣 / 武将）→ 关系网 → 底部三键「罢免 / 提拔 / 选为代理人」（动作语义见[决策 31](#决策-31--官员招募政策触发遴选)）。总览区固定在屏底：国力五维五格（含「基准 N · 天赋 ±M」的拆分说明）+ 属性综合四条 + 内阁团结 + 天赋加护清单（**未触发的也要列出并显示「尚差 N」**——这是引导玩家调整搭配的主要反馈）。

*移植代价（这一条决定工期）*：参考图是 SVG——1200×720 的 30° 俯视透视，全部由 JS 现算投影后拼 `<path>`，并用到 `feGaussianBlur` / `feColorMatrix` 柔焦、线性与径向渐变、地面倒影、暗角。**Scene2D 一样都没有**（libGDX 只有纹理与九宫格，无矢量渲染、无滤镜）。因此：

- 朝堂场景**整幅烘焙成位图**，五个人物各出一张透明底立绘，落 `android/Images.Cabinet/`（该层目录会被打包成 `Cabinet.atlas`，经 `ImageGetter` 取用，与 `Images.NationIcons` 等同一约定）；姓名牌、卡片、弹窗用 Scene2D 控件画。
- 人物与姓名牌的命中区固定在 1200×720 的设计空间里，整幅等比缩放贴屏，命中区按同一比例换算——**不要**在运行时复算那套透视。
- 参考图的装饰字体（Ma Shan Zheng / ZCOOL KuaiLe）走 Google Fonts 网络加载，游戏内**不引入**：用 Unciv 现有字体，避免再背一份 CJK 字体的体积与授权。

*模组兼容红线*：参考图的候选池写死了中国历史人物（卫青 / 苏秦 / 董仲舒……）。硬编码 = 所有文明都配一套中国内阁，与决策 24、不变式 I-8 正面冲突。候选池**必须按 `Nation` 走规则集数据**（与 `Nation.spyNames`（`Nation.kt:69`）同一位置、同一手法），且**必须有兜底**：模组国家没有候选池时用「职位名 + 序号」生成占位人物——既不能崩，也不能落回中国人名。`bio` / `zi` 按人一条纯文本、体量大，允许缺省并回退到模板句。

**三处定案（2026-09-11）**

1. **`ctrl` = 当前国家的全局控制力。** 它与[决策 27](#决策-27--控制力control城市第-8-个计数--补给奖惩) 的城市控制力**不是撞名，是同一概念的两个层级**——决策 27 管每一座城（0–100，喂补给档位），内阁 `ctrl` 管**整个国家**。不改名。两者的耦合方式（全局值影响各城增速、还是作为全国性偏移）属实现细节，按下面第 2 条的定位从轻处理，别做成一套复杂的双层结算。

2. **五维是添头，天赋才是正主。** 这一层的设计目的是**把属性凑成能触发天赋的门槛**，五维本身只给少量直接加成，不是核心系统。由此定下实现优先级：

   - **重点做对天赋**——触发判定（`S[k] ≥ cond.v`，全内阁求和）与 `talent.nat` 的实际效果，这是玩家真正感知到的机制载荷。
   - **五维的直接加成从轻**，不必精调权重，也不要为它另建结算管线。落地仍走 `Civilization.temporaryUniques`（`Civilization.kt:201`；`IsPartOfGameInfoSerialization`，进 `clone()`；`TurnManager.kt:330` 每回合 `endTurn()` 递减；已并入 `getMatchingUniques` 检索路径）——`gold` / `happy` / `tech` 天然能写成原版 unique（`[+15]% [Gold]` 之类），由 `CabinetManager` 每回合刷成 `turnsLeft = 1` 的临时 unique。**选它正是因为它最省事**：不碰任何既有结算代码，模组也自动吃得到。唯一要小心的是每回合重刷别与存档残留叠加。

3. **`faction` 与 `stance` 纯展示，`stance` 简化为「文臣 / 武将」粗略二分。** 参考图的四轴（武将 / 文官 / 外戚 / 百姓）收成两类即可，`relations` 关系网同样只做展示。**v1 不与 `factionBalance` 联动**——仓里已有的 EU4 式 `Civilization.factionBalance`（`Civilization.kt:144`，−100 鸽 ↔ +100 鹰，每回合向 0 回归，±50 触发 `getFactionGoldModifier()` / `getFactionStrengthModifier()`，带 `FactionBalancePopup` / `FactionBalanceScreen` 与顶栏按钮）**与内阁无关、独立运行**。它本身是开局开关 `gameParameters.factionBalanceEnabled`（高级设置「启用派系平衡」，默认开，关法则与 `espionageEnabled` 相同：按钮不建、金币/军力修正为 0、不回归、切片不带该字段）。内阁不去驱动它。

   这条合法的依据：参考图里 `faction` / `stance` / `relations` **本来就不参与五维计算**（只有 `attrs` 与 `talent.nat` 参与），所以纯展示不会让任何公式缺项。

   *留个记号*：这样一来玩家会看到两套彼此无关的「派系」界面（内阁里的文臣武将标签、顶栏的鹰鸽天平）。v1 可接受；哪天觉得割裂，最自然的缝合点仍是「内阁整体的文臣 vs 武将倾向驱动 `factionBalance` 漂移」。

#### 决策 29 — 代理人（傀儡城邦）

原版对攻下的城市只有吞并 / 傀儡 / 拆除，傀儡城做不了很多事。用代理人替代「死傀儡」。

**规则**

- 可以把一座**傀儡城市**或**傀儡城邦**交给代理人；傀儡城市交出后**自动变成城邦**（由代理人统辖，不再是我们的城市）。
- 该城邦按代理人命令行动，具备一般城邦功能：**可宣战、可议和**。代理人**初始忠于我们**。

**忠心（`morale`，运行时状态；与官员的静态天资 `attrs.loy` 是两个字段，见决策 30）**

- 受命外派时以该官员的 `attrs.loy` 为初值——**忠诚天资高的人外派更耐造**，这就是四维在代理人侧的直接作用。
- 下降：① 对外作战失利；② 长期得不到援助；③ 我们下发的任务长期未完成。
- 低于阈值 → 要求独立；**拒绝** → 发起独立战争；忠心不足的多个傀儡会**协同反叛**。
- 阈值除忠心外还参考**国力 / 军力 / 文化**（这些是随局势变动的量，不是国家固有属性）。
- **全文明统一阈值，不按国家区分（定案 2026-09-11）。** 不给任何 `Nation` 配初始阈值差异——省掉一份按国家的规则集数据、连带省掉它的模组兜底，也避免「某些文明的代理人天生更容易反」这种玩家无从预期的隐性差异。

**指令**：修建筑与经济、发展科研、扩充军备、上贡资源。任务形态**参考原版**。上贡**间隔可设，默认 10 回合**。

**代理人本体**：**就是一名内阁官员被外派**，不是另一种生物——见[决策 30](#决策-30--官员统一模型阁臣与代理人是同一种人的两种任命)。属性即官员的四维天资，「侧重不同（军事 / 经济 / 科研 / 外交型）」= 四维的偏斜。

**实现形态（2026-09-11 定案）：单开实体，走间谍系统那套骨架，不进 unit 管线。**

代理人**不是** `MapUnit`。三条理由：

1. **语义不对**——`MapUnit` 占地块、耗移动力、能被杀被俘、每回合被 `UnitAutomation` 自动化。代理人坐在城邦政府里，忠诚度/任务/上贡全是回合结算，与地图位置无关。
2. **会破坏模组兼容**——`MapUnit` 必须有 `ruleset.units` 里的 `BaseUnit` 定义。走单位管线意味着 RC 得往规则集塞新单位，任何换基础规则集的模组都不会有它，机制直接失效。这违反决策 24 与不变式 I-8。`Spy` 则完全不碰 `ruleset.units`。
3. **引擎已有现成模板**——`Spy` 的字段形状（名字 / 等级 / 所在城市 / 带倒计时的当前动作）与代理人所需（属性 / 忠诚度 / 所辖城邦 / 当前任务 + 上贡倒计时）一一对应。七件套对照见[决策 28](#决策-28--内阁间谍图标改为内阁总入口)。

UI 上仍可复用单位面板：`SpyPresenter : UnitTable.Presenter` 已经证明「像单位一样被选中并显示信息」**不需要**真是单位。

**引擎映射（已核对源码）**

- 城邦 = 一个 `Civilization` 且 `Nation.cityStateType != null`；`logic/civilization/diplomacy/CityStateFunctions.kt` 负责初始化。
- 「傀儡城市 → 城邦」= 中途新建 AI 城邦文明并接管该城。触发点在 `logic/city/managers/CityConquestFunctions.kt`：`puppetCity()`（`:152`）之后加「交给代理人」分支，**借用 `liberateCity()`（`:203`）已验证的 Resurrection 路径来「建 civ」这一半**。中途建文明另有先例 `ConsoleCivCommands`、`GameStarter`。
  **但城本身必须用 `moveToCiv()` 转过去，不要让它流经 `conquerCity()`**——理由同下方反向路径的陷阱 1 与 2：那条路会掉人口、掠金、并清掉抵抗旗标。**去程与回程用的是同一个干净函数，这样「交出去 → 召回」才是真正对称、可逆、无损耗的。**
- **选「新建独立 Civilization」而非「给城市加个反叛标记」的关键理由**：城邦本身就是 `Civilization`，宣战 / 议和 / 独立战争天然可用，不必为「会造反的城市」另造一套外交。
- 城邦化**复用 `CityStateType`** 可行，但忠诚度/任务/上贡/协同反叛是新增，仍需部分修改。
- 城邦外交复用 `allyCiv` / `getProtectorCivs()`。
- **独立结局视为「解放」**：该城邦可回归也可独立。
- **「城邦 → 傀儡城」反向路径（召回代理人，决策 30 定案 ②）**——引擎件齐全，三步：① `City.moveToCiv(宗主国)`（`City.kt:528` → `CityConquestFunctions.moveToCiv()`，`:304`）把城和平转回；② `city.isPuppet = true` 走原版傀儡机制；③ 城邦此时 `cities.isEmpty()`，调 `Civilization.destroy()`（`Civilization.kt:1168`）——该函数的文档注释明写「expects `cities.isEmpty()`，剩余单位销毁、外交关系清理」，正是所需。

  **三处必须处理，否则召回会出脏状态**

  1. **只能调 `moveToCiv()`，绝不能走 `conquerCity()`。** 已核源码：征服副作用**全在 `conquerCity()`（`:112`）里**——掠夺金币、摧毁建筑、血量减半、**掉人口**（`-1 - pop/4`，`:133`）、以及**改写抵抗旗标**（`:136-143`）；而 `moveToCiv()`（`:304`）是干净的所有权转移，**完全不碰抵抗**。外交惩罚则在 `puppetCity()` 调的 `diplomaticRepercussionsForConqueringCity()` 里。所以「和平交接」不需要额外抑制任何东西，**只要别走错函数**。
  2. **城邦在代理人治下若打下了别的城**（决策 29 允许它宣战）——**定案（2026-09-11）：一并转回宗主国，全部按傀儡处理。** 逐座 `moveToCiv()` + `isPuppet = true`，转完 `cities.isEmpty()` 成立，`destroy()` 的前置条件即满足。语义上与召回原城一致，不必为「代理人自己打下来的城」另立一类。

     **代理战争的代价：吃好战惩罚，但保留无损耗返还（定案 2026-09-11）。**

     - **好战惩罚记在宗主国头上。** 依据是宗主国本来就能插手代理人的行为（宣战、议和都是我们下的令），既然战争是我们发动的，外交后果就该我们担。引擎侧即 `DiplomaticModifiers.WarMongerer`（`DiplomacyManager.kt:107`，`DeclareWar.kt:218` 按 `-5f` 施加，`DiplomacyTurnManager.kt:286` 会随时间衰减）——**需要显式把惩罚路由到宗主国**，因为发起方在引擎眼里是城邦而不是我们。
     - **无损耗返还保留**（不掉人口、不加抵抗、不掠金）。这正是代理人机制相对「直接吞并」的**优点所在**；若连这个也拿掉，玩家没有理由绕这一圈。
     - **惩罚在开战 / 攻城当时实时施加，不要拖到召回时结算。** 否则「代打之后永不召回」就能一直不付账；而且其他文明本来就应该在战争发生的当下作出反应，而不是若干年后突然翻旧账。
  3. **`destroy()` 会向全场广播「The City-State of [X] has been destroyed!」**——对一次和平召回是误导性播报，需要换文案或走静默路径。

- **控制力衔接（定案 2026-09-11）**：**控制力不因易主而中断。** 它是城市自己的属性，全局连续累积——交给代理人期间照常 +2/回合（驻军 +3），召回时**原样带回来**，既不重置为 1、也不按外派回合衰减。**唯一的暂停条件仍然只有抵抗期。** 这不是新规则，而是[决策 27](#决策-27--控制力control城市第-8-个计数--补给奖惩)「范围（全局）：所有文明的每一座城市，含 AI 与城邦」的直接推论——代理人统辖的城邦也是 `Civilization`，它的城本来就在控制力的作用域内。

  **由此产生一个正面的玩法后果**：刚打下来的城控制力只有 1，交给代理人期间它照样成熟；等你召回时拿回的是一座高控制力的傀儡城。「外派养城」成为一条合理路径，而代价是占掉一名官员、并承担 `morale` 跌破阈值闹独立的风险——这正是决策 29「用代理人替代死傀儡」想要的效果。

  **两个实现陷阱**

  1. **控制力必须是 `City` 上的存量字段，不能实现成「`gameInfo.turns - city.turnAcquired`」这类推导值。** `moveToCiv()`（`:318`）每次转移都会把 `turnAcquired` 重置为当前回合，用推导式实现的话，交出与召回各会把控制力清零一次——正好违背本条定案。
  2. **抵抗期必须能跨移交存活。** 用户要求「除非仍然抵抗」，而 `moveToCiv()` 恰好不动抵抗旗标（`CityFlags.Resistance`，见 `City.kt:275` 的 `isInResistance()`），所以**只要走 `moveToCiv()` 就天然成立**。反之若把移交写成 `conquerCity()` / `liberateCity()`，`:141-142` 会**清掉抵抗旗标**——那就成了「打下城立刻甩给代理人即可跳过抵抗期」的白嫖漏洞。

**存档陷阱**：代理人挂在**宗主国** `Civilization` 的 `CabinetManager` 上，它统辖的城邦是**另一个** `Civilization`。两者只能用 civ name / `HexCoord` 弱引用互指，在 `setTransients()` 里解开——双向强引用会让存档序列化成环。这与 `Spy` 用 `location: HexCoord?` + `@Transient city` 是同一手法。

#### 决策 30 — 官员统一模型：阁臣与代理人是同一种人的两种任命

**定案（2026-09-11）**：内阁不是「五个席位」，而是「**一个官员池 + 若干任命位**」。池里每个人是同一个 `models/Official.kt`；`assignment` 决定他此刻在哪：

| `assignment` | 位置 | 数量 | 作用 |
|--------------|------|------|------|
| `Idle` | 在野候选 | 不限 | 备选，不产生任何效果 |
| `Cabinet(post)` | 内阁五席之一 | **恰好 5**（决策 28 的五职位） | 四维计入 `S[k]`，天赋参与全内阁触发判定 |
| `Agent(cityState)` | 外派统辖一座傀儡城邦 | 0..N（每座城邦一人） | 走决策 29 的忠心 / 任务 / 上贡 |

**为什么这样更好**

1. **它把静态菜单变成了真机制。** 两套独立系统时，内阁是「挑五个人然后再也不管」的一次性配置屏。合并后，一个高 `war` 的人既是最好的军事统帅，也是最适合镇守边境城邦的代理人——**每打下一座城都要付一次机会成本**，而且付的是内阁那边立刻能看见的代价（他一走，`S.war` 掉，靠 war 阈值触发的天赋可能当场熄火）。这是决策 28 那套「天赋按全内阁求和判定」的设计本来就在等的东西。
2. **数据本来就是重合的。** 决策 29 要的「随机属性 + 军事/经济/科研/外交侧重 + 忠诚度」，逐项都能在决策 28 的 `attrs` 四维里找到对应。原先拆成两个模型是把同一份数据抄了两遍。
3. **内容量减半。** 按 `Nation` 走规则集的候选池（含 `bio` / `zi` / `talent` 文本）是 R2+R3 里最大的一笔内容成本。一个池子而不是两个，直接砍一半——这在模组兼容要求下（每个 `Nation` 都要有池子 + 兜底）尤其值钱。
4. **工程面收敛。** 一个模型、一条序列化路径、一个名字池兜底、一套存档往返测试。

**唯一真正的裂缝：`loyalty` 有两个含义**，已在决策 28 的数据模型表里拆成 `attrs.loy`（静态天资，进 `S.loy`）与 `morale`（外派后的运行时忠心）。**这条不能省**，合并字段会造出「代理人在边疆闹独立，顺带把首都的内阁团结拉垮」这种没人设计过的耦合。

**已拍板（2026-09-11）**

1. **外派中的官员，`talent` 不算数。** 离开内阁就完全不贡献天赋——`attrs` 退出 `S[k]`，`talent` 也一并失效。机会成本最锋利，实现也最简单：结算时只遍历 `assignment is Cabinet` 的那五个人，不需要任何「远程生效」的特例。「天赋改为作用于他统辖的城邦」留作后续可选增强，**v1 不做**。
2. **召回代理人 = 城邦解散，城市回归为我们的傀儡城**——走**原版傀儡机制**（`City.isPuppet`），额外叠加控制力（[决策 27](#决策-27--控制力control城市第-8-个计数--补给奖惩)）。阁臣被罢免则回 `Idle` 池。于是「傀儡城 ⇄ 代理人城邦」成为一对**可逆变换**，代价就是要不要占掉一名官员。

3. **官员池上限 = 20 人**，**开局 0 人**，v1 **不做退场**（不因年老或事件离场）。人从哪来、何时开始见[决策 31](#决策-31--官员招募政策触发遴选)。「人才更替」留到机制跑通之后再谈——否则 R2 会从一个 UI 屏膨胀成一套人才经营系统。

#### 决策 31 — 官员招募：政策触发遴选

**定案（2026-09-11）**：官员不是开局发满，而是**随政策推进逐步入朝**。每次获得遴选机会 → 弹一次候选人选择 → 选中者进入官员池（`assignment = Idle`）。

**引擎挂点（已核对）**：`logic/civilization/managers/PolicyManager.kt` 的 `adopt(policy, branchCompletion)`（`:241`）是政策落地的唯一入口，且它在 `:268-276` **本来就在派发触发式 unique**，还带一个通用的 `UniqueType.TriggerUponAdoptingPolicyOrBelief`。所以不需要新造事件系统，两条路都通：

| 路线 | 做法 | 评价 |
|------|------|------|
| (a) 硬编码 | `adopt()` 里加一行「+1 遴选机会」 | 最省事，但**模组必崩**：政策数量在不同规则集里差一个数量级 |
| (b) 新 `UniqueType` | 定义「[获得一次官员遴选]」，由规则集挂到具体政策上 | 接进现成的触发框架，模组可自由增删，**推荐** |

**节奏定案（2026-09-11）：挂政策分支的「开启」与「完成」，两者都给一次遴选**，而不是每一条政策。

理由是引擎已经替我们区分好了：`adopt()` 的签名里就有 `branchCompletion: Boolean`，分支开启也是一次单独的 `adopt`。原版约 60 条政策，若每条都给，对一个 12 人的池子是数量级超发，遴选会从「事件」退化成「顺手点掉的弹窗」；改按分支计，原版 10 个分支 × (开启 + 完成) = **一局至多 20 次遴选**，且每次都落在玩家本来就在做重大抉择的时刻。

**与 20 人上限正好对齐（已核对源码）**：`Civ V - Vanilla` 与 `Civ V - Gods & Kings` 的 `Policies.json` **都是 10 个分支**（按「X Complete」计），× (开启 + 完成) = **上限恰好 20 次**，与[决策 30](#决策-30--官员统一模型阁臣与代理人是同一种人的两种任命) 定案 3 的 20 人池**一一对应**。而实战中不可能开完并完成全部 10 个分支，所以**原版对局里池子根本填不满**——「满员怎么办」在原版是不会发生的分支。

**因此满员规则从简：作废 + 提示「朝中已无空位」，不做替换 UI。** 只有分支数 > 10 的模组才会撞到，不值得为它做一套「纳入并替换」的交互。

**开启门槛：科技「文官制度」（`Civil Service`）**

- **开局不开内阁，初始 0 人。** 研出 `Civil Service` 后，内阁入口出现、遴选开始生效。
- **已核对**：`Civil Service` 在 `Civ V - Vanilla` 与 `Civ V - Gods & Kings` 的 `Techs.json` 中均存在，属**中世纪时代**（G&K 前置为 `Currency` + `Horseback Riding` + `Drama and Poetry`），官方简中译名正是「**文官制度**」——不需要新增科技，也不需要新译名。

- ⚠ **时序冲突必须处理。** 政策分支从**远古时代**就能开（Tradition / Liberty / Honor 都是 Ancient），而 `Civil Service` 在**中世纪**。等你研出它时，通常已经开了三四个分支、完成一两个——也就是**已经有三到六次遴选机会在内阁存在之前就触发过了**。两种处理：

  | 方案 | 行为 | 评价 |
  |------|------|------|
  | (a) **累积补发** | 未解锁期间的机会记成计数，解锁时可用 | **推荐**。不惩罚正常科技路线；叙事上也顺——「文官制度确立，此前积累的人望一次性转为朝班」 |
  | (b) 作废 | 解锁前的机会直接丢弃 | 会把 `Civil Service` 变成近乎强制的抢科技目标，严重扭曲科技节奏；文化开局的玩家损失最大 |

  **采用 (a)，且不要在解锁瞬间连弹五个遴选窗。** 照引擎现成的 `PolicyManager.freePolicies` + `shouldShowPolicyPicker()`（`PolicyManager.kt:33`、`:44`）那套做法：**存成一个计数器**，由玩家自己在内阁屏里逐次消耗，回合结束提醒里挂一条「有待遴选」。

- **模组兜底（决策 24 / I-8）**：总转换模组可能没有 `Civil Service`。**门槛应做成规则集可声明的 unique**（与决策 31 路线 (b) 同一手法），回退顺序为：规则集声明的解锁项 → `Civil Service` 科技 → **都没有则开局即开**。**绝不能回退成「永不开启」**，否则内阁在模组下会静默消失。

**在野席位只有 6 个，人可能更多。** 20 人上限 − 5 名阁臣 = 最多 15 人在野，而参考图两侧站班只画了 6 个位置（`of0`–`of5`）。**站班那一排做翻页或「另有 N 人候旨」的角标即可**，不必为此改构图——朝堂场景本来就不适合塞十几个人。

**「幕后官员」的 UI 位置——参考图已经画好了。** `内阁系统.html` 的朝堂场景里除五卿之外，还画了 **6 个两侧站班的官员**（`OFFI` 的 `of0`–`of5`，`OFF_X = ±560` × `OFF_Z = [1250, 950, 650]`，即左右各三名，按纵深排开）。它们目前**纯装饰，没有任何点击处理**。在野池直接落在这六个位置上——语义、构图、交互三者天然对上，不必另设计一个「后备名单」界面。池子超过 6 人时这一排翻页或只显示前几名。

**详情页的三个动作**（承参考图弹窗底部三键，语义按决策 30 重新定义）：

| 动作 | 语义 | `assignment` 变化 |
|------|------|-------------------|
| 罢免 | 从当前任命上撤下 | `Cabinet(post)` / `Agent(cs)` → `Idle` |
| 提拔 | 补进内阁某一席（该席原任者被顶回在野） | `Idle` → `Cabinet(post)` |
| 选为代理人 | 外派统辖一座傀儡城邦（决策 29） | `Idle` → `Agent(cityState)` |

#### 决策 32 — 可选启用：开局开关，不是模组

**结论先行：内阁 / 代理人做不成 Unciv 模组，但能做成开局开关；后者才是这个引擎里「可选 DLC」的正确形态。**

**为什么不能是模组**：Unciv 的模组只能是 **JSON + 图片**，**不能携带 Kotlin 代码**（已确认本 fork 内无任何脚本后端）。内阁是几千行 Kotlin——屏幕、管理器、回合结算、存档序列化——没有任何途径作为模组分发。这是引擎硬限制，不是工作量问题。

**能做成什么**：`GameParameters` 里的开局开关，**引擎里就有现成模板**——`espionageEnabled`（`GameParameters.kt:32`）正是「整个子系统随勾选开关」的既有做法，而 **RC 自己已经用过这个模式**（`civAgentFillSeats`，`:54`）。照抄即可：新增字段、在 `clone()` 里带上（`:78-104` 那一段）、在 `GameOptionsTable` 加勾选框。

**必须是两个开关，不是一个**

| 开关 | 管什么 | 为什么要分开 |
|------|--------|--------------|
| `cabinetEnabled` | 内阁 + 代理人（决策 28 / 29 / 30 / 31） | **叠加式功能**：不开就当没有，对原版平衡零影响 |
| `controlEnabled` | 控制力（决策 27） | **全局平衡改动**：改 `getUnitSupplyFromCities()`，对所有文明所有规则集生效，等于把每个模组的兵力平衡重做一遍 |

两者「敢不敢开」的性质完全不同，绑成一个会逼玩家二选一。

**时机警告——这个开关必须现在就建，不能等做完再加。** 每个入口（顶栏按钮、`TurnManager` 结算、`GameInfo` 序列化、CivAgent 观察切片）都要从第一天带上这道门。事后补 gate 的代价是**重写**而不是加几行 `if`，尤其序列化：功能做完再想「关掉时不要写这些字段」，存档格式就已经定死了。

**内容仍然向模组开放（这才是「DLC 感」的正确落法）**：代码常驻 fork + 开局开关控制启用 + **官员内容走规则集 JSON**（候选池按 `Nation`，同 `spyNames` 的位置与手法，见决策 28 的模组兼容红线）。模组作者加不了代码，但能**整套替换官员内容**——宋朝模组配宋朝名臣，科幻模组配舰队指挥官。决策 31 的路线 (b) 让他们连「哪些政策给遴选」都能改。

**开关关闭时的行为**：顶栏按钮不出现；`CabinetManager` 不结算也不产生 unique；切片不带内阁字段（否则大脑会对着一个不存在的系统编话）；存档里该段留空但**字段仍在**（避免存档格式分叉）。

---

## 5. 风险

| 风险 | 状态 |
|------|------|
| 外交叠加导致席位行为自相矛盾 | **已消解**（Gap-1 方案 A 落地，2026-09-11） |
| 契约四处漂移（广告 ≠ 可执行） | **活跃**，见 Gap-3；已有一次线上事故 |
| 切片零测试，迷雾裁切无回归保护 | **活跃**，见 Gap-8 |
| 本地 fork 落后 upstream 285 commit | 观察中；不主动 rebase（决策 2），但安全修复需人工挑 |
| 网关只靠 127.0.0.1 绑定，无应用层鉴权 | 可接受（单机），联机场景需复审 |
| 密钥进存档 | **已消解**（I-1），勿回退 |
| UncivServer 版本错配 | 已消解（同源自构建 4.21.4-patch1） |
| 依赖漂移（`instructor` 缺失只在真调 LLM 时炸） | 已加固（`/healthz` 探测客户端可构造性）；**勿直接 `pip install -r requirements.txt`**，它钉死 `Flask==2.0.3` 会降级环境 |

---

## 6. 模块边界

| ID | 模块 | 进程 | 职责 | 明确不做 |
|----|------|------|------|----------|
| **H** | Host | 脚本 + `AgentBrainLauncher` | 勾选/开局/Options 拉起大脑；可选起游戏 | 不执行 CLI；不持有密钥全文 |
| **S** | SaveWarehouse | `UncivServer.jar` | `PUT/GET /files/{uuid}` | 无棋盘、无控制台、无 LLM |
| **G1** | `AgentGateway` | 游戏 | localhost HTTP；席位鉴权；控制台两档；`/v1/content` `/v1/ruleset` | 不解析自然语言 |
| **G2** | `ObservationSlicer` | 游戏 | 按席位裁雾的切片（决策 22） | 不给全图、不开上帝视野 |
| **G3** | `AgentWhitelist` + `IntentExecutor` 审查 | 游戏 | schema → 白名单 → 席位 → 引擎合法性 → 贸易槽 | 不写存档 JSON；不跑 GM |
| **G4** | `AgentTurnHook` | 游戏 | 拦 `automateCivMoves`；确认；执行；回执；超时回原生 | 不并行多国；不在玩家回合落子 |
| **G5** | `LeaderDialoguePopup` + `AgentTab` | 游戏 | 聊天 UI、情报交换、上传、确认清单、密钥与采样配置 | 不发明第三套聊天协议 |
| **G6** | NativeAI（原版） | 游戏 | 单位 / 工人 / 城市 / 科技 / 政策 | Agent 不点格子 |
| **B1** | 席位运行时 | 大脑 | `(gameId, civId)` 独立上下文与锁 | 不把多国塞进一次补全 |
| **B2** | `llm_router` | 大脑 | 六厂商路由（deepseek/openai/openai_compatible/gemini/anthropic/ollama）、采样参数、流式 | 密钥不进 `GameInfo` |
| **B3** | 记忆与信任 | 大脑 | `deployment/data/{gameId}/` 下 jsonl + `trust.json` | 不上 SQLite/向量；不改引擎好感 |
| **B4** | 聊天门面 | 大脑 | `/message` `/events` `/messages` `/upload` | `/message` 不执行落子 |

**Kotlin 新代码放** `Unciv-Source/core/src/com/unciv/logic/civagent/`；UI 放 `ui/screens/worldscreen/` 与 `ui/popups/options/`。重打 Windows64 用 **jpackage**。

---

## 7. 明确不做

- 微调基座；训资源价值网络（引擎 `TradeEvaluation` 已给定价）
- Python 写回存档；JPype 挂 LibGDX
- SQLite / 向量库镜像棋盘或规则集（决策 23 / 26）
- Discord / 飞书 / `NEED_POST` / 自实现 `/files`
- 每文明一个模型进程；嵌 `agentapi server -- claude`
- `POST /message` 当场宣战
- 密钥进客户端或存档
- 改 RC 好感语义；主动 rebase 到上游 4.21.16+
- vendoring HDS Interlude（AGPL）
- **跨回合 headless 仿真 / W7 伏羲论文仿真**——2026-09-10 判定：没有真实 LLM 回复的仿真测不到 agent 行为，mock 出来的结论没有价值。W7 仅在需要论文数据时才重启。

---

## 附：训练场评估体系（2026-09-12 定纲）

- **国家强度分三档**：T0 原生基线（无 LLM，测国家本体）→ T1 LLM×国家（测大脑+国家合力）→ T2 方法论对打（测方法论孰优）。顺序不可跳：先 T0 基准，再调方法论。
- **回合三档**：50/100/150（stats jsonl 一局覆盖三检查点）；200 后置。**主榜用的势只取后程 100/150**（t50 仍采样，不进名次，避免中期肥、终局灭被抬起来）。
- **难度梯度**：Emperor 起步（AI 进攻性缩短对局），Immortal 可选；难度对双 AI 对称，不污染相对比较。
- **固定出生点**：被测国左位、参照国（罗马）右位、种子恒定——对照实验三固定。
- 国家池：无悔华夏 cathay mod（盛唐/蒙古帝国/秦/晚唐）+ 原版主力（巴比伦/埃塞俄比亚/瑞典/罗马/希腊/埃及），至少 6 国参与轮换。

### 附：三轴测试口径（2026-09-14，pytest 锁住）

主榜、势、征服**分列**，禁止揉成一张总分。`tests/test_arena.py` 守住下列不变式：

| 轴 | 定义 | 测试要点 |
|---|---|---|
| **主榜 = 均名次**（越小越好） | 局内：存活者按后程相对势排，灭国垫底；跨局算术平均。排序键：`meanPlacement` → 存活率 → 势。 | 灭国排在存活者之后；一场雪崩抬高势不能压过更稳的均名次；`rankingAxis=meanPlacement` |
| **势（诊断）** | 后程相对势（检查点 100/150，1.0=局内中位）。占城已含在城/人/军里，**不再把征服加成叠进 `score`/`powerRel`**。 | 占 5 城时 `score==powerRel` 且 `bonus==0`；t50 高峰不进检查点 |
| **征服（诊断）** | 占城 / 灭国 / 存活。规则仍在 `scoring.conquest_wonders`（每城 3 奇观、丢>10 扣 3、灭国 1 奇观），只作并列列。 | 诊断字段仍记 `conquestWonders`；不进入主榜分数 |

默认**不加**旧 wrap 先验（`--prior-weight` 默认 0）、**不加** `duel150`（仅 `--include-duel` 走旧公式）。`fit_weights` 标签改为「末回合存活且城数最多」，特征用 `FIT_FACTORS`（去掉 `engineScore`，避免自证）。

### 附：三轴口径的**测试强度**与已知空洞（2026-09-15，变异测试实测）

对 `tests/test_arena.py` 做了 13 次定向变异（改坏实现看用例会不会红）。首轮 10 个变异 **8 被杀**首轮 10 个变异 **8 被杀**；补完 P0 后 M7/M8/M11 也全部被新用例杀掉。

已锁死：`powerRel` 不叠征服、主榜按均名次（非势）、检查点只用 100/150、拟合特征不含 engineScore、灭国垫底、丢城阈值 10、duel 默认不进 combined、melee 默认不吃 save/batch、**均名次平手先比存活率**、**combine 排序以均名次为主键**、**checkpoint 标签与回合对齐**。

本轮修掉的真缺陷：`report._game_scores` 的 `zip(checkpoints, bases)` 在文明跳过某检查点时把后值贴到前键（实测 t150 的值被标成 `100`）；现改成 `(turn, value)` 采样对。

**仍未堵的空洞（已知、未动）**：

1. **地图分集**：PLAN 写「不同地图不得混表」，但 `report.py` 无 map 概念，`melee_scores(where=None)` 默认把 rotation 里三种条件（旧图-cathay / 测试图-cathay / 测试图+SongDynasty）合算；需显式 `--maps` / `--mods-filter`（已在生成文档表头警告）。
2. **短局回退**：`last_turn < 100` 时 `_power_turns` 退回 `[last_turn]`，即 t50 仍会进名次（与「t50 不进名次」矛盾）。需拍板：要么写进口径，要么禁止。
3. **无属性测试**：placement 无置换不变性、powerRel 无全局缩放不变性、`conquest_wonders` 无单调性。
4. **无集成/产物回归**：除新增的 `render_markdown` 单测外，`index.jsonl → report → nation_strength → 榜单` 全链仍只有手跑。

### 附：国家榜产物改为可复现（2026-09-15）

`ranking-total.md` 旧版本是**手写维护**、且已漂移到旧公式（`mean(混战,mk)+0.1×min(duel150,cap)`，MongolEmpire 2.8372+0.3=3.137），与锁定的默认 `include_duel=False` 相矛盾。处理：

- 旧文件归档为 `arena/archive/ranking-total-20260914-duelblend.md`。
- `nation_strength.py` 新增 `render_markdown()` + `--md <路径>`，与 `--write` 同源同次生成，文件写「请勿手改」。
- 新榜命令：`python arena/nation_strength.py --write --md arena/ranking-total.md --maps 测试arena.json`（主榜=均名次，duel150 只作末列）。

### 附：训练场口径硬化（2026-09-15，用户四问落地）

| 项 | 定论 |
|---|---|
| **arena 是包** | 新增 `arena/__init__.py`；包内一律 `from . import x`，直接用脚本跑时靠各模块 `except ImportError` 回退。`python -m arena.x` 与 `python arena/x.py` **两种都通**，调用方不再写 `sys.path.insert` |
| **层级** | `paths`（配置）→ `procs`（进程）→ `gamelog`（日志/索引）→ `scoring`（口径）→ `report`/`nation_strength`（榜）→ **`harness`（单局执行）** → `run_rotation`（只排赛）/ `run_batch` → `fit_weights`（回归） |
| **跑满口径（宽松）** | `gamelog.is_complete()`：日志给出结果（`VICTORY` 或 `no victory (turn limit)`）才算跑完；被 timeout 切断/无 outcome **不算**；**提前胜利算跑完**（不惩罚早战型）。默认接入 report / nation_strength / fit_weights，`--allow-partial` 回旧行为 |
| **标准图 + 对照条件** | **`测试arena.json`**（Large 66×43、`mirroring=Left-right`、2791 格）为唯一基准图；地图+模组是**两个独立条件**，要分开出榜：`report --maps/--mods-filter`、`nation_strength --maps/--mods-filter`、`fit_weights --maps`。**实测（2026-09-15）**：地图 36 种资源 = 32 原版 + 4 cathay（Bronze/Jade/Tea/醴），**Song Dynasty 只定义 1 种资源且地图一个没用** → 两批地形/资源相同；真正差异在**文明**：cathay 给 Late Tang/MongolEmpire/Qindynasty/Sheng Tang，**Song Dynasty 给 Northern Song/Southern Song**。所以 cathay-only 批（50 局，16 国名单）与 cathay+Song 批（21 局，39 国名单）是不同对照条件，不得默认混算。机制：`ArenaLauncher` 只认 CLI `--mods`，`map_fix --mods` 会把地图 `mapParameters.mods` 覆盖成 CLI 值；原稿备份 `arena/archive/maps/测试arena-pristine-20260915.json` |
| **旧榜先验** | 旧榜分数 **×0.1** 作为回归设计矩阵最后一列（`scoring.PRIOR_FACTOR`，不 z-score）；**链路由 `arena/prior-current.json` 默认启用**（之前该文件不存在 → 默认等于关闭，已修）；**只作诊断，不进 power()**。实测 60 局：系数 −0.2185、命中率 +0.0pp → **无增量信息** |

**退出机制硬要求（`harness.py`）**：完成标记→等 2s→主动杀（JVM 打完不退出）；超时 `max(120, max_turns×12)` 强杀；`finally` 兼底保证异常/Ctrl-C 也收尸；单局异常只跳本局、不炸整批；批末 `kill_stale()`。

### 附：waitress 与训练场在整体架构中的位置（2026-09-12）

- **waitress**：大脑层的服务容器。架构上不新增组件——大脑节点由「agent_api（Flask 应用逻辑）」+「waitress（HTTP 容器：监听/线程池/连接管理）」构成，图上合并标注为「agent_api（waitress 容器）」。启动链 waitress → gevent → werkzeug（兜底，仅调试）。
- **训练场（arena/）**：独立的离线评测环，与在线对局栈并列，与大脑代码**无 import 关系**。分层见上表：`harness.py`（地图注入 → ArenaLauncher 拉起 → 收尸 → 写索引）只管**单局**，`run_rotation.py` 只管**排赛**（拉丁方 + 墙钟预算），`spawn_classify / report / nation_strength / fit_weights` 负责评分分析。与大脑的唯一耦合点是 T1/T2 时的 `/v1/decision`；T0 纯离线不需要大脑。闭环：跑局 → stats/存档/轨迹 → 离线分析 → 调 prompt/阈值/权重 → 写回大脑层。
- **地图对称硬门槛（2026-09-12）**：评测图必须是**屏幕竖中线**左右对称（`(x,y)→(-x-y,y)`，`worldWrap=false`），生成器内置校验失败即拒写。**不同地图的对局不得混进同一排名表** —— 2026-09-15 起由 `--maps` 强制执行（`report` / `nation_strength` / `fit_weights` 均支持）。旧 wrap 圆柱镜像图已作废。**当前基准图 = `测试arena.json`。**
