# 非原版内容如何进 LLM（内容桥 / content bridge）

> 直接回答三个问题：
> 1. **不需要训练**——也不可行：模组任意、会更新、一局一套。LLM 的职责是**读懂递过来的描述**，不是把模组记进权重。
> 2. **方向反了**：不是「理解后用 Unciv JSON 表示」。`GameInfo.ruleset` **本身就是** Unciv JSON（已合并 base+模组）。要写的是**读模型/投影器**（ruleset → 卡片），不是写回器。
> 3. **引擎已经提供了自然语言**：每个规则集对象有 `getCivilopediaTextLines(ruleset)`；每个 unique 都有 `text`（638 个模板，带 `[]` 参数占位），**未知 unique 也有 `text`**，不是黑盒。

---

## 1. 引擎已经给了什么（有代码证据）

| 能力 | 位置 | 为什么关键 |
|------|------|-----------|
| 规则集是**已合并**的活对象 | `GameInfo.ruleset`（`Ruleset.add` 合并 base + 扩展 + `replaces`） | 模组不用单独解析；引擎已经算好最终形态 |
| 每个对象字段**少而通用** | `BaseUnit`：strength/rangedStrength/movement/cost/unitType/requiredTech/requiredResource/obsoleteTech/upgradesTo/replaces + uniques；`Building`：requiredTech/cost/requiredBuilding/requiredResource/replaces/uniqueTo + uniques；`Technology`：cost/column/row + uniques… | 任何模组内容都能用同一套字段描述 |
| **能力谓词**现成 | `BaseUnit.isMilitary / isCivilian() / isRanged() / isWaterUnit / isCityFounder()`；`Building.isWonder / isNationalWonder / isAnyWonder()` | 「新单位是干什么的」不用问 LLM，从字段就能推 |
| **unique 模板化** | `Unique.text` / `placeholderText` / `params`；`UniqueType` **638** 个模板；`Unique.getDisplayText()` | 模组新增的 unique 也落在同一套模板语法里，可读可译 |
| **自然语言渲染** | `getCivilopediaTextLines(ruleset)`（Nation/Building/Belief/Policy/Technology/Era/Speed…）；`Building.getDescription(city)`；`Technology.getDescription(civ)`；`Policy.getDescription()` | 「如何用自然语言描述」**引擎已经写了**，别重写 |
| 基础规则集可作基线 | `RulesetCache[BaseRuleset.Civ_V_GnK.fullName]`；`Ruleset.allRulesetObjects()` | 「哪些是原版有的」= 集合差，可计算 |
| 模组替换语义 | `replaces`、`uniqueTo`、`modOptions.isBaseRuleset`、`unitset/tileset` | 增量摘要的依据 |

**结论**：模组内容是**可枚举、可投影、可描述**的；缺的不是知识，而是**把引擎知识投影成 LLM 可消费形态的那一层**。

---

## 2. 你那条流水线，逐步校正

| # | 你的步骤 | 是否可计算 | 做法 |
|---|----------|-----------|------|
| 1 | 观察到不是原版 | ✅ | `ruleset.name` + 模组列表 + `isBaseRuleset` |
| 2 | 是不同规则集吗 | ✅ | 合并结果 vs `RulesetCache[Civ_V_GnK]` 的差异规模 |
| 3 | 是扩展模组吗 | ✅ | 模组 manifest（`topics: unciv-mod-rulesets / -expansions / -graphics`） |
| 4 | 扩展了哪些内容 | ✅ | 按 kind 做**集合差**（units/buildings/techs/policies/resources/…） |
| 5 | 哪些原版有 | ✅ | 与基线集合**交集** |
| 6 | 哪些原版没有 | ✅ | **差集**（新增/替换） |
| 7 | 新增的如何理解 | ⚠️ 不是「理解」，是**投影** | 字段 → 能力标签（`caps`）+ `getCivilopediaTextLines` |
| 8 | 理解了如何用 Unciv JSON 表示 | ❌ 方向反了 | Ruleset **已经是** Unciv JSON；写**读模型**即可 |
| 9 | 如何用自然语言在 LLM 与 Unciv 间传输 | ✅ | 规则集指纹 + 增量摘要 + **按需内容卡**；unique 用 canonical text，界面用 Civilopedia 文本 |
| 10 | 如何执行 | ✅ | 意图引用 **ID/能力**，执行前按规则集校验（D1/D2 已落地）；未知一律拒绝并回原生 |

你流程里最「想当然」的两处：**第 7 步不是理解问题**（引擎字段已够），**第 8 步方向反了**（不需要回写 JSON）。其余都成立，而且大部分是**可计算的**。

---

## 3. 架构：投影 + 增量 + 按需查询

```
L0 引擎真相      GameInfo.ruleset（已合并 base+模组，模组无关）
     │
L1 内容卡投影    任意对象 → ContentCard{kind,id,caps,stats,requires,uniques,desc}
     │           （Kotlin 投影器，纯读，复用 Civilopedia 渲染）
     │
L2 增量摘要      合并规则集 − 原版基线 → {added, removed, replaced} by kind
     │           直接回答你的第 4–6 步；只把「增量 + 本回合选项」送 LLM
     │
L3 按需查询      游戏内网关新增 GET /v1/content?kind=&id=（与 GET /v1/ruleset/delta）
     │           LLM 看到不认识的东西就问，不预灌整棵树（守住决策 22）
     │
L4 执行          意图带 ID/能力 → 规则集校验 → 引擎执行；未知 → ok:false 回原生
```

**为什么把查询放在游戏网关（8081）而不是大脑（2335）**：规则集活在游戏进程里，`AgentGateway` 本来就是「服务端工具」（决策 7）。让大脑**反向按需拉取**，比把规则集序列化塞进每个切片更省、更准，也天然守住决策 22「不倒整棵科技树」。

