"""训练场评分：power 公式、征服/灭国加成、归一化、名次。

**这是评分的唯一来源。** 以前同一份公式写了三遍（run_rotation.py 的 docstring、
evaluate.py 的函数体、cross_report.py 的 power()），并且已经漂移：
cross_report 把灭国加成写成 `90/500`（硬编码 median≈500），evaluate 写成 `eliminated*90`。
两份不同口径的"同一排名"会让结论互相打架，故集中到这里。

口径（2026-09-14 三轴，主榜=均名次）
------------------------------------
局内名次   = 后程相对势（100/150，不含征服加成）；灭国者排在存活者之后
势（诊断） = 同上相对势的局内归一化（1.0 = 中位）—— 占城已含在城/人/军里，不再叠征服
征服（诊断）= 净占城 / 灭国 / 存活；不进入主榜分数
跨局主榜   = 均名次升序（越小越好）；势与征服只做并列列
旧图先验与 duel150 不进主榜
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Iterable, Mapping, Sequence

CHECKPOINTS = (50, 100, 150)
# 势（诊断）只用后程截面，避免 t50 把中期肥、终局灭的国家抬起来。
POWER_CHECKPOINTS = (100, 150)

# 手写权重（用户定纲 2026-09-12）
# 商定权重（2026-09-14 用户定：奇观 30→20、军力 10→15、科技 4→14）
# 改之前先把旧值存成 arena/weights-legacy-hand.json —— 历史榜都是旧权重算的，
# 想在 `--compare` 里复现旧值就需要它（report/nation_strength 都支持 --weights <json>）。
# 商定权重（2026-09-14 第四版：按“目标占比”反解 + 引擎分提权）
#   方法：w_f ∝ 目标占比_f / 实测原始量_f（原始量 = 330 个文明-局的因子总量），
#   再把奇观权重锛定 20 以保持与历史量级可比。实测原始量：
#     cultureStored 319,467  militaryMight 284,907  engineScore 229,422  faith 125,799
#     population 55,792  techs 14,982  cities 3,942  wonders 1,407
#   本版目标占比（已逐项验证命中）：军力 60.0% 人口 8.0% 科技 8.0% 文化 5.0% 奇观 4.0% 信仰 4.0%
#     **引擎分 10.0%**（上一版只有 1%，用户要求提高）城市 1.0%
#   军力项刻意用 arena 裁决器的简化定义（Σ max(基础强度, 远程强度)），**不计派系平衡修正**
#   —— 引擎的 getMilitaryMight() 带 √金币加成与派系修正，用户明确不要那个（见 EXECUTION）。
#   历史版本：weights-legacy-hand.json（军力10/奇观30/科技4）、weights-legacy-mil15.json（军力占 80.2%）、
#   weights-legacy-engine1.json（本版之前，引擎占 1%）。
HAND_WEIGHTS = {
    "population": 1.0087,
    "cities": 1.7846,
    "militaryMight": 1.4815,
    "wonders": 20.0,
    "techs": 3.7565,
    "cultureStored": 0.1101,
    "faith": 0.2237,
    "engineScore": 0.3066,
}

# 参与 power 的因子（与 stats jsonl 字段一一对应）
FACTORS = tuple(HAND_WEIGHTS)
# 拟合特征不含 engineScore：它既是 power 因子又曾当标签，会自证。
FIT_FACTORS = tuple(f for f in FACTORS if f != "engineScore")

# 回归用的额外先验列（2026-09-15 用户定）：旧榜分数 × prior_weight 作为一列进设计矩阵，
# 让回归自己学「旧榜信念」的系数；它**不进** HAND_WEIGHTS，也不进 power()。
PRIOR_FACTOR = "priorScore"
PRIOR_WEIGHT_DEFAULT = 0.1
FIT_FEATURES = FIT_FACTORS + (PRIOR_FACTOR,)

# 征服加成（2026-09-14 用户第二次重定）：回到"按城线性"，但单位是奇观数，并加丢城惩罚。
#   占领城市（手里创始文明非我的城）每座 = 3 奇观
#   丢城 > 10 座 → 扣 3 奇观（不设存活条件）
#   灭国 = 1 奇观
#   计数仍按【最终归属】：foundingCiv 永不改变 + citiesCreated 只计一次 →
#   反复拉锯的城只计一次。见 savedata.conquest_metrics。
CAPTURE_WONDERS_PER_CITY = 3.0     # 每占领一座城 = 3 奇观
LOSS_PENALTY_THRESHOLD = 10        # 丢城超过这个数才扣
LOSS_PENALTY_WONDERS = 3.0         # 扣 3 奇观
ELIMINATION_BONUS_WONDERS = 1.0    # 灭一国 = 1 奇观（原 3 奇观 → 降为 1）
ELIMINATION_BONUS = ELIMINATION_BONUS_WONDERS * HAND_WEIGHTS["wonders"]  # = 20 power，供 power 量纲展示

# 性格预设：加权 A/B/C 三族的相对重要性（不是 power 因子权重）
PERSONALITIES = {
    "balanced": {"power": 1.0, "conquest": 1.0, "economy": 1.0},
    "money": {"power": 0.8, "conquest": 0.4, "economy": 2.0},
    "war": {"power": 0.8, "conquest": 3.0, "economy": 0.4},
    "science": {"power": 1.0, "conquest": 0.4, "economy": 1.2},
}


def weights_for(personality: str = "balanced") -> Mapping[str, float]:
    return PERSONALITIES.get(personality, PERSONALITIES["balanced"])


_FAITH_CACHE: dict | None = None


def faith_config() -> dict:
    """信仰型国家名单与信仰权重倍数（来自 arena/faith-nations.json，可手工编辑）。

    判定方式：国家 uniques 或 uniqueTo 特色建筑/改良/单位里出现『信仰产出』写法
    （[+N Faith] / Faith from / +N% Faith）或 faith 正数值，且不是"宗教启用时才可用"这类条件句。
    实测认出 7 国：Celts / Egypt / Ethiopia / MongolEmpire / Qindynasty / Songhai / The Maya。
    """
    global _FAITH_CACHE
    if _FAITH_CACHE is None:
        path = Path(__file__).resolve().parent / "faith-nations.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            _FAITH_CACHE = {"nations": set(data.get("nations") or []),
                            "multiplier": float(data.get("_multiplier", 2.0))}
        except Exception:
            _FAITH_CACHE = {"nations": set(), "multiplier": 1.0}
    return _FAITH_CACHE


def power_weights_for(civ: str | None = None,
                      weights: Mapping[str, float] | None = None) -> dict[str, float]:
    """取 power 因子权重；**信仰型国家**把信仰项乘上倍数（用户定：信仰型国家信仰权重更大）。"""
    w = dict(weights or HAND_WEIGHTS)
    if civ:
        cfg = faith_config()
        if civ in cfg["nations"]:
            w["faith"] = float(w.get("faith", 0.0)) * float(cfg["multiplier"])
    return w


def power(entry: Mapping, weights: Mapping[str, float] | None = None, civ: str | None = None) -> float:
    """单条 stats 记录的国力。weights 缺省用 HAND_WEIGHTS（可被拟合结果替换）。

    `civ` 给了且该国在信仰型名单里 → 自动对其信仰项加权（见 power_weights_for）。
    """
    w = power_weights_for(civ, weights)
    return sum(float(entry.get(factor, 0) or 0) * float(w.get(factor, 0.0)) for factor in w)


def power_series(entries: Sequence[Mapping], weights: Mapping[str, float] | None = None,
                 civ: str | None = None) -> list[float]:
    return [power(e, weights, civ=civ) for e in entries]


def slope(values: Sequence[float]) -> float:
    """最小二乘斜率（值对回合序号）。"""
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(values) / n
    denom = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, values)) / denom if denom else 0.0


def conquest_wonders(captured: int, lost: int = 0, eliminated: int = 0) -> float:
    """征服 → 奇观分。

    每占领一城 +3 奇观；丢城 > 10 座扣 3 奇观（不设存活条件）；灭国 +1 奇观/国。
    可负（只丢不占时）。
    """
    wonders = CAPTURE_WONDERS_PER_CITY * int(captured or 0)
    wonders += ELIMINATION_BONUS_WONDERS * int(eliminated or 0)
    if int(lost or 0) > LOSS_PENALTY_THRESHOLD:
        wonders -= LOSS_PENALTY_WONDERS
    return wonders


def conquest_power_bonus(captured: int, lost: int = 0, eliminated: int = 0,
                         weights: Mapping[str, float] | None = None) -> float:
    """征服加成的**绝对分**（power 量纲）= 奇观分 × 奇观权重。"""
    w = (weights or HAND_WEIGHTS).get("wonders", 20.0)
    return conquest_wonders(captured, lost, eliminated) * float(w)


def conquest_ratio(captured: int, lost: int, eliminated: int, median_power: float,
                   weights: Mapping[str, float] | None = None) -> float:
    """征服加成的**势比**（median 归一）：除以局内 median power，不写死任何 median。"""
    med = median_power if median_power else 1.0
    return conquest_power_bonus(captured, lost, eliminated, weights) / med


def zscores(values: Mapping[str, float]) -> dict[str, float]:
    """(x - mean) / std。全部相等时返回全 0。"""
    if not values:
        return {}
    data = list(values.values())
    mean = statistics.fmean(data)
    sd = statistics.pstdev(data)
    if not sd:
        return {k: 0.0 for k in values}
    return {k: (v - mean) / sd for k, v in values.items()}


def median_series(values: Mapping[str, float]) -> dict[str, float]:
    """x / median。median 为 0 时返回全 1.0（1.0 = 局内中位）。"""
    if not values:
        return {}
    med = statistics.median(list(values.values()))
    if not med:
        return {k: 1.0 for k in values}
    return {k: v / med for k, v in values.items()}


def normalize(values: Mapping[str, float], method: str = "median") -> dict[str, float]:
    """局内归一化。median（默认，1.0=中位）或 z（z-score，0=均值）。"""
    if method == "z":
        return zscores(values)
    if method == "median":
        return median_series(values)
    raise ValueError(f"未知归一化方法: {method}（可选 median / z）")


def latest_at(entries: Sequence[Mapping], turn: int) -> Mapping | None:
    """turn 回合或之前最后一条记录（该回合不存在时向前取）。"""
    usable = [e for e in entries if int(e.get("turn", 0)) <= turn]
    if not usable:
        return None
    return max(usable, key=lambda e: int(e.get("turn", 0)))


def relative_at(
    by_civ: Mapping[str, Sequence[Mapping]],
    civ: str,
    turn: int,
    method: str = "median",
    weights: Mapping[str, float] | None = None,
    captured: int | None = None,
    lost: int | None = None,
) -> tuple[float, int, int, float] | None:
    """某文明在 turn 的（相对势 + 征服加成, 占领量, 灭国数, 征服加成本身）。

    `captured` / `lost` 来自终局存档的征服指标（见 savedata.conquest_metrics）；
    缺省时退回“占领数来自 stats、丢城按 0”——丢城惩罚会测不到（高估）。
    """
    mine = latest_at(by_civ.get(civ, []), turn)
    if mine is None:
        return None
    same_turn = {c: latest_at(rows, turn) for c, rows in by_civ.items()}
    powers = {c: power(e, weights, civ=c) for c, e in same_turn.items() if e is not None}
    if not powers:
        return None
    median_power = statistics.median(list(powers.values()))
    normalized = normalize(powers, method)
    captured_stats = int(mine.get("capturedCities", 0) or 0)
    eliminated = int(mine.get("eliminatedNations", 0) or 0)
    cap = captured_stats if captured is None else int(captured)
    los = 0 if lost is None else int(lost)
    bonus = conquest_ratio(cap, los, eliminated, median_power, weights)
    base = normalized.get(civ, 1.0 if method == "median" else 0.0)
    return base + bonus, cap, eliminated, bonus


def placement(scores: Mapping[str, float], reverse: bool = True) -> dict[str, int]:
    """按分数排名次（1 = 最好）。同分并列（1,2,2,4）。"""
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=reverse)
    ranks: dict[str, int] = {}
    last_value = None
    last_rank = 0
    for index, (name, value) in enumerate(ordered, 1):
        if last_value is not None and value == last_value:
            ranks[name] = last_rank
        else:
            ranks[name] = index
            last_rank = index
            last_value = value
    return ranks


def summarize_civ(
    entries: Sequence[Mapping],
    personality: str = "balanced",
    weights: Mapping[str, float] | None = None,
    conquest_metrics: Mapping | None = None,
    civ: str | None = None,
) -> dict:
    """单文明的三族评分（A 国力增速 / B 征服进度 / C 性格加权）+ 终局快照。

    `conquest_metrics` = 终局存档算出的 {net, alive, source}（见 savedata.conquest_metrics）；
    不传则退回“占领量当净占领、存活按真”，会在 B_征服进度 里标 conquestSource=stats-only。
    """
    ordered = sorted(entries, key=lambda e: int(e.get("turn", 0)))
    if not ordered:
        return {}
    powers = power_series(ordered, weights, civ=civ)
    captured_total = sum(int(e.get("capturedCities", 0) or 0) for e in ordered)
    peak_might = max(int(e.get("militaryMight", 0) or 0) for e in ordered)
    eliminated = int(ordered[-1].get("eliminatedNations", 0) or 0)
    power_slope = slope(powers)
    gold_slope = slope([float(e.get("gold", 0) or 0) + float(e.get("goldPerTurn", 0) or 0) * 10 for e in ordered])
    science_slope = slope([
        float(e.get("techs", 0) or 0) * 10 + float(e.get("sciencePerTurn", 0) or 0) * 5 for e in ordered
    ])
    economy = gold_slope + science_slope
    span = max(1, int(ordered[-1].get("turn", 0)) - int(ordered[0].get("turn", 0)))
    conquest_rate = captured_total / span
    w = weights_for(personality)

    captured_final = None
    lost = 0
    alive = True
    source = "stats-only"
    if conquest_metrics:
        captured_final = conquest_metrics.get("captured")
        lost = int(conquest_metrics.get("lost") or 0)
        alive = bool(conquest_metrics.get("alive", True))
        source = conquest_metrics.get("source", "save")
    captured_used = int(captured_final) if captured_final is not None else captured_total
    wonders = conquest_wonders(captured_used, lost, eliminated)
    power_bonus = conquest_power_bonus(captured_used, lost, eliminated, weights)
    return {
        "turns": (int(ordered[0].get("turn", 0)), int(ordered[-1].get("turn", 0))),
        "A_国力增速": round(power_slope, 2),
        "B_征服进度": {
            "capturedTotal": captured_total,
            "capturedFinal": captured_final,
            "lost": lost,
            "alive": alive,
            "conquestSource": source,
            "conquestRate": round(conquest_rate, 3),
            "peakMight": peak_might,
            "eliminated": eliminated,
            "conquestWonders": wonders,
            "conquestPowerBonus": int(power_bonus),
        },
        "adjustedPower150": round(powers[-1] + power_bonus),
        "经济斜率": round(economy, 2),
        "C_性格加权分": round(
            power_slope * w["power"] + conquest_rate * 100 * w["conquest"] + economy * w["economy"], 2
        ),
        "终局快照": dict(ordered[-1]),
    }


def group_by_civ(rows: Iterable[Mapping]) -> dict[str, list[dict]]:
    """stats jsonl 行 -> {civID: [按回合排序的行]}。"""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("civID", "?")), []).append(dict(row))
    for rows_of in grouped.values():
        rows_of.sort(key=lambda e: int(e.get("turn", 0)))
    return grouped
