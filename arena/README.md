# arena — 训练场（离线国家强度评测环）

**它是什么**：一个不依赖任何 UI 的评测环，用 desktop jar 的引擎本体跑"国家强度"对照实验。
与在线对局栈并列，唯一的耦合点是 T1/T2 时席位会调大脑的 `/v1/decision`（T0 全离线，不需要大脑）。

**三档不可跳**：先 T0 基准（无 LLM，纯国家本体）→ 再 T1（LLM × 国家）→ 再 T2（方法论对打）。

---

## 地图套件（按图分集）

**硬规则：不同地图的对局不得混进同一张排名表。** `index.jsonl` 的 `map` 字段是分集键；`report.py` / `cross_report.py` 读排名时必须按 `map` 过滤（或显式 `--map`）。

| 地图 | 用途 | 出生点 | 对称轴 |
|------|------|--------|--------|
| `arena-64x64.json` | GUI 冒烟 / 人机手感；盛唐 vs 蒙古 | 2（盛唐左 / 蒙古右） | **屏幕竖中线** `screenX=0` |
| `arena-64x64-rotation.json` | T0/T1 轮换赛（6 槽） | 6（`slots-64x64.json`） | 同上 |

**对称验收（生成器内置，失败即抛）**

1. `worldWrap=false` —— 环绕圆柱上的「镜像」在 UI 里看起来不是竖中线对称（2026-09-12 已踩坑）
2. 镜像公式：`(x,y) → (-x-y, y)`（`screenX → -screenX`）
3. 陆地格数左 = 右；成对格子内容字节级一致
4. 出生点成对迁移：右席 = 左席的精确镜像（禁止再出现「埃塞俄比亚漂到 (27,0)」）

```bash
python arena/map_generator.py --out <Unciv>/android/assets/maps/arena-64x64.json --players 2p
python arena/map_generator.py --verify --out <Unciv>/android/assets/maps/arena-64x64.json
```

GUI 手感测试：把生成结果拷到客户端 `Unciv/maps/`（或设 `ARENA_CLIENT_MAPS`），新游戏 → 自定义地图 → 勾选 **unciv unrepentant cathay** → 选盛唐。

---

## 快速上手

```bash
cd C:\Users\<user>\Desktop\Civagent_repo

D:\Python\python.exe arena/paths.py            # ① 先看路径认不认得出来（换机器必做）

D:\Python\python.exe arena/run_rotation.py --rounds 2 --max-turns 150 --difficulty Emperor
D:\Python\python.exe arena/report.py --rebuild          # ② 出排名（含覆盖率）

D:\Python\python.exe arena/fit_weights.py               # ③ 自动权重：哪个权重最能预测强弱
D:\Python\python.exe arena/report.py --weights fitted   # ④ 用拟合权重再看一次排名
```

单局、单步：

```bash
D:\Python\python.exe arena/evaluate.py --tag r0g0              # 单局三套分（A/B/C）
D:\Python\python.exe arena/evaluate.py SaveFiles/arena-stats-714c8c21.jsonl
D:\Python\python.exe arena/cross_report.py --rebuild           # 国家 × 槽位矩阵
D:\Python\python.exe arena/spawn_classify.py                   # 槽位评级（发展型/早战型）
```

---

## 装载在哪里

| 工件 | 位置 | 备注 |
|---|---|---|
| 评分口径（**唯一来源**） | `arena/scoring.py` | power 公式、征服加成、归一化、名次 |
| 路径/环境 | `arena/paths.py` | 全部可用环境变量覆盖 |
| 对局索引 | `arena/index.jsonl` | tag ↔ gameId ↔ stats/final + 胜负 |
| 对局日志 | `arena/logs/{tag}.log` | **仓库内**，不会被 `%TEMP%` 清掉 |
| 无头驱动器 | `Unciv-Source/desktop/src/.../ArenaLauncher.kt` | 打进 `desktop/build/libs/Unciv.jar` |
| 生成的地图 | `Unciv-Source/android/assets/maps/arena-*.json` | 引擎按 `maps/<name>` 读 |
| 每回合统计 | `.../assets/SaveFiles/arena-stats-{gameId8}.jsonl` | **评分骨干** |
| 终局存档 | `.../assets/SaveFiles/arena-final-{gameId8}` | Base64(gzip(GameInfo)) |
| 汇总排名 | `arena/ranking.json`、`arena/rotation-cross-report.json` | report.py / cross_report.py 写 |
| 拟合权重 | `arena/fitted-weights.json` | fit_weights.py 写 |
| 大脑轨迹（T1/T2） | `Civagent_repo/deployment/data/{gameId}/` | 决策/应用/事件 |

---

## 看排名

**`python arena/report.py`** 打印两块，缺一不可：

1. **覆盖率** —— 索引多少局、多少局有 stats、多少局有胜负、缺口是哪些 gameId。
   以前这里没有，导致"只有几局"的排名被当成全量（实测 54 局里只有 29 局可评分）。
2. **国家排名** —— 局内归一化均分 + 一次征服加成（占城 +0.2 势比 / 灭国 = 3 奇观折算 median）。