### 3.1 内容卡（L1）形状

```json
{
  "kind": "unit",
  "id": "Hunter Gatherer",
  "name": "Hunter Gatherer",
  "era": "Neolithic era",
  "caps": ["military", "land", "melee", "city-founder"],
  "stats": { "strength": 6, "rangedStrength": 0, "movement": 2, "cost": 20 },
  "requires": { "tech": null, "resource": null, "building": null },
  "replaces": null,
  "uniques": ["May found a city"],
  "desc": "Civilopedia 文本（getCivilopediaTextLines 拼行）"
}
```

### 3.2 能力标签推导（全部来自现有字段/谓词，不猜）

| kind | caps 来源 |
|------|-----------|
| unit | `isMilitary` / `isCivilian()` / `isRanged()` / `isWaterUnit` / `isCityFounder()`；`unitType.name`；`requiredTech/requiredResource/obsoleteTech/upgradesTo` |
| building | `isWonder` / `isNationalWonder` / `isAnyWonder()`；`requiredTech/requiredBuilding/requiredResource/replaces/uniqueTo` |
| resource | `resourceType`（Luxury/Strategic/Bonus）、`improvement`、`revealedBy` |
| tech | `era()`、`cost`、`column`、前置 |
| policy | `policyBranchType`、`row/column`、`requires` |
| nation | `leaderName`、`preferredVictoryType`、`cityStateType`、`uniques` |

### 3.3 增量摘要（L2）

```
rulesetFingerprint = hash(ruleset.name + mod 列表 + isBaseRuleset)
delta = {
  baseRuleset: "Civ V - Gods & Kings",
  mods: ["Emperors and Deities"],
  added:    { unit: [...], building: [...], tech: [...], resource: [...], era: [...] },
  replaced: { building: [{id, replaces}] },
  removed:  { unit: [...] },
  newUniques: ["..."],          // 基线里没出现过的 unique 模板文本
}
```

只送**差集 + 本回合真正要选的选项**；`added` 的每项可只送「ID + 一行 desc」，细节走 L3。

---

## 4. 传输策略（守住决策 22）

| 内容 | 放哪 | 大小控制 |
|------|------|----------|
| 规则集指纹 + mod 列表 + `isBaseRuleset` | 每次决策切片的 `ruleset` 小节 | 几十 token |
| 增量摘要（`delta`） | **会话级 system prompt**（按指纹缓存，一次） | 上限 N 条，超出只留 ID |
| 本回合要选的**选项**（仅当 Agent 需要选，如建造/科研） | 切片 `options`（每条一张精简卡） | 只送引擎已判定「可选」的 |
| 任意不认识的 ID | **按需** `GET /v1/content` | 一次一张 |
| 语言 | unique 用 canonical 英文（稳定）；人话用 Civilopedia 文本（跟 `GameSettings.language`） | — |

**关键**：Agent 只需要「它要决策的那部分」的模组知识。当前是**外交**（资源 + 关系 + 军力），模组知识需求极小；将来若把建造/科研交给 Agent，就送**该城当前可选项**，而不是整棵树。

---

## 5. 未知内容

- 未知 unique：`Unique.type == null`，但 `text`/`params` 仍在 → 卡片里 `uniques` 照常给文本，只是 `caps` 可能为空。**永不崩**。
- 未知资源/单位 ID：交易/建造执行前查规则集，查不到 → `ok:false` 并回原生（`AgentTurnHook.executeTrade` 已是这个形状）。
- 未知时代名：`normalize_era_tier` 三级兜底（已落地）。

---

## 6. 那「训练」和「策略档案」放哪

- **不训练**。每局模组不同，微调不可行也不必要；LLM 读的是**运行时投影**。
- **可选**：`ruleset_profiles/<fingerprint>.md`——手写或离线生成一段「这个模组的胜利条件/外交机制/关键资源」摘要，注入 system prompt。这是**上限优化**（打得好），不是**不崩的前提**。
- **可选**：离线把某模组的 `delta` 跑一遍缓存成文件，省运行时投影开销（缓存失效靠指纹）。

---

## 7. 与既有决策的关系

- **决策 18**（Agent 不点单位、棋盘归原生 AI）→ 模组知识需求被压到最小。
- **决策 19**（六边形网格、不读像素）→ tileset 美化模组与本桥无关。
- **决策 22**（切片只传顶栏 + 排名，不倒引擎接口）→ 本桥用**增量 + 按需查询**，不推翻它。
- **决策 24**（模组兼容）→ 本桥是它的「内容侧」补全；D1/D2/D3 已落地。

---

## 8. 分阶段

| 阶段 | 内容 | 验收 |
|------|------|------|
| **M-content-0** ✅ 已落地 | L1 投影器 + `GET /v1/content`（只读，unit/building/resource/tech/improvement/promotion/nation/policy/era）+ `GET /v1/ruleset`；切片加 `ruleset` 指纹 | EnD 里查任意单位/资源返回卡片，`caps`/`desc` 正确 |
| **M-content-1** ✅ 已落地 | L2 增量摘要（`RulesetDelta` + `GET /v1/ruleset/delta`）；资源 `kind` 进切片（`resourceKinds`） | 简中 + EnD：交易能对新增资源给出正确 kind，或被明确拒绝 |
| **M-content-2** | L3 按需查询闭环（LLM 主动问）+ 可选策略档案 | 总转换规则集一局跑完，外交不出现原版专属名词 |

### 测试夹具
在现有合成模组基础上加：`base-replace`（替换原版建筑）、`expansion-resources`（新增 strategic/luxury）→ 断言投影器 `caps`/`desc` 正确、未知 unique 有文本、按需查询命中。
