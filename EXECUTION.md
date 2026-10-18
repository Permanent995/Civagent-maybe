# CivAgent 执行计划：任务板 + 归档

> 动手清单。设计与约束在 [`PLAN.md`](PLAN.md)。
> 重写日期：**2026-09-11**（上一版存档于 `EXECUTION.backup-20260911-130519.md`）

## 0. 怎么用这份文档

- **领任务** → [§3 任务板](#3-任务板)，每项都有「改哪里 / 验收 / 依赖」。
- **改代码前** → 读 `PLAN.md` §2 八条不变式与 §3 偏差表。
- **改完** → 走 [§4 交付纪律](#4-交付纪律)，不跳步。
- **想知道以前发生过什么** → [§5 归档](#5-归档)。

**W0–W7 波次模型已退役。** 它在 2026-09-06 到 09-08 完成了历史使命（W1–W5 全部落地，W7 判定不做），但过去几天的真实工作——模组兼容、内容桥、十批正确性修复——都在波次表之外。现在按**工作流**分组：`P`（修偏差）/ `R`（RC 自研机制）/ `M`（模组与内容）/ `X`（体验与质量）/ `D`（数据）/ `O`（可选）。旧波次编号只在归档里出现。

---

## 1. 现状快照（2026-09-11）

| 项 | 值 |
|----|-----|
| 版本 | 两仓均已发 **v0.1.0**：大脑仓 `release/release` @ `220a87e`（tag `v0.1.0`）；游戏仓 `master` @ `a44305038` |
| 游戏仓分支 | `Unciv-Source` @ `master`，工作区 clean，落后 upstream 285 commit（不主动 rebase，见决策 2） |
| 游戏侧代码 | `core/src/com/unciv/logic/civagent/` 15 模块；UI 4 弹窗；devconsole 重构 9 文件 |
| 游戏侧测试 | **48** 个 `@Test`（civagent 包）；`:tests:test` 全量绿 |
| 大脑 | `deployment/ai/agent_api.py`（925 行，Flask :2335，11 路由） |
| 大脑测试 | **90** 个 pytest 用例（另 7 个遗留文件默认跳过） |
| 契约 | `docs/protocol/`：ObservationSlice **v2**、Intent **v1** |
| 分发 jar | 55,997,986 B @ 2026-09-11 12:45，已同步进 `Unciv-SongDynasty/app/` |
| 分发包 | `Desktop\Unicv-RC-1.0-win64-fixed.zip` 98.1 MB / 360 文件 |
| 运行时数据 | `deployment/data/{gameId}/`：`{civID}.jsonl`、`chat_{pair}.jsonl`、`claims.jsonl`、`trust.json`；`_uploads/`、`host/`、`export/` |

**活路径**：`start-host.ps1` → `python -m deployment.ai.agent_api`。仓库里的 `ai_server.py` / `civsim/` / `chatbot/` / Redis / JPype **全部不在活路径上**（PLAN Gap-7）。

---

## 2. 常用命令

```powershell
# 起大脑（探测 2335，未起则拉起）
.\scripts\start-host.ps1

# 大脑测试
python -m pytest tests/ -q

# 游戏测试（JDK 17；Gradle 8.11.1 不能跑在 JDK 25 上）
cd C:\Users\<user>\Desktop\Unciv-Source
.\gradlew :tests:test

# 只跑 civagent
.\gradlew :tests:test --tests "com.unciv.logic.civagent.*"

# 打包并换进客户端（先完全退出游戏）
.\gradlew :desktop:dist
# desktop/build/libs/Unciv.jar → Unciv-SongDynasty/app/Unciv.jar（先备份 .bak-<时间戳>）

# 导出训练样本
python scripts/export_chat_dataset.py
```

**注意**

- **不要** `pip install -r requirements.txt`——它钉死 `Flask==2.0.3` / `Werkzeug==2.0.3`，会把当前环境降级。按需单装。
- 改了 Python **必须重启大脑**（对话「+」菜单 → Restart brain，或 `POST /v1/shutdown`）。
- 改了 Kotlin 必须重新 `:desktop:dist` 并**完全退出游戏**再开。

---

## 3. 任务板

| ID | 任务 | 优先 | 仓 | 依赖 |
|----|------|------|----|------|
| **P1** | 外交「覆盖 vs 叠加」定语义并实现 | **P0** ✅ | 游戏 | 方案 A 已落地（2026-09-11） |
| **P2** | 契约四处对齐（删 `common_enemy`、补 `intel` 字段） | **P0** ✅ | 大脑 | 不变式测试守住（2026-09-11） |
| **P3** | `ObservationSlicer` 补测试（含迷雾裁切） | **P0** ✅ | 游戏 | 顺带修叙述器 tr 顺序 + 槽 key 两个真 bug |
| **P4** | 性格锁端到端接通 | P1 ✅ | 两仓 | 切片渲染引擎性格，大脑优先切片值 |
| **P5** | 贸易槽持久化 / 与引擎 `tradeRequests` 对账 | P1 ✅ | 游戏 | reconcile 以引擎为事实源（2026-09-11） |
| **P6** | 删除 `ChatReplyParser` 死代码分支 | P2 ✅ | 游戏 | 2026-09-11 |
| **R1** | 控制力机制（决策 27） | **主线** | 游戏 | — |
| **R2** | 内阁系统（决策 28 / 30 / 31 / 32） | 主线 | 游戏 | 设计已全部拍板；第 1–7 步可开工，**第 8 步等美术** |
| **R3** | 代理人 / 傀儡城邦（决策 29） | 主线 | 游戏 | R2（内阁选拔） |
| **M1** | M-mod-1：切片规则集标签补完 + D4 / D6 | P2 | 两仓 | — |
| **M2** | M-content-2：LLM 主动按需查询闭环 | P2 | 两仓 | — |
| **X1** | 汉化补完（配置页 / 调试 UI） | P1 | 游戏 | — |
| **X2** | 多模态视觉：分类管线（决策 16） | P2 | 大脑 | — |
| **X3** | 提示词预设薄层 | P3 | 两仓 | — |
| **D1** | 数据飞轮：采集完整化 + 评测集 | P2 | 大脑 | — |
| **O1** | W-Lobby：短码开房 → 大厅 | 可选 | 游戏+服 | — |
| **O2** | M0 收尾：双客户端 GUI 联机冒烟 | 可选 | 发行物 | — |
| **O3** | 旧伏羲栈隔离或删除 | 可选 | 大脑 | — |
| **O4** | 训练场按地图分集 + 竖中线对称硬门槛 | P1 ✅ | arena | 2026-09-12：生成器重写，旧 wrap 镜像作废 |

### P0 — 修偏差

#### P1 · 外交「覆盖 vs 叠加」

**问题**：`NextTurnAutomation.kt:51-68` 在 `applyIntents` 之后照跑全套 `DiplomacyAutomation` + `TradeAutomation.exchangeLuxuries`。详见 PLAN Gap-1。

**先决**：这是产品决策，不是纯技术修复。三个候选——

| 方案 | 行为 | 代价 |
|------|------|------|
| A（推荐） | 本批**成功落地的意图类别**对应的原生调用本回合跳过；未覆盖的类别仍走原生 | 要维护「意图类别 → 原生调用」映射表 |
| B | CivAgent 席位**完全接管**外交，原生外交整段跳过 | LLM 沉默/超时的回合该席位完全不做外交，行为退化 |
| C | 维持现状，改文案承认「叠加」 | 与决策 8.2、I-3 冲突，玩家看到的确认清单继续对不上实际 |

**改哪里**：`NextTurnAutomation.automateCivMoves`；`AgentTurnHook.applyIntents` 需返回本批覆盖了哪些类别。

**验收**：LLM 议和成功的回合，该席位不再由原生 `offerPeaceTreaty` / `declareWar` 产生第二次外交动作；`exchangeLuxuries` 不再破坏 `AgentTradeSlots` 的单槽约束；新增回归测试断言「Agent 宣战后原生 `declareWar` 未被调用」。

#### P2 · 契约对齐

**改哪里**：`docs/protocol/intent.schema.json` 删 `common_enemy`；`IntelPayload` 的 `kind` / `aboutCiv` 补进 schema；`docs/protocol/README.md` 的白名单列表同步。

**验收**：一个校验脚本或测试，断言 `INTENT_TYPES`（Python）== schema 枚举 == `IntentExecutor` 的 `when` 分支集合。**这条不变式（I-2）应该由测试守住，不能靠人记。**

#### P3 · 切片测试

**改哪里**：新建 `tests/src/com/unciv/logic/civagent/ObservationSlicerTest.kt`。

**验收**：至少覆盖 —— ① `hideOtherCivilizationStats=true` 时 `knownCivs` 只剩关系/距离类字段；② 未接触文明不出现；③ `borderThreats` 只计 `isVisible` 的敌军且不含坐标；④ `nearestHostileCity` 只认 `isExplored`；⑤ 存量/增速字段成对非空。顺带补 `AgentWhitelist`、`AgentTradeSlots`、`IntentNarrator` 的基础用例。

#### P4 · 性格锁

**问题**：切片 `personality` 字段永不赋值，大脑从 `config.yaml` 取，默认全是 `"pragmatic ruler"`。详见 PLAN Gap-2。

**改哪里**：`ObservationSlicer.slice()` 读 `civ.nation.personality` → `ruleset.personalities[...]` 的系数，渲染成短文本（如 `aggression 8, loyalty 3, diplomacy 5`）写入切片；`agent_api.seat_personality()` 改为**优先用切片带来的值**，config 仅作覆盖。

**验收**：两个不同 `personality` 的席位，`/v1/prompt` 能看到不同的性格行；`config.yaml` 未配置时不再退化为默认人格。

#### P5 · 贸易槽

**改哪里**：`AgentTradeSlots` 从进程内 `HashMap` 改为以引擎 `civ.tradeRequests` 为准做对账（引擎侧本来就进存档），或随 `GameInfo` 持久化。

**验收**：存档 → 退出 → 读档后，同一对文明不会重复投递报价。

#### P6 · 死代码

`ChatReplyParser.kt` 全仓仅测试调用（生产走 SSE 的 `intents[]`）。要么接进生产当**客户端侧兜底解析**，要么连同测试一起删。不要留两套会漂移的解析规则。

### R — RC 自研机制（主线）

规格见 `PLAN.md` §4.3。这三块是纯引擎逻辑，**不依赖 LLM**，与 CivAgent 正交，可独立推进。

#### R1 · 控制力

**改哪里**：`City` 新字段（默认值 + 进存档）；`CityFounder` 初始化（founded 50 / puppet 1）；回合处理累加（+2，驻军 +3，抵抗期跳过，上限 100）；`Civilization.getUnitSupplyFromCities()` 改逐城求和并叠加档位；城市界面右上角第 8 计数 + 城市统计界面「影响力」+ 图标资源。

**验收**：新城 50 且每回合 +2；驻军 +5；抵抗期不涨；傀儡从 1 起；补给档位 ≥80→+4 / [50,80)→+3 / [40,50)→+2 / <40→−2 且人口项不变；存档往返保值；城邦与 AI 同样生效；**城市易主后控制力不重置**（用 `moveToCiv` 转一次城来断言——这条同时守住 R3 的召回语义）。

**注意**：这是全局机制，会改变所有文明的单位补给上限——需要一次平衡性回归（原生 AI 会不会因此爆兵或缺兵）。**也正因为它是全局平衡改动而非叠加功能，它要有自己的 `gameParameters.controlEnabled`，不与内阁共用开关**（决策 32）。

#### R2 · 内阁

**骨架已定：整体仿照原版间谍系统**（决策 28 的七件套对照表）。数据走 `Spy` / `EspionageManager` 那套，**不走单位管线**。

**UI 已定案**（参考图 `内阁系统.html`）：「**二三排列**」朝堂场景，五职位 = 军事统帅 / 间谍首脑（后排）+ 外交大臣 / 财政管家 / 教化祭司（前排）。官员模型、国力五维公式、弹窗结构、移植代价、模组红线全部见 `PLAN.md` 决策 28 的「UI 与数值定案」，**照那份实现，公式勿自行改动**。

**结构已定**（决策 30）：**阁臣与代理人是同一种 `Official` 的两种 `assignment`**，全国一个官员池。R2 建的就是 R3 要用的那个池子——所以第 1 步的模型和序列化要一次做对。

**可选启用已定**（决策 32）：走 `gameParameters.cabinetEnabled` 开局开关，**不是模组**（Unciv 模组只能是 JSON + 图片，带不了 Kotlin）。**这道门是第 1 步，不是最后一步**——事后补 gate 要重写序列化。

**剩余阻塞**（都不挡第 1–7 步开工）

- **美术**：朝堂场景烘焙图 1 张 + 人物立绘 5 张（透明底）+ 幕后站班官员图。Scene2D 画不了 SVG，这是硬依赖——**只挡第 8 步**，前七步用竖排列表先跑通。
- **决策 28 三项已拍板**：① 内阁 `ctrl` = **当前国家的全局控制力**，与决策 27 的城市控制力是同一概念的两个层级，不改名、耦合从轻；② **五维是添头、天赋才是正主**——重点做对天赋触发与 `talent.nat` 效果，五维的直接加成从轻，落地走 `temporaryUniques`（选它就是因为不碰既有结算代码）；③ `faction` / `stance` / `relations` **纯展示**，`stance` 简化为「文臣 / 武将」二分，**v1 不与 `factionBalance` 联动**（后者保持原样独立运行）。
- **决策 30 / 31 已全部拍板**：① 外派官员的 `talent` **不算数**（`attrs` 与 `talent` 一并失效，结算只遍历在阁的五人）；② 召回代理人 = **城邦解散、城市回归为我们的傀儡城**（原版 `isPuppet` + 控制力）；③ **官员池上限 20 人，开局 0 人，v1 不做退场**；④ 遴选挂**政策分支的开启与完成**（两个规则集都是 10 分支 → 上限恰好 20 次，与池子一一对应；原版填不满，满员即作废+提示，不做替换 UI）；⑤ **内阁由科技「文官制度」`Civil Service` 解锁**（Vanilla 与 G&K 均有此科技，中世纪，官方简中译名即「文官制度」）。

**实现顺序**

1. **`gameParameters.cabinetEnabled` 开关**（决策 32）：照 `espionageEnabled`（`GameParameters.kt:32`）与 RC 自己的 `civAgentFillSeats`（`:54`）——加字段、进 `clone()`（`:78-104` 那段）、`GameOptionsTable` 加勾选框。**先建门再建房**。顺带给决策 27 单开一个 `controlEnabled`（理由见决策 32：那是全局平衡改动，不能和叠加式功能共用一个开关）。
2. `models/Official.kt` + `managers/CabinetManager.kt`（照 `Spy.kt` / `EspionageManager.kt` 的形状：`clone()` / `setTransients()` / `endTurn()`，进 `GameInfo` 序列化）。字段按 PLAN 的**官员数据模型**表；`faction` / `stance` / `relations` / `rank` **纯展示、只存不算**（`stance` 用「文臣 / 武将」二分即可）。**一个 `officials` 池**，`assignment` 分 `Idle` / `Cabinet(post)` / `Agent(cityState)`（决策 30）——R2 只用到前两种，但**字段和序列化现在就要留全**，否则 R3 得改存档格式。`attrs.loy` 与 `morale` **必须是两个字段**。
3. **内阁入口按钮**：**不要**复用 `espionageButton` 的生命周期——`TechPolicyDiplomacyButtons.kt:95/:120` 用 `isEspionageEnabled()` 把它整段包住，关了间谍按钮压根不构造，内阁会彻底不可达。改照同文件 RC 自己的 `factionBalanceButton`（`:107-111`、`:213-221`）无条件构造，只在旁观者 / 蛮族 / **`cabinetEnabled == false`** 时隐藏。
4. `CabinetScreen`：先用**竖排列表**跑通（不依赖美术），「间谍首脑」一格跳**原封不动的** `EspionageOverviewScreen`，并在 `isEspionageEnabled() == false` 时把该格**灰掉而非隐藏**。官员详情页带三个动作：罢免 / 提拔 / 选为代理人（决策 31 的动作表；「选为代理人」在 R3 落地前先灰掉）。
5. **招募**（决策 31）：候选池按 `Nation` 走规则集（同 `Nation.spyNames`，`Nation.kt:69`），**兜底必须先于内容**——没有池子就「职位名 + 序号」生成，不崩、也不落回中国人名。遴选触发挂 `PolicyManager.adopt()`（`:241`），**走新 `UniqueType` 而不是硬编码**（`:268-276` 已有现成的触发式 unique 派发框架）；**分支「开启」与「完成」各给一次**，`adopt()` 签名里的 `branchCompletion` 就能区分。
6. **开启门槛与机会累积**（决策 31）：内阁由 `Civil Service` 解锁；门槛做成**规则集可声明的 unique**，回退顺序 = 规则集声明项 → `Civil Service` 科技 → **都没有则开局即开**（绝不能回退成「永不开启」）。**解锁前触发的遴选机会必须累积**——政策分支从远古就能开，而 `Civil Service` 在中世纪，正常科技路线下解锁时已经欠了三到六次。存成计数器、由玩家在内阁屏逐次消耗，**别在解锁瞬间连弹五个窗**；照 `PolicyManager.freePolicies` + `shouldShowPolicyPicker()`（`PolicyManager.kt:33`、`:44`）那套。
7. 结算与落地：`S[k]` 属性综合 → **天赋触发判定** → 国力五维 + 内阁团结（公式见 PLAN）。**工期往天赋上压**——触发判定与 `talent.nat` 的实际效果是玩家真正感知的部分；五维的直接加成从轻，不必精调权重。落地走 `Civilization.temporaryUniques`：`gold` / `happy` / `tech` 写成原版 unique（`[+15]% [Gold]` 之类），`CabinetManager.endTurn()` 每回合刷成 `turnsLeft = 1`；`ctrl` 作为国家级全局控制力接决策 27，`fav` 接城邦影响力与外交修正。**每回合重刷要先清上一轮的，别与存档残留叠加。**
8. 二三排列朝堂屏：美术到位后替换第 4 步的竖排，命中区固定在 1200×720 设计空间等比缩放；**竖排作为窄屏兜底保留**。在野官员落在参考图**两侧站班的 6 个位置**（`OFFI` 的 `of0`–`of5`，原型里是纯装饰、无点击处理），**上限 20 人时在野可达 15 人，这一排必须翻页或挂「另有 N 人候旨」角标**。

**不做**

- 内阁五卿**不实现** `UnitTable.Presenter`。他们在地图上没有位置，只活在内阁屏；`SpyPresenter` 那套是给 R3 代理人用的。
- **不做成 Unciv 模组**——引擎不允许模组带代码（本 fork 内无脚本后端）。模组的着力点是**内容**：按 `Nation` 替换官员池、用决策 31 的 unique 改遴选节奏。

**验收**

- 内阁屏可开、可进间谍屏且间谍功能零回归；**关闭间谍的对局里内阁仍可进入**，只有「间谍首脑」一格灰掉；**无间谍时内阁仍可进入**。
- `CabinetManager` 存档往返保值，**含 `assignment` 与 `morale`**（R2 阶段虽然还没有 `Agent` 任命，也要断言这两个字段能原样往返，否则 R3 会撞上存档格式变更）。
- 换一个**模组规则集 / 无候选池的国家**开局：内阁能开、生成占位人物、不崩、不出现中国人名。
- 五维与天赋触发的计算结果与参考图原型逐项一致（同一组候选人 → 同一组数字）。
- **天赋是主要载荷**：换一名阁臣使某天赋跨过 `cond.v` 门槛时，其 `nat` 效果当回合生效；跌回门槛下当回合失效。
- **临时 unique 不累积**：连跑 20 回合后 `temporaryUniques` 里内阁产生的条目数恒定，不随回合数增长；读档后同样不翻倍。
- **`cabinetEnabled = false` 开局**：顶栏无内阁按钮、`CabinetManager` 不结算也不产生 unique、CivAgent 切片不含内阁字段、存档仍能正常往返（字段在但为空，**不得分叉存档格式**）。
- **招募链路**：分支开启 / 完成各触发一次遴选 → 选中者进入在野池并出现在内阁屏；**中途的普通政策不触发**。
- **科技门槛**：研出 `Civil Service` 前内阁入口不出现、官员数为 0；**解锁前触发的机会全部累积**，解锁后计数正确（例：解锁前开了 3 分支完成 1 个 → 解锁时应有 4 次待遴选）且不连弹窗口。
- **门槛兜底**：删掉 `Civil Service` 的自制规则集下内阁**开局即开**，绝不出现「永不开启」。
- **池子上限**：招到第 20 人后满员提示且机会作废，不崩、不越界。

#### R3 · 代理人

依赖 R2——**代理人不是新实体，是 R2 那个官员池里的人被外派**（决策 30），`Official` / `CabinetManager` / 名字池 / 序列化全部复用，R3 只加「任命为代理人之后会发生什么」。仍然**不是 `MapUnit`**（决策 29 三条理由）。

**召回已定**（决策 30 定案 ②）：召回代理人 = **城邦解散、城市回归为我们的傀儡城**，走原版 `isPuppet` + 控制力。于是「傀儡城 ⇄ 代理人城邦」是一对可逆变换，代价是占不占一名官员。

**控制力衔接已定**（决策 27 / 29）：**控制力不因易主而中断**——交给代理人期间照常增长，召回时原样带回，唯一暂停条件仍是抵抗期。去程回程都只走 `moveToCiv()`，交出与召回因此是**对称、可逆、无损耗**的。

**多余城市已定**：城邦在代理人治下自己打下的城，**召回时一并转回宗主国、全部按傀儡处理**（逐座 `moveToCiv()` + `isPuppet = true`，转完 `cities.isEmpty()` 成立，`destroy()` 前置条件即满足）。

**代理战争代价已定**：**宗主国吃好战惩罚**（`DiplomaticModifiers.WarMongerer`，`DiplomacyManager.kt:107` / `DeclareWar.kt:218`），因为宣战议和本来就是我们下的令；但**无损耗返还保留**（不掉人口、不加抵抗、不掠金）——那是代理人机制相对直接吞并的优点所在。**惩罚在开战 / 攻城当时实时施加**，不拖到召回结算，否则「代打之后永不召回」就能一直不付账。实现上要**显式把惩罚路由到宗主国**，引擎眼里的发起方是城邦而不是我们。

**独立阈值已定**：**全文明统一，不按国家区分**。阈值本身仍随国力 / 军力 / 文化浮动，但不存在按 `Nation` 配置的初始差异——因此不需要新增规则集字段，也不需要为它写模组兜底。

**设计待定项：无。** R3 的阻塞只剩 R2（官员池与 `CabinetManager`）。

**实现顺序**

1. 傀儡城 → 新建 AI 城邦文明并接管：`CityConquestFunctions.puppetCity()`（`:152`）后加分支，走 `liberateCity()`（`:203`）的 Resurrection 路径 + `diplomacy/CityStateFunctions.kt`
2. `assignment = Agent(cityState)` 的**任命与召回**；`morale` 以该官员 `attrs.loy` 为初值，三条升降条件（作战失利 / 断援 / 任务未完成）。两个要点：
   - **外派后其四维与天赋一并退出结算**（决策 30 定案 ①），内阁那边的天赋触发要当场重算——这是机会成本的落点，必须能在内阁屏上看见。
   - **召回走反向路径**：`City.moveToCiv(宗主国)`（`City.kt:528`）→ `isPuppet = true` → 城邦 `cities.isEmpty()` 后 `Civilization.destroy()`（`Civilization.kt:1168`，该函数自带单位销毁与外交清理）。**三处别踩**：① **绝不能走 `conquerCity()`**——掠金 / 毁建筑 / 血量减半 / 掉人口 / 改写抵抗旗标全在那个函数里（`:112`、`:133`、`:136-143`），`moveToCiv()`（`:304`）才是干净的转移且**完全不碰抵抗**；② 城邦若另打下了城，**逐座一并转回、全部按傀儡处理**，转完 `cities.isEmpty()` 才成立，`destroy()` 的前置条件才满足；③ `destroy()` 默认广播「城邦已被消灭」，和平召回要换文案。
3. 指令与上贡（间隔可设，默认 10 回合）
4. 独立要求 → 拒绝 → 独立战争 → 低忠心者协同反叛
5. `AgentPresenter : UnitTable.Presenter`（照 `SpyPresenter`）——外派的官员在地图上有位置，**这一步属于 R3 不属于 R2**

**验收**

- 傀儡城交出后变成可宣战 / 可议和的独立城邦；`morale` 三条件生效且初值等于该官员的 `attrs.loy`。
- **把一名在任阁臣外派后，内阁屏的 `S[k]` 与天赋触发状态当场变化**；召回后**原样恢复**（决策 30 定案 ① 的机会成本可见且可逆）。
- **召回往返无损耗**：交出 → 召回 后，城回到手上且 `isPuppet == true`、城邦文明已 `destroy()` 且不残留在存活集合里；**人口 / 血量 / 建筑全部零变化**（即证明没走 `conquerCity()`）；未打过仗则**好战惩罚也为零**。
- **代理战争实时记账**：城邦对第三方宣战 / 攻城的当回合，宗主国身上就出现 `WarMongerer` 修正；打完不召回也照样已经记上；召回时**不再二次施加**。
- **控制力跨移交连续**：交出前 N → 外派 M 回合（无抵抗）→ 召回后应为 `min(100, N + 2M)`，**不重置也不衰减**。
- **抵抗期不可白嫖**：在抵抗期内把城交给代理人，抵抗旗标必须**存活**且控制力**仍不增长**；抵抗结束后才开始涨。
- 上贡按间隔到账；拒绝独立触发战争；独立结局按「解放」处理，可回归可独立。
- **存档往返无环**（宗主国与城邦只能弱引用互指），且在「已召回、城邦已销毁」的存档上读档不出悬空引用。

### M — 模组与内容

#### M1 · M-mod-1

切片补规则集标签（资源 `kind` 已做，nation 相关待补）；**D4**：`factionBalance` 在非 RC 规则集下的语义（要么标注要么省略）；**D6**：遗留 `civsim` 原生决策写死 `Masonry`/`worker`（仅 `use_ai: native_unciv` 时触发，可能随 O3 一起处理）。

#### M2 · M-content-2

LLM 主动按需查询闭环：让模型在遇到不认识的模组内容时，能通过 `GET /v1/content?kind=&id=` 拉取内容卡再决策，而不是靠原版先验瞎猜（D5）。另可选「策略档案（按规则集指纹）」——**这是上限优化，不是不崩前提**。

### X — 体验与质量

#### X1 · 汉化补完

**缺口**（审计逐条列出）：`AgentTab` 的 Model / Streaming / Temperature / Max output tokens / Frequency & Presence penalty / Top P / Saved key / Starting local brain…；`LeaderDialoguePopup` 的 Restart brain / View last prompt / Restarting the brain…；`AgentPromptViewerPopup` 全文；`WorldScreenMenuPopup` 的 CivAgent settings；`AgentBrainLauncher.statusText()` 的状态串。

**注意**：用 Python 脚本改 `Simplified_Chinese.properties`（`scripts/fix_civagent_zh.py` 那套），PowerShell `Add-Content` 会写坏 UTF-8。占位符用**单层方括号**（`squareBraceRegex` 不认嵌套），改完跑 `TranslationTests`。

#### X2 · 多模态视觉

现状只有搬运：`/upload` 落盘 + 把路径字符串拼进提示词，**没有任何视觉能力**（PLAN Gap-6）。

按决策 16 补：体积/类型门禁（已有 4MB）→ **分类**（无关 / 外交相关 / 疑似泄雾或作弊）→ 相关的转 ≤N 字事实观察 → 与打字同一套「说」。无视觉模型时要求用文字说明，不猜测。默认不把识别结果整段回给玩家。

**验收**：自拍不产生任何意图；条款截图只改贸易草稿；全图/控制台截图当 `claim` 处理，绝不写迷雾。

#### X3 · 提示词预设薄层

流式呈现已完成（`kind=partial` 原地更新 + 客户端实时气泡）。剩下的是「酒馆式」预设：多预设 / 块开关 / lorebook。**结论仍是只做薄层**——身份、性格、迷雾切片、贸易规则这些是系统级不变量，不该交给用户预设覆盖，否则 I-4 / I-5 会被绕过。可开放的只有说话风格与长度偏好。

### D — 数据飞轮

#### D1 · 采集完整化

**已经在存**：`chat_{pair}.jsonl`（完整对白 + seq + draftIntents + 时间）、`{civID}.jsonl`（决策与落地回执）、`claims.jsonl`（主张 + 证伪结论）、`trust.json`。`scripts/export_chat_dataset.py` 可导出 SFT 格式（实测 10 条样本）。

**缺口**：① 导出只用了聊天，`claims` / `trust` / 决策记录未进样本；② 训练要字节级对齐的话，需要把**当次 system prompt 原文**落盘（现在只有 `_last_prompts` 内存里最后一条）；③ 没有评测集与指标，无法判断换模型/改提示是变好还是变坏。

**建议顺序**：先做 ③（固定一批局面 + 人工标注的期望行为，作为回归基准），再做 ②，最后才谈微调。

> **隐私**：导出物含真实玩家文本。只在本机使用；用于训练需当事人同意。

### O — 可选 / 搁置

- **O1 W-Lobby**：M-L1 短码开房（房主生成短码 ↔ `gameId`，他人输码加入，沿用现有 `userId` / 服密码鉴权）→ M-L2 大厅（房间列表、好友邀请、Ready、全员准备或 30s 后开）。加入时提示缺失 mod，沿用 Unciv 模组管理。**大厅服务不跑 LLM、不执行 CLI、密钥不进联机存档。** 不挡单人 Agent。
- **O2 M0 收尾**：两个 RC 客户端连 `localhost:8080` 同步 ≥3 回合。Server 已同源自构建（4.21.4-patch1），curl 级 PUT/GET 已过，只差 GUI 人工冒烟。
- **O3 旧栈处置**：`ai_server.py` / `civsim/` / `chatbot/` / `redis_mq` / `civagent/civagent.py` / `search.py` / `skills.py` / 旧 LLM utils 全部不在活路径。建议**移入 `legacy/` 并在 README 标注**（保留论文可复现性），而不是直接删。移动后要同步 `tests/conftest.py` 的跳过逻辑与 `scripts/tasks/`。

---

## 4. 交付纪律

改动落地必须走完，不跳步：

1. **对照不变式**：改动是否触碰 `PLAN.md` §2 的 I-1…I-8？触碰则先在 PR 描述里说明为什么不算违反。
2. **两侧测试**：Python `pytest tests/ -q`；Kotlin `:tests:test` **全量**（不只 civagent 包——devconsole 重构和翻译文件都有全量回归）。
3. **契约同改**：动了意图/切片字段，`docs/protocol/` 两个 schema 必须同批更新（I-2）。
4. **打包**：`:desktop:dist` → 备份旧 jar 为 `app/Unciv.jar.bak-<yyyyMMdd-HHmmss>` → 换入 `Unciv-SongDynasty/app/`。重建发布 zip 时**必须排除** `SaveFiles` / `MultiplayerGames` / `mods` / `ModListCache.json` / `lasterror.txt` / 旧 jar 备份（漏排一次出过 197 MB 的包）。
5. **重启**：改 Python 重启大脑；改 Kotlin 完全退出游戏再开。
6. **记录**：在 [§5 归档](#5-归档) 追加一条，写清**根因**而不只是「修好了」。

---

## 5. 归档

### 5.1 已完成的波次（W0–W6）

| 波次 | 结果 |
|------|------|
| **W0** 联机管道 | `UncivServer.jar` 同源自构建（`Specification-Version: 4.21.4-patch1`）；`/isalive`、`PUT/GET /files` curl 冒烟过；`MultiplayerFiles` 落盘路径随 `-f`。**双客户端 GUI 冒烟未做**（→ O2）。注意 4.21.x 的 `/files` PUT 和 GET **都要** Basic 头（用户名 = UUID）。 |
| **W1** 游戏内网关 | `ConsoleContext` 把控制台从 `DevConsolePopup` 解耦（游戏内行为不变）；`AgentGateway` 绑 127.0.0.1:8081（占用则回退临时端口）；`console_gm` / `console_agent` 两档；`AgentWhitelist` 席位鉴权；最小切片；协议 schema 冻结。 |
| **W1b** 厂商适配 | `llm_router` 统一入口，六厂商（deepseek / openai / openai_compatible / gemini / anthropic / ollama）；env > sidecar > yaml；`redact()` 脱敏；席位只覆盖模型 id、共用一把钥匙。 |
| **W2** 最小可玩 | `AgentTurnHook` 拦 `automateCivMoves`，15s 超时回原生；切片升 v2（存量+增速成对、城市一行、`factionBalance`、已知国 6 字段 + `vsSelf`）；大脑 `/v1/decision` `/v1/applied` `/healthz`；无 Redis 无 JVM 可 import。 |
| **W3** 说立刻做等回合 | `LeaderDialoguePopup`（顶栏 chat 图标进入，开场白用原版 `neutralHello`，零次 LLM）；`/message` 200 = 已开始 + SSE `/events`；草稿意图只在该席位**下一回合**落地；语言锁；席位锁与决策共用。 |
| **W4** 确认与贸易槽 | `AgentIntentConfirmPopup` + `IntentNarrator` 人话清单（绝不给原始 JSON）；30s 超时 = 拒绝且不重发；贸易走 `TradeRequest` 与原生 AI 同一通道；`AgentTradeSlots` 单槽；国库/GPT/资源三道保险丝。 |
| **W5** 情报信任地图 | `share_intel` 必须带文字主张，只记 `claim` 不开雾；`trust.json` sidecar（默认 0.40，高信任阈 0.65）；切片补 `proximity` / `sameContinent` / `landReachable` / `navyShipCount` / `borderThreats` / `nearestHostileCity`；`war_focus` 不可达则降级。 |
| **W6** 多模态 | 只完成搬运层：`/upload`（≤4MB）+ 对话「+」挂图 + 路径引用。**视觉/分类未做**（→ X2）。 |
| **W7** 论文仿真 | **判定不做**（2026-09-10）：没有真实 LLM 回复的仿真测不到 agent 行为。仅在需要论文数据时重启。 |
| **H** Host | `start-host.ps1/.bat` + `AgentBrainLauncher`（勾选 / 开局 / Options / 「+」菜单四个触发点）；`POST /v1/shutdown` 支持热重启。 |

### 5.2 模组兼容与内容桥（2026-09-08）

**审计结论**：任意模组不会「全崩」，但「换皮就行」也不对。缺陷矩阵 D1–D6 见 [docs/mod-compatibility.md](docs/mod-compatibility.md)。

- **M-mod-0 已落地**：**D1** `IntentExecutor` 在宣战/议和前检查 `DiplomaticRelationshipsCannotChange`（此前 `applyIntents` 跑在原生判断之前 = Agent 当 GM）；**D2** `civ_display_name()` 兜底（写死 6 个国家名遇到 46 个自定义国家 + 简中会 KeyError）；**D3** 切片加 `eraCount`，时代序按规则集缩放而非原版索引。
- **M-content-0/1 已落地**：`ContentCards`（kind/id/caps/desc/uniques，`caps` 由引擎谓词推导，`desc` 复用 `getCivilopediaTextLines`）；网关 `GET /v1/content` `/v1/ruleset` `/v1/ruleset/delta`；切片加 `ruleset` 指纹与 `resourceKinds`；提示词带规则集名与 mod 列表。
- **决策 26**：内容桥不需要数据库/向量库——规则集在内存且是 `kind+id` 精确键查表。

### 5.3 关键事故与根因（有复发价值）

| 事故 | 根因 | 教训 |
|------|------|------|
| **确认了却没议和** | `INTENT_TYPES` 广告 13 个类型，`IntentExecutor` 只实现 7 个；模型发 `seek_peace` 落到 `else` 静默失败 | 立 **I-2 广告集 == 可执行集** 与 **I-3 失败必须可见**；补齐 6 个意图、删掉做不到的 `common_enemy` |
| **开聊天必崩** | `AgentChatClient` 对「大脑没起」这一常态零容错，`ConnectException` 被当崩溃 | 外部进程不可达是**常态**不是异常；加 `isReachable()` + 离线提示行 |
| **玩家自己的话消失** | 两层：① `fetchMessages` 用 POST 打 GET-only 路由 → 405；② 席位名含空格/中文（`Sheng Tang`）直接拼进 query → `URL can't contain control characters` | 异常被吞成 `emptyList()` 是最难查的 bug；URL 参数一律 `URLEncoder.encode` |
| **旧意图被重复执行** | SSE `after=0` 回放把历史 `draftIntents` 重新入队 | `/message` 先分配 seq 再写 transcript；弹窗先水合取最大 seq 再订阅 |
| **议和落地时崩溃** | `AgentIntentConfirmPopup` 用 `stage.width`，而 `Actor.getStage()` 在 `open()` 之前恒为 null（Unciv 自己的 `Popup` 用 `stageToShowOn`） | UI 异常绝不能搞崩回合线程；两条确认路径都加了兜底 |
| **对话报 `No module named 'instructor'`** | 懒加载 `import instructor` 写在函数体内（本是测试缝），`/healthz` 只查 key → 「端口通 + 有 key」被判健康 | 健康检查要真的构造一次客户端；加 `client_dependency_problem()` → 503 `{"llm":"broken"}` |
| **测试忽红忽绿** | 用户在游戏里存了真实 sidecar，`civagent.config` 加载它 → 「未配置」类用例看到真 key | `tests/conftest.py` autouse fixture 屏蔽 sidecar + 清空 LLM 环境变量 + reload 三个缓存模块 |
| **发布包 197 MB** | robocopy 未排除 `mods`，用户装的真模组（含 `.git` pack 与 mp3）混进包 | 打包排除清单写进 [§4 交付纪律](#4-交付纪律) |
| **模型给不出贸易数量** | 聊天提示词根本不带经济数据（切片只走 `/v1/decision`） | 缓存最近切片 `_last_slices`，聊天注入 `_economy_line()` + `_trading_rules()`；无观测时明写「不要编数字」 |
| **信任证伪永不触发** | 玩家主张以 `kind="other"` 落盘，验证器只认 `war`/`peace` | 加 `classify_claim_kind()` 中英关键词分类 + `massing_troops` 走 `borderThreats`；不可观测类型保持 open |
| **模型跟着对方写文言文** | `VOICE_BY_CIV` 只有 6 个原版国家，MOD 国家落通用卡 | `generic_voice_for()` 按国名生成 + 所有口吻追加 `_ANTI_MIMICRY` |

### 5.4 其它已落地的细节

- **密钥 sidecar**：Options → CivAgent 页（provider / key 密码框 / base URL / model / streaming / 五个采样参数 / brain URL / repo path），写 `~/.civagent/secrets.json`，`POST /v1/config` 热加载，响应只回 `keyHint`。游戏内 ☰ 菜单也有入口。
- **提示词查看器**：大脑记每席最后一次 prompt → `GET /v1/prompt` → `AgentPromptViewerPopup`（显示 provider/model/streaming/sampling + token 估算 + 完整原文）。入口在对话「+」与 Options。
- **流式**：`chat_stream_text()` 绕过 instructor 纯文本累加，SSE 发 `kind=partial` 原地更新，客户端不为 partial 推进 `lastSeq`，收到 `reply` 定型。
- **行动记录**：Unciv 对 `PlayerType.AI` 席位 `addNotification` 直接 return，所以 `AgentMod.actionLog` 自记最近 12 条（含失败原因）→ 切片 `recentAgentActions` → 提示词「不得自相矛盾或重复」。
- **切片贸易状态**：`agreements`（引擎渲染的已签协议 + 剩余回合）、`hasOpenBorders`、`modifiers`（按权重前 6）、`incomingTradeRequests`、已知文明 `resources`（同受 `hideOtherCivilizationStats` 门控）。
- **对话 UI**：领袖名用 `getLeaderDisplayName()`；标题拖动 + 右下角缩放（改**单元格**尺寸，只改 ScrollPane 会被 `pack()` 拉回）；正文 `setWrap(true)` + 宽度约束；打开定位到最新一条；Enter 发送。
- **单人局**不再显示联机手柄状态钮。

### 2026-09-11 · P0 三刀（P1/P2/P3）+ W5.7 证伪钩子 + denounce（第六次）

**W5.7 收尾 — 证实/证伪钩子 + 谴责意图**

- `trust_store.verify_pending_claims`：决策时以切片为"通知源"核对未决 war/peace 主张——`aboutCiv` 已遇见且状态吻合 → 证实（信任 +0.05）；超 10 回合窗口未兑现 → 证伪（−0.05）；未遇见/未过期保持 open；resolution 记录入 claims.jsonl 保证每条只结算一次。信任对 =（接收者，主张者）。
- `IntelPayload` 增 `kind`（war/peace/other）/`aboutCiv`；`/v1/decision` 与聊天 share_intel 均记录。
- **denounce 谴责意图**：Python 白名单 + schema + README；Kotlin `AgentWhitelist`（席位规则同宣战）与 `IntentExecutor`（console `diplomacy denounce`）。

**P2 — 契约对齐（不变式 I-2 由测试守住）**

- `common_enemy` 从 `INTENT_TYPES` / `intent.schema.json` / README 白名单移除（legacy 栈不动）；`intel.kind/aboutCiv` 补进 schema。
- `agent_api.intent_type_list()` 改为从 `INTENT_TYPES` 派生（单一事实源）。
- 新 `tests/test_protocol.py`：INTENT_TYPES == schema 枚举 == README 清单，三方漂移即红。

**P1 — 外交「覆盖」方案 A（EXECUTION 推荐案）**

- `AgentTurnHook.applyIntents` 返回 `Set<Coverage>`（DIPLOMACY / TRADE，来自 ok 的意图类别）；`nativeSkipSet(covered)` 映射：DIPLOMACY → 原生 `declareWar`/`offerPeaceTreaty`/`denounce` 本回合跳过；TRADE → 原生 `exchangeLuxuries` 跳过（不再破坏贸易单槽）。
- `NextTurnAutomation.automateCivMoves` 按映射门控；未覆盖类别与脑失联回合照走完整原生（超时回原生语义不变）。
- 回归测试：宣战成功 → DIPLOMACY 覆盖且跳过集正确；脑失联 → 空覆盖；意图失败（未知目标）→ 不覆盖不压制原生。

**P3 — 切片/叙述器/贸易槽测试（顺带修出两个真 bug）**

- 新 `ObservationSlicerTest`：未遇见文明不可见；`hideOtherCivilizationStats` 剥光全部统计字段但保留关系/距离；存量/增速成对非空；本席数字与引擎一致。
- 新 `NarratorAndSlotsTest`。**Bug 1（游戏内也坏）**：`IntentNarrator` 先 `.tr()` 后 `fillPlaceholders`——无翻译项时方括号被剥掉、占位符字面残留；改为 Unciv 惯用法「值先进方括号、再单次 `.tr()`」，游戏内本地化与无头测试同时正确。**Bug 2**：`AgentTradeSlots` key 不对称（`rome|egypt` ≠ `egypt|rome`），与"每对文明一条"契约不符；key 改为排序对称。
- `Trade` 无 equals——槽测试用 assertSame。

**验证**：Python **93/93**；Unciv `:tests:test` 全量 + `:desktop:dist` BUILD SUCCESSFUL；jar（含 P1-P3 修复）已换入 `Desktop/Unciv-SongDynasty/app/`。

**任务板余项**：P4 性格锁、P5 贸易槽对账、P6 死代码、R1/R2（控制力/内阁主线）、X 系列。

### 2026-09-11 · P4/P5/P6（第七次）

**P4 性格锁端到端**

- `ObservationSlicer.renderPersonality(civ)`：读 `civ.getPersonality()`（即 `ruleset.personalities[nation.personality]`），15 个 `PersonalityValue` 维度里只渲染偏离中性 5 的项（`declarewar 8, loyal 3` 风格），全中性输出 `neutral`——切片小、信息密度高。
- 大脑 `seat_personality(civ_id, slice_personality)`：**切片值优先**（标 "(engine nation personality)"），`config.yaml` 席位块仅作覆盖；`neutral`/缺失回退原逻辑。聊天路径（无切片）保持 config 兜底。
- 测试：AI 席引擎性格进切片（含只列偏离维度）、人类席 `neutral`、大脑优先级两态。

**P5 贸易槽对账（引擎为唯一事实源）**

- `AgentTradeSlots` 内部改持 `SlotEntry(fromCiv, toCiv, trade)`；新增 `reconcile(gameId, civ)`：本席发起的槽，若引擎侧对应文明 `tradeRequests` 已无本席挂起请求（被接受/拒绝/过期）→ 清槽。`applyIntents` 每席回合入口先对账。
- 重启/读档后槽空无害：空槽不撤旧，`put` 本就先撤引擎旧请求——Gap-5 的「重复投递/不一致」随引擎权威消解；槽 key 对称化（pair 语义）。
- 测试：挂起请求保槽；引擎侧清空后 reconcile 清槽。

**P6 死代码**

- 删 `ChatReplyParser.kt` + `ChatReplyParserTest.kt`（围栏解析只在大脑一端；弹窗直接消费 SSE `intents[]`）。

**验证**：Kotlin civagent 包全绿 + `:tests:test` 全量 + `:desktop:dist` BUILD SUCCESSFUL，jar 已换入 `Unciv-SongDynasty/app/`（121 个 civagent 类）；Python 活跃套件 76/76（全量 93 含协议/信任/路由）。PLAN.md Gap-1/2/3/4/5 全部标 ✅，风险表「外交叠加」条目改「已消解」。

**任务板余项**：R1/R2/R3 主线（控制力/内阁/傀儡城邦，按指示 R1 未动）、M1/M2、X1–X3、D1、O1/O2/O3。

### 2026-09-11 · P-memory 事件流 + 三层记忆检索 + 信任可追溯（第八次）

**事件流（原生通知 → 大脑记忆）**

- `AgentMod.drainNewEvents`：以 `civ.notificationsLog`（按回合分组、每个文明都写、天然带迷雾语义——是该文明自己被告知的事）为源，per-seat 游标差分出"自上次决策以来的新通知"，单次最多 40 条防爆量；`applyIntents` 把它们作为 `slice.newEvents` 随 `/v1/decision` 搭车上行（零新端点）。
- 大脑把 `newEvents` 落成 `kind=engine_event` 记录（turn/category/text）。游标推进保证同一通知只送一次（测试断言）。

**三层结构化检索 `select_memory()`（纯 jsonl 过滤，决策 23 不破）**

- 近因层：最新 12 条（取代旧固定窗口）；
- 显著层：全历史的 War/Trade/Diplomacy/Cities 类引擎事件（最多 8 条）——"第 9 回合谁对我宣战"在 100 回合后仍能进 prompt；
- 对手层：含当前谈判对象文本的记录（最多 6 条）。
- 去重、显著/对手层在前、近因收尾。`decision` 路径已切换；聊天路径（无切片）仍用近因窗口。

**信任可追溯（用户问题的直接落地）**

- `bump_trust` 增 `reason`/`evidence`，每次变动追加 `trust_log.jsonl`（delta、变动后值、理由、证据）；三处调用点全部带上：主张证实/证伪引用主张原文与回合，收到玩家情报 +0.03 引用情报内容。
- 新端点 `GET /v1/trust?gameId=&civID=`：返回该席信任表 + 最近 30 条变动日志——AI 为什么信/不信某人，随时可查证据链。
- schema：`observation_slice.schema.json` 增 `newEvents` 定义（不变式测试仍绿）。

**验证**：Python **104/104**（新增 9：事件落盘/三层检索去重/对手层/信任日志证据链/端点）；Kotlin 全量 + dist 绿，jar 已换入 `Unciv-SongDynasty/app/`。

**备注**：通知文本是引擎未翻译串，由大脑语言锁负责玩家语言；`NotificationsLog` 若被引擎裁剪只影响"更早以前"的补充，游标机制不会重复也不会漏发窗口内事件。

### 2026-09-12 · 实测五问题修复（第九次，截图回归）

用户实测发现五个问题，根因与修复：

1. **确认弹窗语言/可读性**：决策 chatText 语言不受控（瑞典席吐瑞典语、巴比伦吐英文）+ 长篇散文淹没动作行。修复：`ObservationSlice` 增 `language`（客户端游戏语言随切片上行）；决策 system prompt 语言铁律改为「chatText 必须用 {language}，无视国家 flavor voice」；narrator 对 chat 行截断 220 字符——弹窗先看动作、再看摘要。
2. **议和失败**（"Format: diplomacy make-peace"）：**不是回合机制问题**（AI 先动不影响——console make-peace 在该席自己回合内即时生效；聊天草稿等下回合是 W3 设计）。真根因：`Sheng Tang` 带空格的文明名未加引号，console 按空白分词后参数超格式 → Format 拒绝。修复：`IntentExecutor` 拼命令一律给文明名加引号；`AgentWhitelist.tokenize` 改为引号感知分词（此前带引号的 actor 也无法匹配席位）。回归测试以 "Sheng Tang" 建国验证宣战→议和全链路。
3. **贸易只见聊天不见动作**：决策提示词补「ACTIONS OVER PROSE」——讨论货物交换必须同批发 `propose_trade`/`buy_luxury` 意图，光说不做不算；chatText 限 60 词。
4. **Agent 行动不进原生通知**：此前只有失败 toast（且 toast 的 tr 占位符用错，屏显 "civNamecivName有 count 项" 乱码）。修复：每批确认的意图（除 chat）逐一写入**所有已遇见该文明的人类玩家**的原生通知日志（分类 War/Trade/Diplomacy，持久可查），失败条目附拒绝原因；toast 文案改纯文本。
5. **"行动无法通过"**：即 #2 的 Format 拒绝，同一根因。

**验证**：新增回归测试（Sheng Tang 议和链路、人类通知落日志、引号分词）；Python 活跃套件 35 + 全量、Kotlin civagent 包 + 全量 + dist 全绿；jar 已换入 `Unciv-SongDynasty/app/`（完全退出再开）。

### 2026-09-12 · 保险丝误伤修复（同日补刀）

用户截图实测：纯资源贸易（棉花换盐/黄金）被 `gold cost 0 exceeds treasury -4` 误拒——金库保险丝写成 `cost > treasury`，在赤字国库（Civ5 允许负金币）下 `0 > -4` 恒真，无金币报价也被拦。修复为 `ourGoldCost > 0 && ourGoldCost > civ.gold`：只有真的报了金币且超出国库才拦截。回归测试：赤字国库 + 开放边境换金币 → 正常投递。同屏"宝石换 6 金被拒 (have 0)"是 LLM 幻觉资源被正确拦截——保险丝本职。jar 已换入。

### 2026-09-12 · 确认弹窗改 scopes：只审与你相关的事 + 全部接受（同日第二刀）

用户实测反馈：AI 之间的外交贸易不该要玩家审批，且缺"默认接受"。产品语义回归 PLAN 5.4（审批的是**对你**的动议）：

- `AgentTurnHook.needsConfirmation(gameInfo, intents)`：仅当批次含 **targetCivID == 人类玩家席** 的非 chat 意图时才弹窗；AI↔AI 的宣战/议和/贸易直接落地，靠原生通知留痕（上一刀的 #4 机制）；模拟/无人局一律自动放行。
- 弹窗三按钮：**全部接受**（本会话不再询问，`AgentConfig.autoAcceptIntents`）/ 接受 / 拒绝；关闭弹窗 = 拒绝（原语义不变）。
- 测试：AI↔AI 议和不触发审批、对玩家宣战必须审批、纯 chat 不审批；原 W4 拒绝/失败 handler 两个用例改为针对玩家席（语义更新）。
- 全量测试 + dist 绿，jar 已换入。

### 2026-09-12 · 审批恢复开关（同日第三刀）

「全部接受」原是单向门（仅重启复位）。现 Options → Agent 增 **Approvals 复选框**（"Ask before actions that target me"）：实时绑定 `AgentConfig.autoAcceptIntents`，勾上即恢复审批、取消即静默放行（等同"全部接受"）；灰色说明写明两种语义。重启游戏该标记天然复位为"询问"。全量测试 + dist 绿，jar 已换入。

### 2026-09-12 · 训练场贯通：MCP + 对称地图 + 无头驱动器 + 首次真训练（第十次）

**四点全部落地（用户提问「能否做到」的答案 = 已做到）**

1. **Unciv MCP**（`civagent/mcp_server.py`，零依赖 stdio JSON-RPC）：game_status / get_observation / run_console_gm / run_console_agent / manage_seats / trust_report 六工具，包 AgentGateway 实现实时操作；Claude Desktop 式注册文档在文件头。
2. **对称地图生成器**（`arena/map_generator.py`）：复刻 Unciv 矩形坐标公式（含 Kotlin 向零取整陷阱），81×81 / 41×41 双规格；对称由构造保证（半区生成+镜像赋值）；实测对称差 0；含全基线地形/地貌、4 自然奇观（2 镜像对）、23 种资源、出生点。已写入客户端 `maps/`（游戏内可选）。
3. **出生点模板**（`arena/spawn_templates.yaml`）：国家池 = cathay mod 四国（Sheng Tang 盛唐/MongolEmpire 蒙古帝国/Qindynasty 秦/Late Tang 晚唐）+ 基线主力（Babylon/Ethiopia/Sweden/…）；1v1 Agent 对抗 / 1v1 人机 / 2v2 队伍 / 4 人混战四模板。
4. **无头训练场**（`ArenaLauncher.kt`）：地图文件开局 + 席位角色 + 循环 nextTurn；修三处（默认玩家列表、全 AI 局缺 Human 席 → 加观战席、终局存档绕过 Gdx Files 直写 Base64(gzip) 标准格式）。周期存档：turnsBetweenAutosaves=10 + 终局 `SaveFiles/arena-final-{gameId}`。

**首次真训练（20 回合 + 12 回合复跑）**

- Agnes 钥匙实测有效（`https://api.agnes-ai.cn/v1`，OpenAI 兼容；agnes-2.5-flash 出话）；写入 `.env`（gitignored），llm_router 全链路验证通过。
- 20 回合双 Agent 对打：`/v1/decision` 每席每回合调用、`/v1/applied` 回执、轨迹落盘 `deployment/data/{gameId}/{seat}.jsonl`。开局无邻居时 LLM 正确地只发外交宣言不做凭空动作（呼应「没遇到不能交流」）；语言锁起效（切片 language）。
- 终局存档实测可解码（Base64→gzip→GameInfo JSON，12 回合/1681 瓦/三文明）。
- 训练方法（决策 6.1 档 1）：跑 N 局 → 轨迹 jsonl + 终局存档 → 评测（胜负/意图有效率/回执 ok 率）→ 调 prompt/阈值 → 再跑。**不引入权重训练**，数据够之前不动 ML。

**版本**：两仓已 commit + 本地 tag **v0.2.0**（未推送）。

**注意/遗留**：
- ⚠️ config.yaml 的真实 DeepSeek 钥匙已随 v0.2.0 提交进**本地** git 历史（未推送远端）。建议轮换该钥匙；后续提交前会先脱敏工作副本。
- ArenaLauncher 调试 println 待清；`SaveFiles` 路径为工作目录（android/assets/SaveFiles），客户端 GUI 存档互不相通（jpackage 数据目录）。
- 41×41 每回合约 1-3 秒（含 LLM 决策）；81×81 只用于正式评测。

### 2026-09-12 · 三反馈修复（第十一次）：价值分层 / 编辑器缺国家 / 左右对称

1. **价值标准 = 混合分层**（用户问「1 产能≈多少金币要权威还是动态」）：
   - **动态权威层（引擎真值）**：CityRow 增 `constructionBuyCost`（`getStatBuyCost(city, Gold)`——现在就把当前建造买断的金价）与 `constructionTurnsLeft`（`turnsToConstruction`）——引擎算的，比任何换算公式都权威；
   - **静态先验层（系统提示）**：1 产能≈2.5 金买值、1 科研≈1 金长期、粮食服务成长、快乐是硬上限；明示"粗锚非定律，城市行里带引擎数字时优先用引擎数字"。
2. **编辑器缺国家**：出生点引用盛唐/蒙古帝国，未启用 cathay mod 的编辑器校验不通过。生成器加 `--no-starts`，产出 `maps/symmetric-arena-editor.json`（无出生点、编辑器可开）；带出生点的版本人局/训练场照常（实测首都落在模板镜像位：长安(-12,-14)/哈拉和林(12,14)）。
3. **左右对称**：镜像变换从点对称 (x,y)→(-x,-y) 改为 **左右镜像 (x,y)→(-x,y)**（左=你，右=镜像世界，人眼直观）；模板出生点改同行对置；构造性对称重验：**0 差异**，4 奇观（2 左右对）、623 资源、三张图（81×81 正式 / 81×81 editor 变体 / 41×41 快速迭代）全部重新生成并同步到客户端与训练场工作目录。
- 全量 tests + dist 绿，jar 已换入；大脑已用新提示词重启（openai_compatible→Agnes）。

### 2026-09-12 · 半小时批处理 + 首份三方法论评估（第十二次）

- 地图升级：arena-51x50.json（51 列镜像轴 + 50 行），**每边 5 奇观**（预筛成对池强制配满，实测左 5 右 5、对称 0 差异）；generator 支持 --wonder-pairs/--no-starts。
- ArenaLauncher 增**每回合国力采样**（arena-stats-{gameId}.jsonl：gold/科研/文化/科技数/城市/人口/军单位/军力/攻占城数）——三套评分离线可算，无需重跑对局。
- `arena/evaluate.py`：A 国力增速（斜率）/ B 征服进度（攻占+军力峰值）/ C 性格加权（balanced/money/war/science 四档权重）。
- `arena/run_batch.py`：10 国轮换（cathay 四国 + 基线六国）1v1 批量，墙钟预算控制。

**首局实测（盛唐 vs 蒙古帝国，双 CivAgent，agnes-2.5-flash）**：30 分钟跑了 **218 回合**（≈8.3 秒/回合，LLM 决策在环），未到 250 上限、未分胜利，预算截断。终局盛唐全面领先：金 8828 / 科 5572每回合 / 28 城 / 820 人口 / 攻占 2 城 vs 蒙古 0。
**三方法论本局一致判盛唐占优**（国力增速 20.3 vs 11.8；征服 2:0；war/balanced 加权同判）——方法论分歧要在胶着局才有区分度，这正是需要批量继续积累的原因。
**结论与下一步**：① 单局 30 分钟跑不完 250 回合 → 10 国轮换要么延长预算、要么降本（小图/无 LLM 原生基线/多实例并行）；② 首局数据已可回答「评分机制」问题：三套并存、数据齐全、分歧时再看。

### 2026-09-12 · 国家强度分档评估方案（T0/T1/T2，v0.2.1 起点）

**三强度档位**（用户定纲）：
- **T0 原生基线**：双席均 native（零 LLM），测**国家本身强度**——引擎加成、开局位置、AI 自动化的纯对比。这是方法论调节的对照组。
- **T1 LLM 方法论**：被测席 civagent（Agnes），对手 native——测「大脑 X 国家」的合力。
- **T2 方法论对打**：双席 civagent、不同提示词/性格/方法论——测方法论孰优。
- 评估顺序：先 T0 定国家强度基准，再调方法论（不跳步）。

**回合分档**：50 / 100 / 150 三档（200 暂缓）。同局 stats jsonl 覆盖三档检查点，一局三用。

**难度分档**：皇帝（Emperor）起步、仙人（Immortal）可加——难度加成对双 AI 对称生效，不破坏相对比较，但 AI 进攻欲望提升大幅缩短对局。ArenaLauncher 已支持 `--difficulty`。

**固定出生点（Q3 决策）**：**是**。被评估国固定左出生位、参照对手（罗马，原版 native）固定右出生位、地图与种子恒定——控制变量，否则测的是运气。

**首轮任务**：≥6 国原生基线（盛唐/蒙古帝国/秦/晚唐 = 无悔华夏 cathay mod；巴比伦/埃塞俄比亚 = 原版），Emperor、150 回合档、罗马参照，逐国 T0 跑完出排名。

### 2026-09-12 · T0 原生基线六国评估完成 + 自动清理（第十三次）

**排名（Emperor、150 回合、罗马参照、固定出生位，势比 = 综合国力/罗马综合国力）**：

| 排名 | 国家 | @50 | @100 | @150 | 终局 金/科技/城/人口/军力 |
|------|------|-----|------|------|--------------------------|
| 1 | Ethiopia（原版） | 1.07 | 1.41 | **1.66** | 552/47/5/129/21 |
| 2 | Qindynasty 秦（cathay） | 0.73 | 0.92 | 1.06 | 575/58/8/186/111 |
| 3 | MongolEmpire 蒙古帝国（cathay） | 0.87 | 0.93 | 0.96 | 1605/48/8/112/139 |
| 4 | Sheng Tang 盛唐（cathay） | 0.83 | 1.09 | 0.92 | 1277/41/**20**/158/**644** |
| 5 | Babylon 巴比伦（原版） | 0.98 | 0.89 | 0.84 | 335/48/6/95/21 |
| 6 | Late Tang 晚唐（cathay） | 0.14 | 0.11 | **0.08** | 被罗马消灭（0 城） |

**洞察**：埃塞俄比亚（原版）原生最强且持续上升；秦是唯一稳健爬升的 cathay 国家；盛唐是极端扩张型（绝对城市数 20 断层第一，但中段后被罗马军力反超势比）；**晚唐原生被罗马全程压制直至灭国**——T1/T2 若用晚唐，大脑面对的是逆风局测试床。
**工程**：修复孤儿 JVM 根因（网关线程改 daemon + 终局显式 stop，实测 61 秒/局干净退场）；`run_batch.py` 加**自动清理**（每局前后按命令行匹配击杀残留 ArenaLauncher 进程）。
**产出文件**：`arena/t0-baseline-ranking.json` + SaveFiles/arena-stats-{4b4c6059(盛唐),及另5局}.jsonl + 各局终局存档。

### 2026-09-12 · 待办与观测（用户指出 python 每次约 1 分钟 CPU 高占用）

- **现象**：python 进程周期性 CPU 高占用约 1 分钟——时长与单局对局时长（~61s）吻合，怀疑对象：批量调度脚本的等待实现 / 大脑在对局期间的行为。**下局训练时在启动后 1 分钟、5 分钟各做一次逐进程 CPU 快照**，实证定位。
- **已修**：大脑服务器偏好链 waitress（生产级 WSGI，已装）→ gevent → werkzeug 开发服务器（兜底）。生产级 WSGI = 专为常驻服务设计的 HTTP 容器（正确的事件循环/线程池/优雅超时），空转占用接近 0；werkzeug 开发服务器是单实例调试用途，常驻会有额外线程开销。
- **已修**：大脑单例锁（2335 已有实例则拒绝启动，杜绝僵尸实例堆积）。

### 2026-09-12 · T0 v2 修正权重版 12 国排名（第十四次）

权重改为用户定纲：军力×10、人口×8、城市×8、奇观×30、科技×4、文化/信仰存量×0.5、引擎分×0.5。旧采样清除，12 国全部重跑（Emperor、150 回合、罗马参照、每局 ~61s）。

| 排名 | 国家 | @50 | @100 | @150 | 终局亮点 |
|------|------|-----|------|------|----------|
| 1 | **MongolEmpire 蒙古帝国** | 1.35 | 1.52 | **25.86** | **唯一正面击溃罗马的局**：攻占 9 城，罗马后期国力归零 |
| 2 | Babylon 巴比伦 | 1.22 | 1.43 | 1.62 | 稳健型，10 奇观 |
| 3 | Egypt 埃及 | 1.02 | 1.09 | 1.55 | 均衡 |
| 4 | Persia 波斯 | 0.88 | 1.04 | 1.42 | 金库 2420 最高 |
| 5 | Greece 希腊 | 0.75 | 0.66 | 1.39 | 后程发力，16 奇观 |
| 6 | Qindynasty 秦 | 0.85 | 0.83 | 1.32 | 科技 59 全场第一 |
| 7 | India 印度 | 0.85 | 1.39 | 1.26 | 中段强势 |
| 8 | Ethiopia 埃塞俄比亚 | 0.80 | 1.16 | 1.18 | 旧权重榜首，军力权重修正后跌至第 8 |
| 9 | Sheng Tang 盛唐 | 0.75 | 0.62 | 1.12 | 金库 4856 全场最高，军力 249 |
| 10 | Songhai 桑海 | 0.80 | 0.66 | 0.89 | — |
| 11 | Sweden 瑞典 | 0.68 | 0.33 | 0.33 | **破产**（-1699 金，军力归零） |
| 12 | Late Tang 晚唐 | 0.17 | 0.08 | 0.04 | 两轮一致垫底，再次被灭 |

**关键结论**：
1. 权重修正导致排名真实翻转（埃塞俄比亚 v1 第 1 → v2 第 8；蒙古帝国升至第 1）——印证「先定权重再评强度」的必要性，方法论调节要以 v2 权重为准。
2. 蒙古帝国是 T0 唯一取得**实质性胜利**的国家（攻占 9 城、罗马崩盘）——T1 挂 LLM 后其他国家的排名位移就是方法论贡献度。
3. 晚唐两轮一致垫底灭国 → T1 逆风局测试床确认。
4. 瑞典破产案例：Emperor 下 AI 经济管理失败样本，值得留轨迹分析。
产出：`arena/t0-v2-ranking.json`；已同步修复权重进 `arena/evaluate.py`。

### 2026-09-12 · 「晚唐最弱」翻案：地图生成器出生点 bug 修复（第十五次）

**排查链**：晚唐 150 回合 0 城 → 存档核实 0 单位（非被灭，开局就没被放置单位）→ 出生点迁移逻辑把地形整备到了新坐标，但 `startingLocations` 仍写**旧坐标**（海上孤地）→ 引擎静默放置失败 → 裸奔 150 回合。

**修复**：`add_starting_locations` 返回迁移后坐标（render 回写）；出生点建城空间校验（两侧半径 3 合计 ≥30 陆地，否则沿行迁移）；孤儿出生点跳过。

**修复后重测（晚唐 vs 罗马，native，Emperor，150 回合）**：第 10 回合建城，150 回合 **21 城 / 265 人口**（vs 罗马 6 城/106）——势比 @150 = **3.5，全榜第二**（仅次于击溃罗马的蒙古帝国）。

**修正后 T0 排名（前六）**：蒙古帝国 25.86 > **晚唐 3.50** > 巴比伦 1.62 > 埃及 1.55 > 波斯 1.42 > 希腊 1.39。盛唐第 10（1.12）——纯扩张经济流在军力×10 权重下吃亏，但这正是 T1 要验证的（大脑会替盛唐补军事短板吗）。

**教训**：评估出现异常值（150 回合 0 城）时必须开存档验尸，而不是记录为「国家弱」。

### 2026-09-12 · 槽位轮换交叉测试（16 次）：国家 × 槽位矩阵（第十六次）

**配置**：64×64 环绕图、6 固定槽位（左 3 右 3 镜像对，评级：L2/R2 发展型、L1/R1/L3/R3 混合偏早战）、12 国拉丁方轮换（每国 2 局 × 2 个不同槽位）、T0 纯原生离线、Emperor、150 回合。

**综合排名（相对势指数均值 + 占城 0.2/城 + 灭国 90/国）**：
Sweden 5.31 > MongolEmpire 3.50 > Songhai 3.30 > Sheng Tang 2.73 > Greece 2.56 > Egypt 2.44 > Qindynasty 2.39 > Late Tang 2.26 > India 2.20 > Ethiopia 2.06 > Rome 1.93（参照席）> Persia 1.89 > Babylon 1.26

**交叉发现（国家 × 槽位矩阵的信号）**：
- **Sweden 单人 1v1 破产（-1699 金）、混战却登顶（5.31）**——它的引擎玩法在混战/多邻环境下反而强势，1v1 对照罗马的口径掩盖了它。这直接证明「单一方法论排名会误判国家」。
- **MongolEmpire 全能**：两槽位均 1.4+（混合型 L1 拿下 2.10）——扩张+军力双修，无短板。
- **Sheng Tang 偏爱早战带**：混合槽 1.33/1.41，发展槽反而平庸——「盛唐缺军事」的结论要修正为「盛唐在竞争压力下会起军事」。
- **Songhai 早战带爆发**：R3 混合带 2.03 全场单局最高之一。
- **Persia/Babylon 两槽位均平庸**——1v1 强势是对照口径红利，混战强度存疑。
- 槽位侧：发展型 L2/R2 的局内中位被食物经济抬得很高（Late Tang 在 L2 打出 1.61 全场最高单槽分之一）。

**产物**：`arena/rotation-cross-report.json`（矩阵+排名+每局明细）、`arena/spawn-report.json`（槽位评级）、`arena/slots-64x64.json`（固定槽位）。

### 2026-09-12 · 16 国 T0 最终排名（第十七次：73 局全量统计）

数据：73 局有效采样（全部 ≥150 回合、新权重字段齐全），每国 2-7 局。晚唐坏出生点局剔除；罗马为固定参照尺（不入榜）。评分 = 势比@150 均值 + 0.2×占城 + 90/500×灭国。

| 排名 | 国家 | 局数 | @150 势比 | 占城 | 灭国 | 加成后评分 |
|------|------|------|-----------|------|------|-----------|
| 1 | MongolEmpire 蒙古帝国 | 5 | 13.39 | 25 | 0 | 14.39 |
| 2 | Sweden 瑞典 | 6 | 7.01 | 10 | 1 | 7.53 |
| 3 | Songhai 桑海 | 5 | 6.23 | 2 | 1 | 6.49 |
| 4 | Greece 希腊 | 5 | 5.66 | 2 | 1 | 5.92 |
| 5 | Qindynasty 秦 | 6 | 5.63 | 4 | 0 | 5.76 |
| 6 | India 印度 | 5 | 4.64 | 1 | 1 | 4.86 |
| 7 | Ethiopia 埃塞俄比亚 | 6 | 4.81 | 1 | 0 | 4.85 |
| 8 | Sheng Tang 盛唐 | 5 | 4.29 | 8 | 0 | 4.61 |
| 9 | Egypt 埃及 | 5 | 4.24 | 0 | 1 | 4.42 |
| 10 | Late Tang 晚唐 | 7 | 3.06 | 1 | 0 | 3.09 |
| 11 | Persia 波斯 | 5 | 2.84 | 0 | 1 | 3.02 |
| 12 | Babylon 巴比伦 | 6 | 1.96 | 0 | 0 | 1.96 |
| 13 | Russia 俄罗斯 | 2 | 1.41 | 0 | 0 | 1.41 |
| 14 | America 美国 | 3 | 0.83 | 0 | 0 | 0.83 |
| 15 | France 法国 | 2 | 0.60 | 0 | 0 | 0.60 |

**分层读法**：
- **T0 梯队（≥5）**：蒙古帝国（征服机器，5 局占 25 城）、瑞典（混战之王）、桑海/希腊/秦——早战与扩张兼备。
- **T1 梯队（3-5）**：印度/埃塞/盛唐/埃及/晚唐——发展稳健，军力或扩张某一项突出（盛唐 8 城攻占）。
- **T2 梯队（<3）**：波斯/巴比伦（种田型，军力权重下吃亏）、俄/美/法（样本少，@50-100 尚可后程乏力，需补局）。

**方法论调节输入（T1 测试的假设）**：盛唐/晚唐/巴比伦这类 T1/T2 国家挂大脑后若能守住前中期，排名位移将最大；蒙古帝国的大脑只需「不犯错」。

### 2026-09-12 · 僵尸大脑清理 + 单例锁加固 + 强度测试优化（B2/B3）

- **用户问「是不是残留了 python 进程」→ 是，而且严重**：`agent_api` **29 个进程 / 1391 MB**，其中只有 **2 个**真正 Listen（`18396`、`16408` 同时绑 `0.0.0.0:2335`）→ **请求在两个大脑之间轮询**（两套独立状态，足以解释行为不一致）。
- 根因两条：
  1. `_serve` 依次尝试 waitress → gevent → **werkzeug 开发服务器**（本机前两者都没装）；werkzeug 设 `SO_REUSEADDR`，**Windows 上允许多进程绑同一端口**；
  2. 旧单例锁只做 `connect_ex` 探测，**挡不住同时启动的竞态**；而客户端每个 civ 回合都会调 `ensureRunning`。
- 修复：
  - `__main__` 单例锁改为 **connect_ex + 独占 bind 双重判定**（bind 失败即退出）；
  - `_serve` 绑定改 **127.0.0.1**（原来 `0.0.0.0`，**暴露到局域网**，而进程内存里有 API key）→ 局域网已连不上；
  - `AgentBrainLauncher` 加 **20s spawn 冷却**（`RETRY_COOLDOWN_MS`），不再每回合都尝试拉起。
- 清理结果：**29 → 1**，内存 **1391 MB → 45.7 MB**；第二个启动被正确拒绝（`singleton lock, exiting`）。
- **顺手做掉已批准的 B3**：`borderThreatMap` 的 `getTilesInDistance`（已废弃、编译期警告）改 `forEachTileInDistance`，去掉每回合 `O(城×61)` 的序列分配（警告随之消失）。
- **测试隔离又漏一处**：14:46 出现 `.env`（Agnes 中转：`OPENAI_BASE_URL=https://api.agnes-ai.cn/v1`、`LLM_DEFAULT_PROVIDER=openai_compatible`）。`config._load_dotenv()` **直接写 os.environ**，绕过 monkeypatch → 8 个测试变红。加 `CIVAGENT_NO_DOTENV` 逃生开关 + conftest 设置它；`.env` 本就在 `.gitignore` 里，不会提交。
- 验证：Python **104 passed**；Kotlin `:tests:test` 全量 **BUILD SUCCESSFUL**；`:desktop:dist` → 55,981,997 B；zip **98.1 MB**；活客户端已更新（备份 `app/Unciv.jar.bak-20260912-194139`）；大脑按「先杀干净再起一个」重启，现为**单进程**独占 `127.0.0.1:2335`。
- **待做（已批准未做）**：**A2** 强度测试 CSV 记录（每回合每文明 分数/城市/科技/人口/军力）——正好直接服务上面那份 T0/T1/T2 测评；**B2** 的**决策**退避（本轮只做了 spawn 冷却）。

### 2026-09-12 · 训练场（arena/）重构：P0+P1+P2 全推（自动权重首次落地）

**起因**：用户问「这一系列脚本和架构有什么可优化的？装载到哪？哪里启动？哪里看自动权重后的排名？」
先核实——**arena 是真实代码**（不是只有 PLAN）：`map_generator/spawn_classify/run_batch/run_rotation/evaluate/cross_report` + `ArenaLauncher.kt`。

**结论（先答"自动权重在哪"）**：**不存在**。权重是手写常量，且同一公式抄了三份（run_rotation docstring / evaluate / cross_report），
已经漂移（cross_report 把灭国加成写成硬编码 `90/500`）。`evaluate.py` 自己的 docstring 写着"哪套权重能预测胜负由批量数据回答"——这一步从没做。

#### 修掉的三个真 bug（都是"结论会错"级别）

1. **索引翻倍**：`key_of` 曾用 `tag or gameId` 当主键 → 同一局既是"日志记录(tag键)"又是"磁盘记录(gameId键)"，54 局被记成 100 条。改为**永远以 gameId 为主键**，tag 降为元数据。
2. **征服加成算了两次**：cross_report 先 `rel + bonus`（真 median），再 `mean + 0.2*城 + 90/500*国`（硬编码 median）→ 加成翻倍且口径不一致。现在加成只加一次，median 取同回真实值。
3. **`%TEMP%` 全目录扫描**：修 O4 时一度默认扫 `%TEMP%/*.log`，那里有 **170 个无关日志** → 索引被污染、单测被真实数据干扰。改为**显式 opt-in**（`ARENA_LEGACY_LOGS`）+ 只认训练场命名。

#### P0（结论正确性）

- **O1 单一评分来源** `arena/scoring.py`：power 公式、征服/灭国加成、归一化、名次，全部一处。
- **O2 1v1 批次终于进评分链路**：旧 `batch-results.json` 只有 `pair/seconds/tail`、**没有 gameId**，cross_report 只认 rotation 的 roster → 这批从未被评分。现在 runner 每局写索引，`report.py --kind batch` 可出排名。
- **O3 覆盖率显式输出**：54 局中只有 29 局可评分（53.7%）；22 局"存档在、stats 被清"（无法复原，需重跑），2 局"跑一半被杀"。以前这些被静默跳过，读者会以为排名是全量。
- **O4 索引取代日志正则**：新日志写仓库内 `arena/logs/`（`%TEMP%` 会被清，实测 49 局只剩 12 个日志）；`gamelog.py --import-legacy "%TEMP%"` 已把那 **12 个历史日志抄进仓库**（胜负标签不再丢）。

#### P1（方法学）

- **O5 自动权重 `arena/fit_weights.py`**：标签用**引擎自己的 `engineScore`**（独立于本仓库 power 公式，避免自证循环），8 因子局内 z-score，L2 逻辑回归（numpy，无需 sklearn），**留一局交叉验证**。
  实测（29 局 / 121 样本）：**hand 55.2%（几乎等于猜）< equal 65.5% < fitted 76.9%**。产物 `arena/fitted-weights.json`，`report.py --weights fitted` 看排名。*这是"手写权重是否成立"的第一次真实检验，答案是：基本不成立。*
- **O6 归一化可选** `median`（1.0=局内中位）或 `z`（z-score），6 人局不再被中位数压平。
- **O7 多人局名次**：旧 evaluate 只在 `len(civs)==2` 时给"对比"，6 人轮换局拿不到名次；现在按 placement 汇总，1v1 与多人同一套。

#### P2（健壮性）

- **O8 `arena/paths.py`**：JDK/仓库/`%TEMP%`/客户端 maps 全部环境变量化；**`ARENA_CLIENT_MAPS` 默认空** —— 离线评测不再往正在运行的客户端 `maps/` 写文件。
- **O9 `arena/procs.py`**：`kill_stale` 改用 psutil（`wmic` 自 Win11 起弃用，回退保留）。
- **O10 `arena/README.md`**：装载位置、入口命令、数据契约、已知缺口（此前只能读 docstring 才知道怎么跑）。

#### 验证

- 新增 `tests/test_arena.py` **22 个用例**（锁定上面三个 bug 的回归）；**全量 pytest 126 passed**（原 104）。
- 真实 GBK 控制台下 `report.py` / `fit_weights.py` 均 **exit=0**：此前一个 `⚠` 符号就会 `UnicodeEncodeError` 打断整次评测（已加 `paths.make_stdout_safe()` 兜底 + 全 ASCII 标记）。
- 只动 Python/arena，**jar 与 zip 无需重打**（交付物 `Unicv-RC-1.0-win64-fixed.zip` 不受影响）。git 仅本地：`release/release` 领先 3，新文件未跟踪，**未 push**。

**顺手确认的现状数据**：索引 54 局（rotation 11 + batch 1 + 未知 42）；T0 手写口径下 MongolEmpire 居首（2.218，6 局）；拟合口径下 MongolEmpire 2.539、Late Tang 掉到末位。

### 2026-09-12 · 地图竖中线对称重写 + 按图分集（O4）

**问题**：用户新游戏开 `arena-64x64`，肉眼确认「根本不是中间竖线对称」。实测左陆 1334 / 右陆 482；根因是生成器用了 `worldWrap=true` 的圆柱镜像——镜像点可以仍落在同一半屏。另：`arena-64x64.json` 曾 **0 出生点**；rotation 出生点迁移按全局 x 间距互斥，把不同纬度的 L3 槽挤掉，埃塞俄比亚漂到非镜像位。

**修复**

1. `map_generator.py`：镜像改为纯 `(x,y)→(-x-y,y)`；**`worldWrap=false`**；只在真正跨 `screenX=0` 的成对格子写内容；出生点按行配对，右席 = 左席精确镜像；`render()` 内 `verify_symmetry()` 失败即拒写。
2. 重导 `arena-64x64.json`（盛唐/蒙古，2 出生点）与 `arena-64x64-rotation.json`（6 槽全成对）；已同步到 `Unciv-Source/.../maps/` 与客户端 `Unciv/maps/`。
3. `tests/test_arena.py` 增 3 个对称回归用例；`arena/README.md` 增加「地图套件（按图分集）」；任务板 O4 ✅。

**后果**：旧索引里 wrap 图上的 T0 排名与新图**不可比**，要重跑才有基准。

### 2026-09-12 · 竖中线对称图 T0 重跑开局（Emperor / 150 / 16 国）

- **归档**：旧 wrap 图结论 → `arena/archive/t0-wrap-legacy/conclusions.json`（含 16 国最终榜与分层）；索引/日志/`arena-*` SaveFiles 已清；`t0-*-ranking.json` 与 `slots`/`spawn-report` 保留。
- **先验权重**：`report.py --prior-weight 0.1`（默认读上述 conclusions），旧结论仅作 0.1 局等效，不与新图当真样本混表。
- **本轮**：`arena-64x64-rotation.json`（竖中线对称）、tag `sym-r*g*`、Emperor、150 回合、每图固定 6 国（16 不整除时环形补席）、2 轮共 6 局、全员上场。
- **监控**：runner 打印出生镜像检查；超时/`Exception`/`turn 1` 缺失会打 `[!]`。

### 2026-09-12 · 激活门：未相遇前静默大脑（第十八次，用户定纲）

**规则**：大脑与数据传输只在「Agent 席位遇见人类玩家」后激活；AI-AI 相遇不触发；纯 AI 局（训练场无人类）全程静默。例外：`--brain` 显式旁路（T1/T2 方法论测试）。

**实现**：`AgentTurnHook.applyIntents` 入口加激活门（`AgentConfig.forceBrainWithoutHuman=false` 默认）——未激活时跳过切片/HTTP/采样前的一切大脑交互，每回合为每个未相遇席位省下秒级 LLM 往返。ArenaLauncher 加 `--brain` 参数。jar 已换入客户端。

**测试**：新增两用例（未相遇静默→相遇激活；纯 AI 局静默+开关旁路）；修一个测试间全局状态泄漏（forceBrainWithoutHuman 未在 tearDown 复位导致用例互相污染）。civagent 包全绿。

### 2026-09-12 · 大脑总开关（Options → Agent，第十九次）

- `AgentSecrets.brainEnabled`（默认 true，持久化 `~/.civagent/secrets.json`）；`applyToRuntime` 同步到 `AgentConfig.enabled`。
- **Options → Agent → Brain 复选框**：关闭 = 全部席位回退原生 AI，网关/大脑拉起/数据传输全停（`AgentMod.ensureStarted` 总开关短路）；开启即恢复。实时生效、随 Save 持久化、重启保持。
- 与激活门的关系：激活门管「遇见人类前静默」，总开关管「彻底关脑」——两者叠加：总开关关 = 一切静默；总开关开 + 未相遇 = 静默；总开关开 + 已相遇 = 激活。
- 附：修一处本回合引入的 ensureStarted 括号缺失（AgentTurnHook 结构破坏导致全仓符号失效）。civagent 测试全绿，jar 已换入。

### 2026-09-13 · 玩家实测局分析 + 存档轮换数据抢救（第二十次）

- **玩家实测（北宋 201 回合，皇帝，纯离线无大脑）**终局：盛唐 AI 41 城/771 人/77 科技/78 军（含玩家 6 城——**AI 在终局前征服了玩家**）＞西班牙 22 城/80 军＞北宋 10 城/136 人/65 科技/20 政策（玩家种田流：科技政策双第一，灭了桑海）。瑞典被盛唐 AI 消灭。
- 与 T0 排名交叉验证：盛唐「41 城断层第一」与 t0-v2 排名的扩张型画像一致 ✓。
- **数据抢救**：游戏自动轮换删除了旧自动存档（只留 ~10 个），北宋局原始存档丢失；终局关键数字（城/人口/科技/政策/军单位数/征服归因）已在删除前提取并存 `arena/game-northern-song-standings.json`（军力明细未能提取）。
- **大脑进程健康**：单例锁生效（仅 1 实例，waitress 空转 0 CPU）。
- **新局检测**：玩家新开晚唐局（149-157 回合自动存档）。

### 2026-09-13 · 标准测试配置定纲 + 晚唐随机图实战数据（第二十一次）

**标准测试配置（用户定纲，此后测试统一）**：6 国一局、固定出生槽位轮换（12+ 国轮换）、蛮族开、全部胜利类型、允许核弹、**皇帝难度**、6 城邦、快速速度、远古开局、模组 = 原版众神规则集 + 无悔华夏 + 宋朝 mod、**间谍开**。

**晚唐随机图实战（b8b781ea，158 回合，Emperor，无 CivAgent——原生 T0 数据点）**：
- **晚唐被朝鲜灭国**：长安/太原/成都易主，朝鲜吞并后 19 城跃居第三。
- 阿拉伯为该局霸主（国力 31136、军力 2826）；玛雅 19118 第二。
- 产出：`arena/game-latetang-158-standings.json`。

**晚唐强度结论汇总（三数据点）**：
1. 竞技场固定好位（修复后）：21 城/265 人，势比 3.5 全榜第二 —— **好位置时很强**；
2. 竞技场坏位（地图 bug 局）：0 城被灭（已修复为生成器教训）；
3. 随机图全配置：**再次被灭**（朝鲜）。
→ **结论：晚唐强度方差极大，高度依赖出生位置与邻国**——这正是「出生点位评估」必要性的最直接证据，也是给晚唐席大脑的核心假设（早扩张/早建军才能自救）。

### 2026-09-13 · 补场完成：22 国强度总表（第二十一次续）

**补场双局**（mk1：俄/美/法/阿拉伯/朝鲜/西班牙；mk2：玛雅/匈人/印加/阿拉伯/俄/法）：
- 首秀表现（相对势指数）：**西班牙 2.56**（1.76 相对 + 占 4 城，首秀最强）、匈人 1.63、俄 1.22、朝鲜 1.12、印加 1.03、美 1.02、法 0.97、玛雅 0.74、**阿拉伯 0.53（两局垫底）**。
- **阿拉伯双峰第二例**：用户随机图霸主（31136）→ 竞技场两局垫底（0.53）——与瑞典同款的「环境依赖型」。

**22 国总表**（`arena/nation-strength-current.json`，混战分+补场分+1v1 综合）：
S 级：蒙古帝国（综合 6.37）· 盛唐 2.02 · 埃塞 1.67 · 桑海 1.48 · 秦 1.45
A 级：罗马 1.30（参照）· 晚唐 1.28（1v1 好位 3.5）· 西班牙 1.28 · 波斯 1.25 · 希腊 1.12 · 埃及 1.11
B 级：俄 1.10 · 法 1.02 · 美 0.99 · 印度 0.99 · 瑞典 0.99（1v1 弱混战强）· 巴比伦 0.95
新观察：匈人 0.81 · 朝鲜 0.56 · 印加 0.52 · 玛雅 0.37 · 阿拉伯 0.27（竞技场口径）

### 2026-09-13/14 · 组合榜（nation-strength-current）可复现化 + duel150 量纲异常修正

**背景**：`nation-strength-current.json`（as_of 9-13，22 国，含 ffa/mk/duel150/combined）是上一会话的产物，
但**产出它的脚本不在仓库里**，是临时内联脚本 —— 即"最新排名"不可复现。

#### 反推出口径（22 行逐行核对命中）

    combined = mean(ffa, mk 中非空者) + 0.1 × min(duel150, cap)

关键点：ffa 与 mk **是取均值不是相加**（偏差最大的正是既有 ffa 又有 mk 的 Russia/France/America，
反推后逐行命中）。

#### 三个来源的现状

| 列 | 来源 | 可复现？ |
|---|---|---|
| ffa | `arena/index.jsonl` 里 20 局 `kind=rotation`（`sym-*`，本图，不含 report.py 的旧图先验） | ✅ 重算后**逐行完全一致**（3.7888/1.9033/…） |
| mk | `roster-mk1/mk2.json` 那两局（按**文明集合相等**匹配 stats，不信文件名） | ✅ 可重算，但见下 |
| duel150 | 1v1 快棋 150 回合势比 | ❌ **源数据已丢失** |

**duel150 是孤儿列**：`SaveFiles` 里 23 个 `arena-stats` **全是 6 文明局，没有一个 1v1**；
索引里无任何 duel 记录；归档里唯一的 batch 局 `78abe323` 连 stats 都没有。
→ 只能冻结在 `arena/legacy/duel150-legacy.json` 供审计，不可能重算。

#### 发现的两个旧口径问题

1. **duel150 是重尾比值且被直接线性计入**：MongolEmpire **25.86**，其余 11 国 0.33~3.5。
   旧口径 `0.1×25.86` 把榜首抬高 **+2.59**（3.79 → 6.37，+68%）。现按 `--duel-cap`（默认 3.0）
   截断后才计入；输出新增 `duel150Raw`（原始值）、`duelInflation`（duel 贡献）、`combinedNoDuel`（完全不计 duel）。
2. **旧管线自己两套归一化口径**：ffa 用 50/100/150 三点均值，mk 却**只看末回合**。
   实测 `Spain T150-only rel=1.759`（旧文件记 1.76）vs 三点均值 1.515 —— 证据确凿。
   现统一为三点均值，`--mk-final-only` 可复现旧口径。
3. **旧口径把"未参加 ffa"写成 0**，于是 `mean(0, mk)` 把 6 个新国家的 mk **除以 2**：
   Spain 2.56→1.28、The Huns 1.63→0.81、Korea 1.12→0.56、Inca 1.03→0.52、The Maya 0.74→0.37、Arabia 0.53→0.27。
   "没参加"≠"拿了 0 分"，现在按缺失处理（`--legacy-zero` 可复现旧口径）。

#### 交付

- `arena/nation_strength.py`：ffa/mk 重算 + duel 冻结读取 + combined，公式写进 docstring；
  `--compare` 逐行对比、`--duel-cap`、`--mk-final-only`、`--legacy-zero`、`--out`；写主文件前**自动备份**。
- `arena/legacy/duel150-legacy.json`：冻结的 duel150 + 为什么冻结的说明。
- `arena/archive/nation-strength-legacy-repro.json`：**旧口径完整复现证明**（唯一差异 = duel 截断）。
- 旧文件备份：`arena/archive/nation-strength-current-20260913-2338.json`。

#### 验证

- `--mk-final-only --legacy-zero --compare`：全 22 行复现旧值，**唯一差异是 MongolEmpire 6.37→4.09
  与 Late Tang 1.28→1.23**（正是 duel 被截断的两国）。
- 修正后榜位移：**MongolEmpire 4.089（-2.28，仍是第一）、Spain 2.315（+1.03，升到第二）、
  The Huns 1.339（+0.53）、Late Tang 1.233（-0.05）**，其余 18 国不变。
- 新增 9 个用例（`tests/test_arena.py`，含公式/截断/缺失语义/文明集合匹配）；**全量 pytest 135 passed**。

### 2026-09-14 · 采用用户的测试图 + 每局 152 回合 + 读存档入榜

**用户定调**：以后测试用 `Unciv-SongDynasty/maps/测试arena-64x64-rotation.json`（他手工补全资源的图）；
每局跑到 **152 回合**就换下一局；并把"读存档"纳入排名。

#### 1. 让 arena 用上这张图（`map_fix.py`）

用户那张图是 **radius=30 六边形 + 左右镜像**，**没有 startingLocations**（客户端手摆的），
而轮换赛靠固定 6 槽位保证公平；`map_generator.py` 会连地形资源一起重造（且它的资源清单硬编码 26 种，
**摆不出模组资源**）。所以新增 `arena/map_fix.py`：**只注入起点**，其余一字不动。

- 起点：`arena/slots-测试arena.json`（3 对镜像位，互距 ≥10，内陆）
- 镜像规则：`(x,y) -> (-x-y, y)`（来自 `run_rotation._check_spawn`，不是 `-1-x`）
- ⚠ **该图镜像命中率只有 41.9%**（用户手雕过），所以槽位公平性是**近似**的；已记录在册
- 注入后：`Unciv-Source/android/assets/maps/测试arena.json`，格 2791、资源 **36 种/577 处**、
  含模组 **Bronze/Jade/Tea/醴**，`mapParameters.mods` 对齐为实际启用的模组

`run_rotation.py` 新增 `--base-map / --map-name / --slots / --minutes`。

#### 2. ★ 找到并修掉"JVM 打完不退"（第一轮 20 分钟只出了 1 局的原因）

`ArenaLauncher` 打完局后 **java 进程不退出**（当前 jar 是另一个会话新构建的，含 `--stats-interval`
与 "brain gated"：`brain gated: activates only after meeting the human`），`subprocess.run` 于是
死等到 **30 分钟超时**（`max(120, 150*12)`）——第一轮 20 分钟就这样只跑完 1 局。

修法（runner 侧，无需重编 jar）：**轮询日志，看到 `[arena] stats: ` / `finished at turn` 就主动收工**
（`proc.poll()` + `procs.kill_pid`）。修后单局 **35–51 秒（中位 45）**，20 分钟可跑十几局。

> 副作用教训：`psutil` 按 **cmdline 匹配**杀"runner"时，会把正在执行该命令的 **bash 自己也杀掉**
> （bash 的 cmdline 里含 `run_rotation.py`）→ 一律按**进程名**过滤。

#### 3. 读存档（`savedata.py`）—— 客户端手打局第一次能进排名

存档结构实测（4.21.4-patch1）与三个坑：
1. **`civ.stats` / `civ.units` 都不存在** → arena 那套 `goldPerTurn/sciencePerTurn` 拿不到；
   **单位挂在格子上**：`tileList[].militaryUnit{owner,name}` → 按 `owner` 归属，军力用
   `units-strength.json` 按单位名反查强度（这就是用户那份 standings 写"去军力项"的原因）。
2. **`isCityState` 在存档里恒为 None**；`cityStatePersonality` 只有部分局的城邦才有（17/24）。
   可靠判据：**`statsHistory` / `espionageManager`（城邦 0/24，主要文明 32/32）**。
   名字不可靠 —— 这几局里 **Venice 就是当城邦用的**。
3. `statsHistory` 是**逐回合序列** `"S123N1C2P25G100T3F9H5W1A0"`，`S`=引擎分数（已用）；
   其余项语义**未经证实**（实测 Siam C305 而实际只有 18 城）→ 不猜，原样保留在 `statsHistoryRaw`。
   另有 `victoryData{winningCiv,victoryType,victoryTurn}` = **现成的胜负标签**（自动权重可用）。

产物：`arena/save_stats/{gameId8}.jsonl`（**与 arena-stats 同 schema，只含主要文明**）→
`report.py`/`scoring.py` 直接可用；`--write-index` 写 `kind=save` 索引；`report.py --kind save` 出榜。

#### 4. 顺手修的两个口径问题

- `report.py` 的**旧图先验**（`archive/t0-wrap-legacy/conclusions.json`，weight 0.1）原本**无条件生效**，
  在 `--kind save` 榜里凭空造出 **15 个 games=0 的国家** → 现在只在 `kind ∈ {all, rotation}` 生效。
- `nation_strength.py` 新增 `--kinds rotation,save`，可选把存档局并进组合榜。

#### 验证

- 轮换赛（测试图 + 152 回合）：**12 局已入索引，16 国全覆盖**，单局 35–51s。
- 存档榜：6 个客户端存档跑通，主要文明正确、城邦/野蛮人/旁观者被剔除。
- 新增 15 个用例（`map_fix` 注入/拒绝越界、`savedata` 判据/单位归属/剔城邦/排名、`nation_strength --kinds`）
  → arena **37 passed**、全量 **141 passed**。

**注意（可比性）**：此前那 20 局 sym + 2 局 mk 跑在**旧图**（23 种资源/138 处、0 模组资源），
与现在这张 **36 种/577 处**的测试图**不是同一实验条件**，两批分数不可直接比。

### 2026-09-14 · roster 扩到"全部国家"：38 主要文明 + 北宋（需要 Song Dynasty 模组）

**用户要求**：把 roster 扩到 32 国（让那些只有 1–2 局的国家进入可信档）。

#### 核对国家名（不照榜单猜名，去规则集查）

基础 `jsons/Civ V - Gods & Kings/Nations.json` 83 条 + 模组 `jsons/Nations.json` 4 条 =
**87 个文明：40 主要 / 47 城邦**。去重后**主要文明实为 38 个**（另有 Barbarians/Spectator 不算）。
比我候选的 32 国多出 7 个：**Aztecs、Carthage、Celts、Germany、Japan、The Netherlands、The Ottomans**。

**★ `Northern Song`（北宋）不在 arena 加载的规则集里** —— 它来自**第三个模组 `Song Dynasty`**
（用户客户端启用的是 `5Hex Tileset / Song Dynasty / unciv unrepentant cathay`，而 arena 只加载了 cathay），
而且 `Song Dynasty` **当时根本不在 arena 的 `android/assets/mods/` 里**。已拷入该目录。

#### 扩 roster

- `arena/nations-full.json`：**39 国** = 38 主要文明 + 北宋
- `run_rotation.py` 新增 `--nations-file`（数组），轮换自动按 `ceil(N/6)` 拆分：
  39 国 → **7 局/轮**（42 席，3 国每轮多打一席），每轮覆盖全部 39 国
- 运行需 `ARENA_MODS="unciv unrepentant cathay,Song Dynasty"`（否则北宋解析失败）

#### 预检（先验证名字再开长跑）

`sym-r90g0`（gameId `7197a067`，6 国含北宋）：**全部名字被引擎接受**，跑满 152 回合，
`[arena] native=[Northern Song, The Ottomans, Japan, Spain, Korea, Aztecs]`，**59 秒/局**。

#### 正式开跑

`sym-r100` 起，39 国 / 测试图 / 152 回合 / 20 分钟预算 / Emperor / stats-interval 24。
计划 420 局（7 局×60 轮），预算内约 3 轮 ≈ 21 局 → 每国 ~3 局。
**要每国 ≥8 局（A 档）需约 8 轮 ≈ 55 分钟**。

#### 可比性（必须记住）

- **19 局早期测试图局只加载了 cathay**；新批次是 **cathay + Song Dynasty** → 规则集不同（多出的国家/资源会改变基准），
  两批不宜不加区分地合并。
- 旧的 20 局 `sym-r0..r6` 跑在**旧图**（23 种资源/138 处、0 模组资源）。
  → 现在索引里 `kind=rotation` 混了**三种条件**（旧图-cathay / 测试图-cathay / 测试图-cathay+SongDynasty），
  出榜时按 `map` 与模组集拆分才严谨。

#### 顺手修掉的一个自杀 bug

`procs.kill_stale` 只按 cmdline 匹配 → **把正在执行该命令的 bash 自己也杀了**（连中两次，exit 15）。
现在 `list_arena_processes` 同时限定**进程名以 java 开头**。另新增 `kill_pid`。

#### 验证

- 单局预检通过（见上）；`nations-full.json` 39 国全部在规则集内且非城邦。
- 全量 **pytest 141 passed**（arena 37）。

#### 键名澄清（用户问「ffa 是萤火虫算法？」）

`nation-strength-current.json` 里的 **`ffa` = Free-For-All（6 人混战局）**，**不是萤火虫算法**（Firefly Algorithm）。
全仓库 grep `firefly`/`萤火虫` **0 命中**；这条流水线里没有任何元启发式，打分是确定性公式：

    combined = mean(混战, mk 非空者) + 0.1 × min(duel150, cap)

按用户要求已把**输出键改名 `ffa → 混战`**（函数 `ffa_scores → melee_scores`），
`compare_with_old` 保留**读旧文件 `ffa` 键**的兼容（2026-09-13 及更早的归档字段名不动）。
唯一带"拟合"的是 `fit_weights.py` 的 L2 逻辑回归 + 留一局交叉验证。

### 2026-09-14 · 权重调整：奇观 30→20、军力 10→15、科技 4→14

**用户指定**（人口 8 / 城市 8 / 文化·信仰·引擎分 0.5 不变）。

改在**唯一来源** `arena/scoring.py:HAND_WEIGHTS` —— `report.py`/`evaluate.py`/`cross_report.py`/
`nation_strength.py` 全部经 `scoring.power()` 取权重，所以只动一处即全链生效。

#### 连带处理

- **旧权重存档**：`arena/weights-legacy-hand.json`。历史榜单全是旧权重算的，
  想在 `--compare` 里复现旧值需 `--weights arena/weights-legacy-hand.json`（report/nation_strength 都支持）。
- **`ELIMINATION_BONUS` 保持 90 不动**：它的原注释写"每灭一国 = 3 奇观"，而奇观权重降到 20 后
  3 奇观只有 60 —— 90 现在等于 4.5 奇观。**只改注释、未改数值**（改灭国加分是另一个决定）。
- **测试**：`test_power_matches_hand_formula` 改为从 `HAND_WEIGHTS` 推导（改权重时自动跟随）；
  新增 `test_hand_weights_are_the_agreed_values` 把商定值**钉住**，以后改权重必须显式改测试。
- 文档串同步：`scoring.py` 的 power 公式行、`fit_weights.py` 的对比说明。

#### 权重效果（同一批局，只换权重）

39 国 combined 变化都在 **±0.06 以内，名次最多挪 2 位** —— 因为两个方向互相抵消：
科技权重 ×3.5 抬高科技流国家，奇观权重 ×0.67 压低奇观流国家。

| 变动 | 国家 |
|---|---|
| 名次退 2 | The Maya（26→28）、Rome（11→13） |
| 名次进 2 | The Huns（14→12） |
| 其余 | 挪 ±1 或不动 |

榜首仍 MongolianEmpire（4.701→**4.753**），榜尾仍 Celts（0.169→0.188）。

#### 验证与产物

- 全量 **pytest 142 passed**（arena 38）。
- 落盘 `arena/nation-strength-current.json`（旧版备份 `archive/nation-strength-current-20260914-1749.json`）
  + `arena/ranking-total.md`（39 国表格版，含权重说明）。

### 2026-09-14 · 灭国加成 90 → 60（恢复"灭国 = 3 奇观"语义）

奇观权重从 30 降到 20 后，`ELIMINATION_BONUS=90` 已不等于 3 奇观（=4.5）。用户决定**保持原语义**，
故同步改为 **60 = 3 × 20**。常量在 `scoring.ELIMINATION_BONUS`，测试改为**跟随常量**
（`test_relative_bonus_*` 不再硬编码 90）。

#### 影响：极小，名次不动

同一批 55 局只切这个常量：39 国 combined 全部微降 **≤0.013**（最大 Northern Song 2.097→2.084），
**榜首榜尾与名次顺序不变**。

**为什么连"没灭国"的国家也变了？** 因为 arena 的灭国口径是**简化归因**：
`ArenaLauncher.dumpStats` 把"任何其他主要文明已灭亡"都计入在场者（v1 简化，不追责最后一击）。
实测 55 局里 **26 局**存在灭国（152 回合的轮换局常有一两个小国被灭；6 个客户端存档局有 3–4 个），
所以几乎每个国家都在某几局拿到过 +1/+2 灭国 → 全体随常量一起微降。

#### 验证与产物

- 全量 **pytest 142 passed**（arena 38）。
- 落盘 `arena/nation-strength-current.json`（备份 `archive/nation-strength-current-20260914-1756.json`）
  + `arena/ranking-total.md`（表头已注明征服加成 = 占城 +0.2 势比/城、灭国 +60）。

### 2026-09-14 · 征服规则重做：净占领阶梯（替代"每城线性加分"）

**用户重定**（并追问了"拉锯城市怎么算"）：

    net = 占领 − 被占领            # 占领=手里创始文明非我的城；被占领=我创立但已丢的城
    net < 5     且存活 → +1 奇观 ；未存活 → 0
    5 ≤ net ≤ 10 且存活 → +2 奇观 ；未存活 → 0
    net > 10    且存活 → +4 奇观 ；未存活 → +3 奇观
    灭国 = 1 奇观（20 分）        # 原 3 奇观/60 分 → 降为 1
    **阶梯替代**原来的"占城 +0.2 势比/座"，不是叠加

#### 拉锯城市怎么算：按【最终归属】，不按易手次数

Unciv 每座城记 `foundingCiv`（创始文明，**永不改变**），且 `citiesCreated` 只在建国时计一次 →
`A→B→A` 的城最终归 A（A/B 各计 0），`A→B` 不还则 B +1、A +1：**一座城只计一次**。
另两个方案为何不用：① 按易手**次数** —— 需要事件级日志，而 arena 是**采样快照**（新一轮每 24 回合），
事件根本没记录，且拉锯越多分越高（正是用户担心的扭曲）；② 取**峰值** —— 抢 8 丢 6 会按 8 计，系统性高估。

**数据可行性已验证**：`citiesCreated` / `foundingCiv` / `isDefeated` 都在**终局存档**里
（`arena-stats` 没有 `citiesCreated`）；实测算出的占领数/城数与 `arena-stats` 末行**逐项吻合**；
本批 **55/55 局都能拿到终局存档**（`conquestSource=save`），所以全部是真净值，无退回近似。
拿不到存档的局会退回"只算占领、丢失按 0"并标 `stats-only`（高估）。

存活口径（用户定）：`isDefeated` 为假且仍有城。

#### 两件必须知道的影响

1. **征服的价值被大幅调低**：旧口径 `0.2 势比/座` 意味着每占一城 ≈ **+0.2 × 局内中位国力**
   （中位几千里 → 一城值上千分）；新阶梯**最多 +4 奇观 = 80 国力**，差 1~2 个数量级。
   后果：榜首 MongolEmpire **4.747 → 3.207**，全表向 base 压缩（`混战` 列的相对势占比变大）。
2. **阶梯没有惩罚档**：净<5 且存活 → +1（**丢 11 座城也是 +1，与净 0 相同**），未存活 → 0。
   即**丢城不扣分，征服只加不减**。若要让"丢城"有代价，需要另加负档（用户原始描述里提过"扣分"，
   但在澄清时选择了 0）。

#### 验证与产物

- 新增/改写用例：阶梯边界（4/5/10/11 × 存活/未存活）、`median` 不写死、`summarize_civ` 用终局指标、
  一次只加一次（不重复）、存活档 +1 的语义 → **arena 40 / 全量 144 passed**。
- 顺手修掉测试里硬编码 `power=16` 的脆弱点（`row()` 默认含 `techs=3`，科技权重涨到 14 后单国 power 实为 58）。
- 落盘 `arena/nation-strength-current.json`（备份 `archive/nation-strength-current-20260914-1757.json`）
  + `arena/ranking-total.md`（表头写清阶梯定义）。

### 2026-09-14 · 征服规则三定 + 权重按"目标占比"反解（第三版）+ 信仰型国家加权

#### 1. 征服规则三定（用户定，**替代**上一版净占领阶梯）

    每占领一座城        +3 奇观
    丢城 > 10 座        −3 奇观（不设存活条件）
    灭国                +1 奇观
    计数口径           仍是【最终归属】（foundingCiv + citiesCreated），拉锯城只计一次

**量级实测（用户问"一个奇观占比大吗"）**：55 局 / 330 个文明-局，单文明 power 中位 **9,076**，
局内 median 中位 **11,436**；**1 个奇观 = 20 分 = 中位国力的 0.175%（势比 0.0017）**。
因子占比：**军力 80.2%** · 人口 8.4% · 科技 3.9% · 文化 3.0% · 引擎 2.2% · 信仰 1.2% · 城市 0.6% · 奇观 0.5%。
→ 故"每城 +3 奇观"= 0.0052 势比/城，**只有旧规则（0.2 势比/城）的 2.6%**；这也解释了为何
之前调权重（奇观 30→20、科技 4→14）排名几乎不动 —— 非军力项加起来才 8.6%。

#### 2. 权重第三版：按**目标占比反解**（不是拍脑袋）

方法：`w_f ∝ 目标占比_f / 实测原始量_f`（原始量 = 上述 330 个样本的因子总量），奇观权重锚定 20
以保持与历史量级可比。实测原始量：cultureStored 319,467 · militaryMight 284,907 · engineScore 229,422 ·
faith 125,799 · population 55,792 · techs 14,982 · cities 3,942 · wonders 1,407。

目标 → 权重（已验证占比逐项命中）：

| 因子 | 目标占比 | 新权重 | 实测占比 |
|---|---|---|---|
| 军力 militaryMight | 60% | 1.1852 | **59.4%** |
| 人口 population | 12% | 1.2105 | 11.9% |
| 科技 techs | 12% | 4.5078 | 11.9% |
| 文化 cultureStored | 6% | 0.1057 | 5.9% |
| 奇观 wonders | 5% | 20.0 | 5.0% |
| 信仰 faith | 3% | 0.1342 | 3.9%（含信仰型 ×2） |
| 引擎 engineScore | 1% | 0.0245 | 1.0% |
| 城市 cities | 1% | 1.4277 | 1.0% |

上一版（军力 15/奇观 20/科技 14/文化 .5/信仰 .5/引擎 .5/人口 8/城市 8，实测军力 80.2%）
存为 `arena/weights-legacy-mil15.json`；原始版存 `weights-legacy-hand.json`。

#### 3. 信仰型国家（用户：信仰型国家的信仰权重要更大）

判定（自动，可编辑 `arena/faith-nations.json`）：国家 uniques 或 **uniqueTo 特色建筑/改良/单位**
里出现信仰产出（`[+N Faith]` / `Faith from` / `+N% Faith`）或 `faith` 正数值，且排除
"Only available when religion is enabled" 这类**条件句**。实测认出 **7 国**：
**Celts / Egypt / Ethiopia / MongolEmpire / Qindynasty / Songhai / The Maya**。

两个坑：① 只扫 `Nation.uniques` 会漏掉 **Egypt/Ethiopia/Songhai**（它们的信仰写在特色建筑的 `faith` 字段里）；
② The Maya 的 "Only available <when religion is enabled>" 是条件句，**必须排除**否则误判。
实现：`scoring.power(entry, weights, civ=...)` + `power_weights_for(civ)`，信仰项 **×2**；
已接到 report/savedata/evaluate/nation_strength 所有按文明算国力的位置。

#### 4. 排名变化（军力 80.2% → 60.0%）

榜首仍 MongolEmpire（3.295→3.168）。名次变动大的：**The Maya 27→17**、**Iroquois 21→14**、
**Northern Song 7→5**、Egypt 13→11、**The Huns 14→21**、**Carthage 19→25**、Late Tang 3→4。
（升的多是科技/文化/信仰型；降的多是纯军力型。）

#### 5. 验证

全量 **pytest 144 passed**；新增用例：权重钉值（第三版）、信仰型只改信仰项、`power(civ=)` 生效、
征服三定量级（含"丢 10 不扣、丢 11 扣"边界）等。
产物：`arena/nation-strength-current.json`（备份 `archive/nation-strength-current-20260914-1828.json`）、
`arena/ranking-total.md`、`faith-nations.json`、`weights-legacy-mil15.json`。

### 2026-09-14 · engineScore 提权（1% → 10%）+ 源码核查"引擎分数怎么来的"+ 军力不计派系修正（刻意选择）

#### 1. 源码核查：engineScore 是 Unciv 自己算的（已逐行确认）

`ArenaLauncher.kt:136` → `civ.calculateTotalScore()`（`Civilization.kt:936`）
→ `calculateScoreBreakdown().values.sum()`，分项：

| 分项 | 公式 |
|---|---|
| Cities | 城市数 × 10 × 地图尺寸修正 |
| Population | Σ人口 × `modConstants.scoreFromPopulation` × 修正 |
| Tiles | 城市拥有的**非水域格** × 1 × 修正 |
| Wonders | `modConstants.scoreFromWonders` × 已建奇观数 |
| Technologies | 已研究科技 × **4** |
| Future Tech | 重复科技 × **10** |

`mapSizeModifier = 1276 / 地图格数`（>1 时按 `(x-1)/3+1` 压缩）→ **不同大小地图的分数不可直接比**。
**分数里没有金币加成、也没有派系修正。**

**顺带纠正一个误读**：上一轮我看到的 `sqrt(gold)`（封顶 2×）+ 派系修正属于 **`calculateMilitaryMight()`**，
不是分数。也就是说引擎的 `Force`（存档 statsHistory 的 `F` token）= 单位战力评估（海军减半）
× √金币加成 × (1+派系修正)。

**`S` token 假设已证死**：`RankingType.Score → getStatForRanking → calculateTotalScore()`，
所以 `savedata.py` 把 `S` 当 engineScore **正确**；但 `getStatForRanking` 开头 `if (isDefeated()) 0`
→ **被灭国文明的 S 记 0**（与游戏内排行一致）。完整 token 表（S/N/C/P/G/T/F/H/W/A）
已核实并写进 `savedata.py` 文档，取代原来的"语义未经证实"（旧猜错两处：`C` 是食物不是城市、`W` 是科技不是奇观）。

#### 2. ★ 军力项**不计派系平衡修正**（用户明确要求，刻意选择）

power 的军力项继续用 **ArenaLauncher 的简化定义** `Σ max(基础强度, 远程强度)`，
**不换成**引擎的 `getMilitaryMight()`。理由：派系平衡修正会随**对手阵容**变动，
把"同一个国家在不同局里的军力"搅成不可比 → 污染国家间排名。
（副作用要记住：arena 局的 stats 里只有这个简化值，存档局才有引擎 Force，
两者口径本就不同，所以**统一用简化值**反而是自洽的选择。）

#### 3. 第四版权重：引擎分提权

比例反解，军力保持 60%，引擎分 1% → **10%**，其余按比例收缩：

| 因子 | 目标 | 新权重 | 实测占比 |
|---|---|---|---|
| 军力 militaryMight | 60% | 1.4815 | 59.2% |
| **引擎分 engineScore** | **10%** | **0.3066** | **9.9%** |
| 科技 techs | 8% | 3.7565 | 7.9% |
| 人口 population | 8% | 1.0087 | 7.9% |
| 信仰 faith | 4% | 0.2237 | 5.2%（含信仰型 ×2） |
| 文化 cultureStored | 5% | 0.1101 | 4.9% |
| 奇观 wonders | 4% | 20.0 | 3.9% |
| 城市 cities | 1% | 1.7846 | 1.0% |

上一版（引擎 1%）存为 `arena/weights-legacy-engine1.json`。

#### 4. 排名变化（引擎 1% → 10%）

变动都很小（≤0.054，因为引擎分本身就是城市/人口/科技/奇观的复合，**抬高它等于再给这些项加一次权**）：

| 国家 | 引擎1% → 引擎10% | 名次 |
|---|---|---|
| England | 1.170 → 1.117 | 19→20 |
| Denmark | 1.230 → 1.177 | 13→17 |
| **The Maya** | 1.196 → 1.223 | **17→13** |
| Ethiopia | 1.443 → 1.472 | 8→8 |
| Egypt | 1.332 → 1.349 | 11→10 |
| Sheng Tang | 1.350 → 1.327 | 10→11 |

榜首仍 MongolEmpire（3.168 → 3.137）。

#### 5. 验证与产物

全量 **pytest 146 passed**（权重钉值已更新到第四版）。
产物：`arena/nation-strength-current.json`（备份 `archive/nation-strength-current-20260914-1832.json`）、
`arena/ranking-total.md`（表头写明权重、引擎分 10%、军力不计派系修正、信仰 ×2、征服三定）、
`weights-legacy-engine1.json`。

### 2026-09-14 · 跨局合成口径拍板（用户定）+ 三口径对比留档

**用户问**："不是看每个局的系数判定吗，把所有局加起来是什么算法？"

**澄清**（算法是三步，第 1 步就是他说的"每局判定"）：
1. **逐局判定（局内自比，不跨局）**：该局每个文明的国力 ÷ **该局 median** → 相对势（中位 = 1.0）；
   检查点取均值；再加征服加成 → 每局每国 1 个分。归一化是必需的：绝对国力跨局不可比
   （地图/模组/回合/对手都不同）。
2. **逐国聚合 = 算术平均**（`statistics.fmean`），**不是求和** —— 求和会让打得多的国家占便宜
   （Rome 19 局 vs Germany 1 局）。一局 = 一次带噪声观测，多局取均值降噪。
3. **来源合并**：`combined = 平均(混战, mk) + 0.1 × min(duel, cap)`。

**用户拍板**：口径 = **算术平均**，单局极端值 = **不截尾**（保留真实幅度）。

**已知代价（已告知并被接受）**：算术平均对单局极端值敏感 —— MongolEmpire 单局 0.53~10.36，
两个 10+ 的雪崩局几乎决定其均值 2.837。

**留档**：`arena/ranking-metrics.md` 给出**均分口径 vs 均名次口径**的全量对照 + 名次差，
并标注两者排序不一致的实例（The Ottomans 均名次并列第 1 但夺冠率 0%；Japan 均分第 2 但均名次第 6）。

### 2026-09-14 · 三轴测试口径（只改测试 + 计划里的测试附录）

主榜改为均名次后，单测从「势+征服揉成 score」切到三轴不变式，避免下次再把征服或 duel 叠回去。

**`tests/test_arena.py` 改了什么**

- 征服：`score`/`powerRel` 不再含奇观加成（`bonus==0`）；`conquestWonders` 仍作诊断列。
- 名次：灭国垫底；主榜按 `meanPlacement`；一场雪崩抬高势不能压过更稳的均名次。
- 检查点：势只用 100/150，t50 高峰不进名次。
- 先验 / duel：默认 `priorWeight=0`、默认 `combine(..., include_duel=False)`；旧公式仅 `--include-duel`。
- 拟合：标签 = 存活且城数最多（不再用 `engineScore`）；特征维数 = `FIT_FACTORS`（去掉引擎分）。

计划侧只更新 `PLAN.md` 附录「三轴测试口径」表；本条不重跑对局、不刷新 `nation-strength-current.json`。

### 2026-09-15 · 三轴测试方法评估（变异测试实测）+ P0 修复 + 国家榜产物重生成

#### 1. 方法：不信任“用例都绿”，直接改坏实现看谁红

对 `tests/test_arena.py` 做了 13 次定向变异。首轮 **10 个变异 8 被杀**，两个存活——正是计划里写了但从未被验证的规则：

| 变异 | 首轮 | P0 后 |
|---|---|---|
| `powerRel` 重新叠征服加成 | KILLED | — |
| 主榜改按势排 | KILLED | — |
| 检查点加回 t50 | KILLED | — |
| 拟合特征放回 engineScore | KILLED | — |
| 灭国不垫底 | KILLED | — |
| 丢城阈值 10→5 | KILLED | — |
| duel 默认混进 combined | KILLED | — |
| melee 默认吃 save/batch | KILLED | — |
| **report 排序丢掉存活率次序键** | **SURVIVED** | **KILLED** |
| **combine 改成按 combined 排** | **SURVIVED** | **KILLED** |
| **checkpoint 标签错位回归** | （无用例） | **KILLED** |

#### 2. 顺手挖出一个真缺陷（不只是缺测试）

`report._game_scores` 用 `zip(checkpoints, bases)` 拼 `checkpoints` 字典，而 `bases` 在"文明缺某检查点"时会少一项→整体前移。实测：只在 t150 出现的文明输出 `{'100': 1.5642}`，值是 t150 的。改为 `samples: list[(turn, value)]` 成对保存。

#### 3. P0 补测（3 条）+ 产物生成器

- `test_report_secondary_sort_key_is_survival_then_power`：夹具刻意让 `Y` 的势**更高**（1.0 vs 0.80），逼出"先比存活率"这一步，否则用例会被势键意外满足。
- `test_combined_sorts_by_mean_placement_before_combined`：Steady（均名次 1 / combined 1）必须排到 Blowout（均名次 4 / combined 5）之前。
- `test_game_scores_checkpoint_labels_stay_with_their_turns`：单样本时 `checkpoints['150'] == base`，旧实现会标成 `"100"`。
- `test_render_markdown_keeps_combine_order_and_flags_no_duel`：新生成器不得重排行序，且表头必须写 `includeDuel`。

#### 4. 国家榜产物：从手写改成可复现（用户拍板归档）

`arena/ranking-total.md` 旧内容仍是旧公式（`mean(混战,mk)+0.1×min(duel150,cap)`，MongolEmpire 2.8372+0.3=3.137），与锁定的 `include_duel=False` 相矛盾；全仓 grep 证实它**没有任何生成脚本**。处理：

- 归档 `arena/archive/ranking-total-20260914-duelblend.md`；
- `nation_strength.py` 加 `render_markdown()` + `--md <路径>`；
- 重跑 `python arena/nation_strength.py --write --md arena/ranking-total.md`：新榜 **均名次为主轴**、39 国、`duel150` 只作末列，逐行校验 `combined == base`（duelInflation 全 0）；旧 `nation-strength-current.json` 自动备份到 `archive/nation-strength-current-20260914-1842.json`。

#### 5. 验证

`tests/test_arena.py` **52 passed**；全量 **pytest 156 passed**（注：本条之前 EXECUTION 记的 144/146 已过时）。

#### 6. 未做（已知空洞，不隐瞒）

1. **地图分集**：`report.py` 无 map 概念；`melee_scores(where=None)` 默认把 rotation 的三种条件合算，与 PLAN「不同图不得混表」相矛盾（只在生成文档表头加了警告）。
2. **短局回退**：`last_turn < 100` 时回落 `[last_turn]`，t50 仍进名次，需拍板写入口径还是禁止。
3. 无属性测试（置换/缩放不变性、单调性）；无 `index.jsonl → 榜单` 端到端回归。

### 2026-09-15 · 训练场四问落地：解耦成包 + 跑满闸门 + 以“测试arena64”为准 + 20 分钟实战跑

#### 1. Q1「测试脚本解耦成单独模块了吗」→ 原来是半解耦，现在真成包

查证结论（原状）：`arena/` **没有** `__init__.py`，靠 11 个模块各自 `sys.path.insert` 互导；与大脑/游戏代码**无** import 关系（这点是解耦的），配置单点在 `paths.py`、进程单点在 `procs.py`；但 `run_rotation.py` 一个文件里混了「生成地图 + 跑对局 + 收尸 + 解析」。

本轮改动（用户选“顺手解耦”）：

- 新增 `arena/__init__.py`（分层表 + 导入约定）。
- 11 个模块去掉无条件 `sys.path.insert`，改成 `try: from . import x` / `except ImportError:` 回退 —— `python -m arena.report` 与 `python arena/report.py` **两种都通**。
- 新增 `arena/harness.py`（**单局执行层**）：`LaunchConfig` / `load_slots` / `generate_map` / `check_spawn` / `_launch` / `run_game`。`run_rotation.py` 瘦成**排赛编排层**（`plan_games()` + 墙钟预算 + `--mods`），不再碰进程细节，也不再用 `global MAP_NAME/SLOTS_FILE/BASE_MAP`。
- `tests/conftest.py` 把仓库根加进 `sys.path`；`tests/test_arena.py` 改 `from arena import gamelog, ...`（不再 `sys.path.append` + `importlib.import_module("fit_weights")` 拼名）。

#### 2. Q2「没达到 150 回合做完的不算」→ 用户选**宽松**口径

- 新增 `gamelog.is_complete()`：日志给出结果（`VICTORY` 或 `no victory (turn limit)`）才算跑完；被墙钟 timeout 切断 / 无 outcome 的**不算**；**提前胜利算跑完**（否则系统性惩罚早战型国家）。
- 默认接入三处：`report.report(require_complete=True)`、`nation_strength.melee_scores/build`、`fit_weights.build_dataset`；`--allow-partial` 回旧行为。`report` 输出新增 `requireComplete` / `droppedIncomplete` 字段。
- 实测：存量 86 局**全部** `complete=True`（都是 `no victory (turn limit)`），所以闸门对旧数据零影响，但以后被预算切断的半局会被自动挡住。

#### 3. Q3「以测试arena64为准 + 旧榜分数×0.1 加入回归分析」

- **地图分集落地**：`report.py` 新增 `--maps` / `maps=`；`fit_weights.py` 同样新增 `--maps`。PLAN「不同地图不得混表」之前只是口号（`report.py` 连 map 字段都不看），现在可执行。
- **标准图** `测试arena.json`：Large 66×43、`mirroring=Left-right`、2791 格、36 种资源、mods 含 Song Dynasty；手工源图已备份 `arena/archive/maps/测试arena-pristine-20260915.json`（map_fix 是原地注入，先存底再跑）。
- **顺手修一个 rebuild 隐患**：`classify_log` 原来不认 `sym-` 前缀，`--rebuild` 会把已有 49 个 `sym-r*` 局全标成 `unknown`。已补 `sym- → rotation`。
- **旧榜先验**：`report.load_prior` 从只认 `ranking` 扩成三种形状（report 的 `ranking` / nation_strength 的 `nations` / 旧列表）；快照 `arena/archive/prior-before-20260915.json`（39 国、55 局全图那一版）。
- **×0.1 进回归**：`scoring.PRIOR_FACTOR = "priorScore"`、`PRIOR_WEIGHT_DEFAULT = 0.1`、`FIT_FEATURES = FIT_FACTORS + (priorScore,)`。设计矩阵最后一列 = `0.1 × 旧榜分数`，**不做 z-score**（跨局常量，z-score 会抹成 0）；系数单独报告，**不进** `fitted-weights.json` 的 `weights`（不污染 `power()`）。
- **结论（诚实，且与直觉相反）**：先验列系数 **−0.2185**（负 = 旧榜“强者”与「末回合城数最多」**反向**），命中率 **70.3% → 70.3%（+0.0pp）**。即旧榜×0.1 对本批数据的预测**没有增量信息**，只作诊断保留。

#### 4. Q4a 崩溃与退出机制（`harness.py` 三层）

1. 完成标记（`finished at turn` / `[arena] stats: `）→ 再等 2s 让 final save 落盘 → **主动杀**（实测 JVM 打完局常常不退出）；
2. 墙钟超时 `max(120, max_turns×12)` 秒 → 强杀并标 `timeout`；
3. `finally` 兜底：正常返回 / 抛异常 / Ctrl-C 都保证该 PID 被收；批末再 `kill_stale()`。

另外 `run_game` 整体包 try/except：地图注入失败、java 起不来、日志无 `gameId` 都只**跳过本局**，不炸整批；`KeyboardInterrupt` → 清理后 `exit 130`。

#### 5. Q4b 20 分钟实战

    python arena/run_rotation.py --start-round 200 --rounds 12 --minutes 20 --max-turns 150 \
        --base-map ".../assets/maps/测试arena.json" --map-name 测试arena.json --slots slots-测试arena.json

- **结果：29 局，失败 0 局**（含冒烟 2 局共 31 条新索引）；全部 `complete=True`、全部 `map=测试arena.json`、outcome 全为 `no victory (turn limit)`；中位 **39s/局**，合计 1284s。
- 退出验证：跑完 `python arena/procs.py` **零残留** java 进程。日志存档 `arena/logs/_run20min-20260915.txt`。
- 索引：rotation 55 → **86** 局；`测试arena.json` 集 **60 局**（29 旧 + 31 新）。

#### 6. Q4c 排名（以测试arena64为准）

- `python arena/report.py --kind rotation --maps 测试arena.json` → `ranking.json`（39 国 / 60 局）。
- `python arena/nation_strength.py --write --md arena/ranking-total.md --maps 测试arena.json`（旧 json 备份 `archive/nation-strength-current-20260915-1614.json`）。榜首 **Inca**（均名次 1.00），榜尾 **Celts**（6.00）。
- 与上一版（55 局、两图混算）对比，名次变动大的：

  | 国家 | 旧→新 | 说明 |
  |---|---|---|
  | Ethiopia | 7 → 22 | **↓15**，最惨；旧榜靠另一张图的局撑着 |
  | Late Tang | 28 → 21 | ↑7 |
  | Persia | 14 → 7 | ↑7 |
  | Sweden | 22 → 16 / Babylon 30 → 24 | ↑6 |
  | MongolEmpire | 3 → 6 | ↓3，均名次 2.0 → 2.6 |

  变动主要来自两个口径变化：**只算 测试arena.json** + **只算跑完的局**。

#### 7. 验证

`tests/test_arena.py` **57 passed**；全量 **pytest 161 passed**。

#### 8. 未做（不隐瞑）

1. **mods 仍混淆**：`测试arena.json` 60 局里 **50 局 cathay-only + 10 局 cathay+Song Dynasty**（旧批落下的）；本轮新跑 31 局统一 cathay-only。要分成两个条件需再跑一轮或加 `--mods-filter` 默认值。
2. `run_batch.py` 仍保留自己的 `run_game`，未并入 `harness.py`。
3. 属性测试（置换/缩放不变性、单调性）与 `index.jsonl → 榜单` 端到端 golden 仍缺。
4. 旧榜×0.1 先验经实测**无增益**（+0.0pp），是否保留为默认列由用户定。

### 2026-09-15 · 两处口径核实（用户追问后直接查证，不再当问题抛回）

#### 1. 「旧榜分数×0.1」——链路默认是否已生效：之前**没生效**，现已落地

- `fit_weights` 侧本来就是默认开：`PRIOR_WEIGHT_DEFAULT = 0.1`、`PRIOR_FACTOR = priorScore`，`main()` 默认 `--prior-weight 0.1`。
- **但默认 prior 路径 `arena/prior-current.json` 当时不存在** → `prior = {}` → `prior_weight` 被降为 0.0，等于默认不启用。现已落地该文件（39 国，取“本轮开跑前的总榜” `archive/prior-before-20260915.json`），不带参数跑 `fit_weights` 即生效。

#### 2. 「测试arena 地图带两个模组资源」——实测：**资源全来自 cathay，Song Dynasty 贡献 0**

逐个模组拆资源表（`mods/*/jsons/TileResources.json`）与地图 2791 格对账：

| 项 | 结果 |
|---|---|
| 地图资源种类 | **36** = 32 种原版 + 4 种 cathay（`Bronze`/`Jade`/`Tea`/`醴`）|
| cathay 定义资源 | 7 种（其中 4 种地图在用）|
| Song Dynasty 定义资源 | **1 种，地图一个都没用** |
| 地图用到但无模组定义 | 0 |

所以“带两个模组资源的地图”这个说法在**资源层面不成立**；但 Song Dynasty 的真正作用是**文明**：

| 模组 | 提供文明 |
|---|---|
| unciv unrepentant cathay | Late Tang · MongolEmpire · Qindynasty · Sheng Tang |
| **Song Dynasty** | **Northern Song · Southern Song** |

机制上为何两种条件会共存（源码已核）：`ArenaLauncher` **只认 CLI `--mods`**（`for (mod in options.mods) mods.add(mod)`；`GameStarter` 只剔除不存在的 mod，**不会**并入 `mapParameters.mods`）；而 `map_fix --mods` 会把地图的 `mapParameters.mods` **覆盖**成 CLI 值。地图 `测试arena.json` 原稿是两个模组（已备份 `archive/maps/测试arena-pristine-20260915.json`），跑一轮 cathay-only 就会把地图里的 Song Dynasty 抹掉。

索引里因此是两个对照条件（同图）：

| 条件 | 地图 | mods | 名单 | 局数 |
|---|---|---|---|---|
| A | 测试arena.json | cathay-only | 默认 16 国 | 50 |
| B | 测试arena.json | cathay **+ Song Dynasty** | 39 国（含 Northern Song/Southern Song）| 21 |

**结论**：两批的**地形/资源完全相同**，差异在**文明池**；哪批算“标准”取决于评分目标是 16 国还是 39 国，不能用“资源不同”来解释。

#### 3. 按标准条件（cathay+Song + 39 国名单）重跑：**用户叫停，已跑部分保留**

- 叫停前已入库 **14 局**：`sym-r300g0..g6`（7）+ `sym-r301g0..g3`（4）+ 3 条旧 `r3` 局，均 `complete=True` 且 `mods=[cathay, Song Dynasty]`。
- 中止那一局的孤儿 stats 已删（`arena-stats-fad886b5.jsonl`）；`procs.py` 确认无残留 java 进程、无 `.mutbak` 残留。
- 为了让两个条件能分开出榜，`report.py` 新增 **`--mods-filter`**（与 `--maps` 对称，`all(m in record.mods)` 语义）；`nation_strength.py` 早有 `--mods-filter`。

#### 4. 叫停时的最终榜（用户指定口径：测试arena）

    python arena/report.py --kind rotation --maps 测试arena.json          # 71 局 / 39 国
    python arena/nation_strength.py --write --md arena/ranking-total.md --maps 测试arena.json

- 质量：索引 **97 局，stats/存档/胜负均 100%**，完整性闸门未剔除任何局。
- 榜首 **The Huns**（均名次 1.00，势 4.1823）；榜尾 **Byzantium**（5.67，存活率 0%）。
- 大样本国家（≥19 局，榜单参考价值最高）：MongolEmpire 2.50 · Persia 2.96 · Rome 3.07 · France 3.17 · Qindynasty 3.25 · Sweden 3.30 · Egypt 3.35 · Greece 3.42 · Late Tang 3.47 · Sheng Tang 3.52 · Babylon 3.58 · Ethiopia 3.61 · Songhai 3.68 · Russia 3.80 · India 4.57。
- 小样本（1–5 局）名次噪声大，已标在“局数”列（如 The Huns 3/1、Denmark 2/0）。

#### 5. 验证

`tests/test_arena.py` **58 passed**（新增 `--mods-filter` 分集用例）。

#### 6. 补测低局数国家（用户追加）：叫停时已完成 11 局

- 名单：`arena/nations-topup.json`（**23 国**，当时局数 2–5 —— 即默认 16 国名单之外的全部国家，含需 Song Dynasty 的 Northern Song）。
- 命令：`--start-round 320 --nations-file arena/nations-topup.json --mods "unciv unrepentant cathay,Song Dynasty" --minutes 20`（每轮 4 局×6 席 = 24 席，一轮覆盖全部 23 国）。
- 叫停时已入库 **11 局**（`sym-r320g0..`），均 `complete=True`；中止那局的孤儿 stats 已删（`arena-stats-f38706f8.jsonl`），无残留 java 进程。
- 效果：低局数国家由 **2–5 → 5–8 局**（中位由 3 → 7），但未达到「≥10」目标（被叫停）。
- **最终榜（叫停时）：`测试arena.json` 82 局 / 39 国**，榜首 The Huns（均名次 1.8）、榜尾 Byzantium（5.5）。

#### 7. 仍未完成的补测（下次直接续跑）

继续补测只需重跑同一命令并把 `--minutes` 给足（名单会自动重算）：

    python -c "...算出局数<10的国家..."                 # 或直接从 ranking-total.md 的「局数」列看
    python arena/run_rotation.py --start-round 340 --rounds 20 --minutes 20 \
        --base-map ".../maps/测试arena.json" --map-name 测试arena.json \
        --slots slots-测试arena.json --mods "unciv unrepentant cathay,Song Dynasty" \
        --nations-file arena/nations-topup.json

预期：每再跑一轮（4 局）全体低局数国家各 +1；要拉到 10 局约再需 2–3 轮（共 8–12 局、约 10–15 分钟）。

### 2026-09-24 · 派系平衡改为开局开关（与间谍同一套）

新游戏 → 高级设置增加 **Enable Faction Balance / 启用派系平衡**（`gameParameters.factionBalanceEnabled`）。默认开，旧存档没有这个字段时也保持开，现有对局行为不变。

关掉之后与关掉间谍同一路：左侧天平按钮不创建、顶栏图标隐藏、金币/军力修正为 0、回合不向 0 回归、花金币倾斜无效、观察切片的 `factionBalance` 为 null。内阁仍不驱动这套天平。

单测：`FactionBalanceToggleTest`。

### 2026-10-16 · 本地模型部署：用探测取代猜名字

症状：大脑 `/healthz` 报 ok、游戏里每个席位却都像原生 AI。根因是路由把模型名当厂商——`qwen*` 一律落到 Ollama 的 `:11434`，vLLM / LM Studio / llama.cpp 的用户不改 `OPENAI_BASE_URL` 就连不上；而 `openai_compatible` 又强制要求 API key，本地回环服务根本没有 key 可填。

- 新增 [civagent/utils/local_models.py](civagent/utils/local_models.py)：端口发现（11434/1234/8000/30000/8080/5001）、`context_length`（Ollama `/api/show` 的 `*.context_length`、vLLM `max_model_len`、llama.cpp `/props` 的 `n_ctx`）、量化标签、以及**行为探测**出来的 `structured` 能力。缓存 10 分钟、失败只缓存 30 秒（先起大脑后起 Ollama 不必重启）。
- [civagent/utils/llm_router.py](civagent/utils/llm_router.py)：优先级改为 `server/model` 前缀 > 用户选定的 provider > 名字启发；带 `distill/q4_k_m/32b/:tag/仓库/` 特征的一律判本地，`deepseek-chat`、`gpt-4o` 等云端产品名不受影响；回环免 key；配置只写 `model: ollama` 时用服务器实际加载的模型；本地调用走 **JSON_SCHEMA → JSON → plain** 降级阶梯（plain 自己剥 thinking、抠 JSON、失败补一条指令重试一次）；流式增量逐个剥推理段。**云端调用路径未改**。
- [deployment/ai/agent_api.py](deployment/ai/agent_api.py)：`/healthz` 新增 `llm: offline`（带端口与原因），`/status` 新增 `local` / `discovered` / `prompt_over_80pct_context`；`parse_intent_fences` 先剥思考再解析围栏——草稿里写过的「宣战」不算对玩家的许诺。
- 文档：[docs/local_models.md](docs/local_models.md)（各服务器启动命令、config 示例、故障对照表、选型建议）。
- 单测：`tests/test_local_models.py`（17，全 fake transport，不碰端口）+ `tests/test_llm_router.py`（43，含阶梯降级与云端路径不变两条守卫）。
- **未完成**：游戏侧 `AgentConfig.decisionTimeoutMs` 仍是 15 秒硬编码、Options→Agent 没有该项，32B 级模型常在超时后静默回退——这是本地部署现在最主要的失败原因，要改的是 Unciv-Source 那一侧。多席位串行的延迟预算同样没做。

同日修正（查官方文档后，发现自己上一段里三处是凭记忆写的）：

- **端口表错了两处**：llama.cpp 的默认口官方已在从 `8080` 迁到 `9931`（启动会警告，建议 `--port` 自己钉住），候选清单补 `9931` 与 LMDeploy 的 `23333`；而 `8080` 同时是 mlx-lm 的默认口——**端口不能用来认身份**。
- 因此新增 `flavor_for()` 端点签名识别：`/api/tags`→ollama、`/props` 或 `/admin/server/props`→llamacpp、`/api/v0/models`→lmstudio、`/metrics` 前缀 `vllm:` / `sglang:`→对应服务器，都不像老实报 `openai_compatible`。顺带修正：LM Studio 的原生 REST 是 `/api/v0/models`，我原先写的 `/api/v0/providers` 是猜的；llama.cpp 的 `/props` **默认关闭**（需 `--props`），识别与上下文都可能拿不到，这不是 bug。
- **Ollama 的上下文字段用错了来源**：`/api/show` 是模型上限，真正分配到的预算在 `/api/ps` 的 `context_length`（`details.quantization_level` 也在这里）。切片被截断发生在分配值那一层，所以 profile 现在同时报 `context_length`（分配）与 `context_capacity`（上限）。
- **vLLM 的 `/v1/models` 不保证有 `max_model_len`**（letta 就这么崩过一次），`context_length` 为 null 时给 `context_note` 让用户自己按 `--max-model-len` / `-c` 对，**不编数字**。
- 文档 [docs/local_models.md](docs/local_models.md) 重写并列出全部出处；测试补 flavor 与三种上下文来源，全量 `pytest tests/ -q` **206 passed**。
- 再补：识别链加入 llama.cpp `/metrics` 的 `llamacpp:` 前缀（`/props` 默认关闭时仍能认出来），provider 前缀新增 `mlx`、`lmdeploy`；本地调用加 120s 自身上限（`LOCAL_REQUEST_TIMEOUT_S`），避免游戏 15s 放弃后线程抱着席位锁等 OpenAI 默认的 600s。
- 取证：除 fake transport 外新增**真 socket** 回归（临时端口起假服务器，探测拒 `json_schema`→降级 `json`→instructor 解析出对象），并已在 127.0.0.1:18080 手工跑通「探测→降级→`/healthz` 200（回环免 key）」；全量 `pytest tests/ -q` **210 passed**。

### 2026-10-16 · jevos 决策闸门（可选）+ 删除上游迁移公告

[feder-cr/jev](https://github.com/feder-cr/jev) 的 jevos 是 MIT 许可的 CPU 决策模型（`POST /v1/systemone`，默认 `:8017`，`GET /health`，TypeSafe Jev 线格式，`noul`/`choice`/`score`，28–130 ms，8192 上下文）。它不能生成意图，所以定位是**闸门**：LLM 提意图，jevos 逐条回答"这一轮值不值得做"。

- 新增 [civagent/utils/jev_decider.py](civagent/utils/jev_decider.py)：`state_from_slice` 拼紧凑状态（回合/产出/城市/交战/已知文明/派系天平）；`gate_intents` 一次请求带最多 6 道 `noul` 题（状态只读一遍），低于阈值丢弃，超上限原样保留；服务没起/超时/HTTP 非 200/答案缺失一律**原样放行**（fail-open）。`CIVAGENT_JEV_GATE` / `_URL` / `_THRESHOLD` / `_KEY`，或 config.yaml 的 `jev_gate` 等；默认关闭，**不下载任何模型**。
- [deployment/ai/agent_api.py](deployment/ai/agent_api.py)：`/v1/decision` 在 scrub 之后过闸；判词写进 decision 记忆（`"jev"`），`/status` 增加 `jev` 块（enabled/url/reachable/threshold/last）。
- 文档：[docs/local_models.md](docs/local_models.md) 新增 §9（启动、配置、线格式、以及"它是闸门不是裁判"的边界）。
- 测试：`tests/test_jev_decider.py` 11 例，真 socket 假 jevos，覆盖阈值/上限/缺答案/非 200/不可达/Bearer。
- README：删掉上游 "Moved the project repository to asdqsczser/CivAgent" 两处（顶部公告 + News 条目）。
