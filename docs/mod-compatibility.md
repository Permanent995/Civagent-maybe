# 模组兼容（CivAgent）

> 结论先行：**不会「随便来个模组就全炸」**，因为棋盘微操本来就交给原生 AI，Agent 只出外交意图。
> 但「换皮」这个直觉也**不成立**——真正要做的是三件事：**意图按规则集校验**、**该从规则集取的名字/类别别写死**、**理解层不要用原版先验硬套**。
> 现存缺陷很窄（见 §3），其中只有 1 个是「行为违规」，其余是质量/潜在问题。

---

## 1. 为什么架构天然抗模组

| 关注点 | 归谁 | 为什么抗模组 |
|--------|------|--------------|
| 造兵 / 工人 / 城市建造 / 科技 / 政策 / 宗教 | **原生 AI** | `NextTurnAutomation.automateCivMoves` 里 `AgentTurnHook.applyIntents(civInfo)` 只是**叠加**，之后 `chooseTechToResearch`、单位/城市自动化、`adoptPolicy` 等全部照跑。原生 AI 直接读 `ruleset`，模组改什么都自动跟上 |
| Agent 的落子 | 意图契约 | `civagent/utils/intent_models.py` 的 `INTENT_TYPES` 只有 `declare_war / make_peace / propose_trade / buy_luxury / open_border / research_agreement / seek_peace / form_ally / mutual_defense / common_enemy / share_intel / chat`。**没有任何单位/科技/建筑名**，新规则集无法让动作空间失效 |
| 观察切片 | 引擎 API | `ObservationSlicer.kt` 用 `civ.getEra().name`、`getCivResourcesByName()`、`cityConstructions.currentConstructionName()`、`tech.techsInProgress`、`relationshipLevel()` 等通用调用；`grep` 全目录**没有一个写死的原版内容名**（仅 `ruleset.tileResources[...]` 这种数据驱动查表） |
| 交易物品 | 规则集查表 + 保护 | `AgentTurnHook.executeTrade` 用 `ruleset.tileResources[offer.ref ?: offer.type]`，且 `?: return AppliedEntry(... error="unknown offer type ...")`——**查不到就拒绝，不会 NPE** |
| 席位 | 通用 | `AgentMod.seedSeats` = `playerType == AI && isMajorCiv() && !isDefeated()`，与具体国家无关 |
| 美化模组（tileset） | 无关 | 决策链是结构化 JSON，**不读像素**；`/upload` 的视觉只处理玩家发的图，与棋盘 tileset 无关 |

实测三个模组：

| 模组 | 类型 | 结论 |
|------|------|------|
| `5Hex Tileset` | `unciv-mod-graphics`，只有 `jsons/TileSets/5Hex.json` + 图 | **零影响** |
| `unciv unrepentant cathay` | 扩展（新增国家/建筑/单位/资源） | 不崩；棋盘正常。缺口是「Agent 不认识新增资源」→ 交易用不上（质量） |
| `Emperors and Deities` | `isBaseRuleset: true`，46 个自定义国家（Etruria/Dai Viet/Srivijaya…）、全新科技/单位/资源/时代 | 棋盘正常（原生 AI）。缺口见 §3 |

> 附带：EnD 的 `Nations.json` 带 `//` 注释（非严格 JSON）。**只有引擎解析规则集文件**，Python 大脑不解析，所以无害。

---

## 2. 缺陷矩阵（含 file:line）

| 编号 | 缺陷 | 触发模组 | 级别 | 位置 |
|------|------|----------|------|------|
| **D1** | Agent 的外交意图**绕过规则集约束** | 设了 `DiplomaticRelationshipsCannotChange` 的规则集/剧本 | **行为违规（live）** | `NextTurnAutomation.kt:51` 的 `applyIntents` 在 `:52` 的 `if (!ruleset.modOptions.hasUnique(DiplomaticRelationshipsCannotChange))` **之前** |
| **D2** | 写死原版国家中文名，直接下标 | 非 6 个原版国家 + 简中 | **KeyError（遗留路径）** | `civagent/action_space/civ_names.py` 只有 china/rome/egypt/mongolia/aztecs/greece；`civagent/skills.py:163,164,401,402` 用 `[...]` 下标 |
| **D3** | 时代序号按原版 Eras.json 顺序 | 自定义时代（Neolithic…） | 质量（用词/断代） | `civagent/utils/leader_voice.py:12` 注释自述 |
| **D4** | 观察切片带 RC 专属 `factionBalance` | 非 RC / 模组规则集 | 质量（无意义但无害） | `ObservationSlicer.kt` `factionBalance()` → `FactionBalanceScreen.determineFactionAndScore` |
| **D5** | LLM 用原版先验理解模组内容 | 全新规则集 | 质量（不会崩，但「想当然」） | 观察切片只给名字，不给「类别」；见 §4 P-C |
| **D6** | 遗留 `civsim` 原生决策写死原版 | 仅当 `use_ai: native_unciv` | 潜在 | `civsim/simulator/simulator.py:448` 示例 `{"result": "Masonry"}`、`strategy.py` 生产示例 `"worker"` |

