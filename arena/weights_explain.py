"""把「权重」翻译成「占比」—— 回答"权重和占比怎么对不上"。

一句话
------
    权重 = 每 1 单位「因子原始值」给多少分
    占比 = 该因子的总贡献 ÷ 全部因子贡献之和

两者看着对不上，是因为**各因子的原始量差了几个数量级**：

    因子原始总量（330 个文明-局）      权重      贡献 = 权重 × 总量
    军力 militaryMight   284,907      1.48     422,000  → 59%
    奇观 wonders           1,407     20.00      28,140  →  4%

所以"奇观权重 20、军力权重 1.48"**并不意味着奇观更重要** —— 恰恰相反，军力占 59%。
就像买菜：苹果 20 元/斤但只买 1 斤，大米 1.5 元/斤但买 200 斤，账单上大米才占大头。

用法
----
    python arena/weights_explain.py                       # 当前 HAND_WEIGHTS
    python arena/weights_explain.py --weights arena/weights-legacy-mil15.json
    python arena/weights_explain.py --civ MongolEmpire    # 拆解某个真实文明
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Mapping

try:  # 包内导入：python -m arena.weights_explain / from arena import weights_explain
    from . import gamelog, paths, scoring
except ImportError:  # 直接脚本：python arena/weights_explain.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gamelog  # noqa: E402
    import paths  # noqa: E402
    import scoring  # noqa: E402

CN = {
    "militaryMight": "军力", "population": "人口", "techs": "科技", "cultureStored": "文化",
    "wonders": "奇观", "faith": "信仰", "engineScore": "引擎分", "cities": "城市",
}


def load_weights(name: str) -> dict:
    if not name or name == "hand":
        return dict(scoring.HAND_WEIGHTS)
    path = Path(name)
    if not path.is_absolute():
        path = paths.ARENA / name
    return json.loads(path.read_text(encoding="utf-8"))["weights"]


def collect(weights: dict, civ: str | None = None):
    """扫全部局：累计各因子原始量；顺手挑一个真实文明做拆解样例。

    未指定 --civ 时自动挑**全样本里 power 最接近中位**的那个文明当"典型国家"。
    """
    raw = {f: 0.0 for f in weights}
    n = 0
    sample = None
    pool: list[tuple[float, str, Mapping, dict]] = []
    for rec in gamelog.load_index():
        if rec.get("kind") not in ("rotation", "save") or not rec.get("scorable"):
            continue
        rows = gamelog.read_stats(rec["stats"])
        if not rows:
            continue
        for c, rs in scoring.group_by_civ(rows).items():
            e = rs[-1]
            w = scoring.power_weights_for(c, weights)     # 含信仰型加成
            n += 1
            for f in raw:
                raw[f] += float(e.get(f, 0) or 0) * 1.0   # 只累计原始量
            pool.append((scoring.power(e, w), c, e, w))    # w 已含加成，不能再传 civ 否则重复乘
            if civ and c == civ:
                sample = (c, e, w)
    if sample is None and pool:
        pool.sort(key=lambda t: t[0])
        _, c, e, w = pool[len(pool) // 2]                 # 中位代表
        sample = (c, e, w)
    return raw, n, sample


def main() -> None:
    parser = argparse.ArgumentParser(description="把权重翻译成占比")
    parser.add_argument("--weights", default="hand", help="hand 或 json 路径")
    parser.add_argument("--civ", default=None, help="拆解某个文明（默认自动挑该局国力最高的）")
    args = parser.parse_args()

    weights = load_weights(args.weights)
    raw, n, sample = collect(weights, args.civ)
    total_raw = sum(raw.values())

    print(f"样本：{n} 个文明-局的**末回合快照**（只统计原始量，不加权）\n")
    print("=== ① 各因子原始量 vs 权重 vs 占比 ===")
    print(f"  {'因子':<8}{'原始总量':>12}{'占比(原始)':>11}{'权重':>10}{'贡献=权重×量':>14}{'最终占比':>10}")
    contrib = {f: weights[f] * raw[f] for f in weights}
    tot = sum(contrib.values())
    for f, v in sorted(raw.items(), key=lambda kv: -contrib[kv[0]]):
        print(f"  {CN.get(f, f):<8}{v:>12,.0f}{100*v/total_raw:>10.1f}%{weights[f]:>10.4g}"
              f"{contrib[f]:>14,.0f}{100*contrib[f]/tot:>9.1f}%")
    print(f"  {'合计':<8}{total_raw:>12,.0f}{100.0:>10.1f}%{'':>10}{tot:>14,.0f}{100.0:>9.1f}%")

    print("\n=== ② 真实文明拆解（看得见每一分从哪来）===")
    if sample is None:
        print("  （没找到样例；用 --civ 指定一个国家）")
    else:
        c, e, w = sample
        print(f"  {c}（局内末回合快照）  权重集 = {args.weights}")
        print(f"  {'因子':<8}{'因子值':>10}{'权重':>10}{'得 分 = 值×权重':>16}{'占总分':>9}")
        parts = []
        for f in w:
            val = float(e.get(f, 0) or 0)
            parts.append((f, val, w[f], val * w[f]))
        ptot = sum(p[3] for p in parts)
        for f, val, ww, sc in sorted(parts, key=lambda t: -t[3]):
            print(f"  {CN.get(f, f):<8}{val:>10,.1f}{ww:>10.4g}{sc:>16,.1f}{100*sc/ptot:>8.1f}%")
        print(f"  {'总分':<8}{'':>10}{'':>10}{ptot:>16,.1f}{100.0:>8.1f}%")
        print(f"  → 即 scoring.power() 的值 = {tot and scoring.power(e, weights, civ=c):,.1f}")

    print("\n（记住：**只有「占比」有意义，权重数字本身的大小没有意义** ——")
    print("  它只是乘在原始量上的系数；改占比请直接说目标百分比，我反解权重。）")


if __name__ == "__main__":
    main()