`--weights fitted` 换成拟合权重（`fit_weights.py` 的产物），就能看到"自动权重算法后的排名"。
`--kind batch` 只看智能体对打那一批（这批以前完全不进评分链路）。

---

## 自动权重（fit_weights.py）

- **标签**：局内 `engineScore` 最高者 —— 用引擎自己的 `calculateTotalScore()`，**独立于本仓库的 power 公式**，
  避免"用我们自己的权重证明我们自己的权重"。
- **特征**：8 个因子在局内的 z-score。
- **模型**：L2 逻辑回归（numpy 全批量梯度下降，不需要 sklearn/scipy）。
- **验证**：留一局交叉验证，对比 hand / equal / fitted 三套权重的命中率。

实测（29 局 / 121 样本，2026-09-12）：

| 权重 | 命中率 | 秩相关 |
|---|---|---|
| hand（手写，现状） | 55.2% | 0.587 |
| equal（等权） | 65.5% | 0.649 |
| **fitted（拟合）** | **76.9%**（LOO） | — |

> 结论：**手写权重几乎等于猜**。默认排名仍用手写口径（保持与历史报告可比），
> 但要知道"能不能预测强弱"这个问题的答案是拟合权重更能。

**旧图先验（wrap 时代）**：结论在 `arena/archive/t0-wrap-legacy/conclusions.json`；
`report.py` 默认以 `--prior-weight 0.1` 并入（极低权重），新竖中线图局权重为 1.0。
`--prior ""` 可关掉。

---

## 数据契约

`arena-stats-{gameId8}.jsonl`（每回合每文明一行，由 ArenaLauncher 追加写）：

```
turn  civID  gold  goldPerTurn  sciencePerTurn  culturePerTurn  cultureStored  faith
techs  cities  population  wonders  engineScore  militaryUnits  militaryMight
capturedCities  eliminatedNations
```

`arena/index.jsonl`（每局一行）：

```
tag  kind(rotation|batch)  gameId  map  players[]  roles{civ: civagent|native}
difficulty  maxTurns  mods[]  seed  log  stats  final
finishedTurn  turnCount  winner  outcome  source(log|disk)
statsExists  finalExists  hasOutcome  scorable  complete
```

> `civID` 就是国家名（`France`、`Sheng Tang`），槽位信息另外靠 `roster-{tag}.json` + `slots-64x64.json`。

---

## 环境变量

```
ARENA_UNCIV_ROOT   ARENA_GAME_CWD   ARENA_SAVE_DIR   ARENA_MAP_DIR
ARENA_JAR          ARENA_JAVA       ARENA_LOGS       ARENA_INDEX
ARENA_CLIENT_MAPS  ARENA_MODS       ARENA_XMX        ARENA_LEGACY_LOGS
ARENA_BATCH_MAP
```

细节见 `arena/paths.py` 顶部。注意 `ARENA_CLIENT_MAPS` **默认空**：离线评测不再往正在运行的
客户端 `maps/` 目录写文件（以前是硬编码写进去的）。

---

## 已知缺口（别当成"已解决"）

1. **stats 覆盖率只有 53.7%**（54 局中 29 局）：22 局是"存档在、stats 被清掉"，**无法复原**，
   要拿回这些局的评分只能重跑。报告里会列出 gameId。
2. **停止方式不优雅**：批次靠墙钟超时（`subprocess timeout`）切断 → java 被硬杀，
   该局没有终局存档（报告里标为"有 stats 但没跑完"）。
3. **ArenaLauncher 的输出契约没有 schema 校验**：stats 字段被 `scoring.py` 依赖，
   引擎侧改名不会有任何报错，只会静默算错。
4. **日志不落盘就没命**：新日志已写 `arena/logs/`，但历史日志只在 `%TEMP%`，
   一旦被清就只能靠 `SaveFiles` 反推（`source=disk`，无胜负标签）。
5. **旧索引里的 wrap 对称图已作废**：2026-09-12 前用 `worldWrap=true` 圆柱镜像生成的局，
   **肉眼不是竖中线对称**（左陆 1334 / 右陆 482）。那些局的排名**不要**与新图混表；
   重跑 T0 才有可比性。新图 `worldWrap=false`，生成器 `verify_symmetry()` 失败即拒写。
6. **`report.py` 按地图分集**：索引已有 `map` 字段；过滤 UI（`--map`）若尚未接上，
   人工出排名时必须自己按 `map` 拆，禁止把不同图的局平均到一起。

---

## 目录

```
arena/
  paths.py          路径/环境（唯一来源）
  scoring.py        评分口径（唯一来源）
  gamelog.py        日志解析 + 索引 + 覆盖率
  procs.py          残留进程清理（psutil，不用 wmic）
  report.py         总报告：覆盖率 + 排名（rotation/batch 统一）
  cross_report.py   轮换赛：国家 × 槽位矩阵
  fit_weights.py    自动权重（逻辑回归 + 留一局验证）
  evaluate.py       单局三套分
  map_generator.py  对称地图合成
  spawn_classify.py 出生点评级
  run_rotation.py   轮换赛驱动
  run_batch.py      智能体对打驱动
  logs/             对局日志（入库）
  index.jsonl       对局索引
```