**哪些不是问题：** 交易资源查表已做 null 保护（不崩）；tileset 无像素依赖；席位/意图契约与内容名无关；棋盘微操归原生 AI。

---

## 3. 修复设计

### P-A 棋盘永远归原生 AI
不把造兵/建造/科技搬进 Agent。模组兼容成本因此保持在「外交层」。

### P-B 意图按规则集校验，而不是只过白名单
`AgentWhitelist` 只挡「命令类型」；还需挡「规则集是否允许」。
- D1：`applyIntents` 前置同一条 `DiplomaticRelationshipsCannotChange`（以及将来任何禁外交的 unique）判断，被禁则直接回原生。
- 交易：`executeTrade` 已按 `ruleset.tileResources` 校验资源存在与库存，保持；补一条「资源类别来自规则集」即可。

### P-C 观察切片保持精简（决策 22），只补「外交真正需要的规则集标签」
不推翻决策 22（**禁止**倒整棵科技树/完整建造队列给 LLM）。只在现有字段上补**引擎已算好的类别标签**：
- 资源：`name + amount + kind`（`kind` 取 `resource.resourceType` → luxury/strategic，来自规则集）；
- 本席：`nationName / leaderName`（来自存档/规则集，用于人设，替换写死表）；
- 时代：传 `eraName` + `eraNumber` 的同时，附带规则集里该时代的顺序（避免用原版下标猜）。
这既解决 D2/D3/D5 的「想当然」，又不违反 22。

### P-D Agent 是玩家，不是 GM
任何意图都必须过：白名单（命令类型）→ 规则集约束（uniques）→ 席位（不能动真人席）→ 引擎合法性。模组改规则时，Agent 跟着被约束。

### P-E 未知一律降级回原生，绝不崩
沿用现有 `catch (_: Exception) → emptyList()/false` 风格；D2 这类下标改为 `.get()` + 兜底原名。

### P-F 策略档案（可选，只为「打得好」而非「不崩」）
总转换规则集（EnD）的外交/胜利逻辑与原版差异大。给 `ruleset fingerprint`（名字 + mod 列表哈希）挂 `ruleset_profiles/`；无档案时用通用能力驱动策略。**不是不崩的前提**，是上限优化。

---

## 4. 分阶段

| 阶段 | 内容 | 验收 |
|------|------|------|
| **M-mod-0（P0）** ✅ 已落地 | D1 规则集约束前置；D2 名称改 `.get()` + 存档/规则集取名；D3 时代序走规则集 | 合成模组下：禁外交规则集里 Agent 不再宣战；非原版国家 + 简中不 KeyError |
| **M-mod-1（P1）** | P-C 补规则集标签（资源 kind / nation / era 序）；D4 在非 RC 规则集下省略或标注；D6 明确 `use_ai: civagent` 为唯一路径并给遗留路径加护栏 | 观察切片含模组资源类别；交易能对新增资源给出正确 kind 或被明确拒绝 |
| **M-mod-2（P2）** | P-F 策略档案 + 通用能力策略 | EnD 一局能正常跑完，外交不出现原版专属名词 |

### 测试夹具（合成模组）
在 `tests/` 下造四个最小模组：
1. **base-replace**：删/改原版单位与资源名 → 断言切片/交易不崩、降级为拒绝；
2. **expansion**：加新资源（含 strategic/luxury）→ 断言 `kind` 正确、交易可成；
3. **no-diplomacy**：`DiplomaticRelationshipsCannotChange` → 断言 Agent 不产生宣战/议和；
4. **tileset**：仅 `TileSets/*.json` → 断言观察切片与意图完全不受影响。

---

## 5. 一句话回答「换皮吗」

- **棋盘**：不用换皮——引擎 + 原生 AI 已经数据驱动，模组自带数据。
- **理解**：要换，但不是「把原版名字翻译一遍」，而是**把该取的名字/类别从规则集取**（P-C），并**按规则集约束行动**（P-B）。
- **上限**：总转换想「打得好」需要策略档案（P-F），但那是优化，不是不崩的前提。

所以：**「都得炸」太悲观，「换皮就行」太乐观**，真实工作量集中在 §3 的 P0/P1。
