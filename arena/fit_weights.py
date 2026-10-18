"""自动权重：让权重从数据里来，而不是手写。

**为什么需要**（用户问的"自动权重算法"）

`evaluate.py` / `cross_report.py` 的权重是手写常量（人口×8、城市×8、军力×15、奇观×20、科技×14，
2026-09-14 用户改过一轮；旧值见 `arena/weights-legacy-hand.json`），
`--personality` 只是 4 组预设。`evaluate.py` 的 docstring 自己写着：
"不裁决唯一真理：三套并列输出，哪套能预测'哪侧赢'由批量数据回答。" —— 这一步以前没人做。
于是"哪套权重更能预测强弱"这个问题在本仓库里一直没有答案，只有手写权重的一种说法。

**做法（避免自证循环）**

标签用**引擎自己的判定** `engineScore`（Unciv `calculateTotalScore()`）—— 它独立于我们的
power 公式，不是我们自己权重的复述。每局取 engineScore 最高的文明为 1，其余为 0。

- 特征：8 个因子在局内的 z-score（消除"局与局之间量级不同"）
- 模型：L2 正则逻辑回归，全批量梯度下降（numpy，无需 sklearn/scipy）
- 验证：**留一局交叉验证**（leave-one-game-out），对比三种权重的预测准确率：
  - hand   手写权重（现状）
  - equal  等权
  - fitted 拟合权重
  这才是"哪套权重更能预测强弱"的诚实回答。

产物：`arena/fitted-weights.json`（`report.py --weights fitted` 会用它）。
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path
from statistics import fmean

try:  # 包内导入：python -m arena.fit_weights / from arena import fit_weights
    from . import gamelog, paths, scoring
    from . import report as report_mod
except ImportError:  # 直接脚本：python arena/fit_weights.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gamelog  # noqa: E402
    import paths  # noqa: E402
    import scoring  # noqa: E402
    import report as report_mod  # noqa: E402


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float:
    """秩相关（无 scipy 依赖）。"""
    if len(a) < 2 or len(a) != len(b):
        return 0.0
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = fmean(ra), fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def build_dataset(records=None, checkpoint: int | None = None,
                  require_complete: bool = True,
                  prior: dict[str, float] | None = None,
                  prior_weight: float = scoring.PRIOR_WEIGHT_DEFAULT,
                  ) -> tuple[list[list[float]], list[int], list[int]]:
    """-> (X 特征, y 标签, game 分组号)。

    特征列 = `scoring.FIT_FACTORS` 的局内 z-score，**再加一列先验**：
    `prior_weight × 旧榜分数`（2026-09-15 用户定）。先验列不做 z-score —— 它是
    跨局同一个「旧信念」，z-score 会把它抹成 0。
    require_complete=True：只拿跑完的局训练（被 timeout 砍的半局标签不可信）。
    """
    import numpy as np  # noqa: F401  (延迟导入，未装 numpy 时给出清晰错误)

    prior = prior or {}
    if records is None:
        records = gamelog.load_index() or gamelog.build_index()
    X: list[list[float]] = []
    y: list[int] = []
    groups: list[int] = []
    game_index = 0
    for record in records:
        if not record.get("scorable"):
            continue
        if require_complete and not gamelog.is_complete(record):
            continue
        rows = gamelog.read_stats(record["stats"])
        if not rows:
            continue
        by_civ = scoring.group_by_civ(rows)
        if len(by_civ) < 2:
            continue
        last_turn = max(int(r.get("turn", 0)) for r in rows)
        turn = checkpoint if checkpoint and checkpoint <= last_turn else last_turn

        entries = {}
        for civ, civ_rows in by_civ.items():
            entry = scoring.latest_at(civ_rows, turn)
            if entry is not None:
                entries[civ] = entry
        if len(entries) < 2:
            continue

        # 局内 z-score：同一因子在局内各文明间标准化（不含 engineScore）
        z_by_factor = {}
        for factor in scoring.FIT_FACTORS:
            values = {c: float(e.get(factor, 0) or 0) for c, e in entries.items()}
            z_by_factor[factor] = scoring.zscores(values)

        # 标签：150（或末回合）仍存活且城数最多者；与 power 公式、engineScore 脱钩
        alive = {c: e for c, e in entries.items() if int(e.get("cities", 0) or 0) > 0}
        pool = alive or entries
        leader = max(pool, key=lambda c: (
            int(pool[c].get("cities", 0) or 0),
            int(pool[c].get("population", 0) or 0),
        ))

        for civ in entries:
            X.append([z_by_factor[f][civ] for f in scoring.FIT_FACTORS]
                     + [prior_weight * float(prior.get(civ, 0.0))])
            y.append(1 if civ == leader else 0)
            groups.append(game_index)
        game_index += 1

    return X, y, groups


def fit_logistic(X, y, l2: float = 1.0, steps: int = 4000, lr: float = 0.3) -> list[float]:
    """全批量梯度下降的 L2 逻辑回归（带截距）。返回 8 维因子权重。"""
    import numpy as np

    Xa = np.asarray(X, dtype=float)
    ya = np.asarray(y, dtype=float)
    n, d = Xa.shape
    # 标准化特征便于统一步长
    mean = Xa.mean(axis=0)
    std = Xa.std(axis=0)
    std[std == 0] = 1.0
    Z = (Xa - mean) / std
    Z = np.hstack([Z, np.ones((n, 1))])

    theta = np.zeros(d + 1)
    for _ in range(steps):
        logits = Z @ theta
        pred = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
        grad = Z.T @ (pred - ya) / n
        grad[:-1] += l2 * theta[:-1] / n
        theta -= lr * grad
    return list(theta[:-1])


def _accuracy(records, weights, fitted_vector=None,
              prior: dict[str, float] | None = None,
              prior_weight: float = scoring.PRIOR_WEIGHT_DEFAULT) -> dict:
    """留一局交叉验证准确率 + 与 engineScore 的秩相关（只在训练集上评估对齐度）。"""
    import numpy as np

    if fitted_vector is not None:
        X, y, groups = build_dataset(records, prior=prior, prior_weight=prior_weight)
        if not X:
            return {"accuracy": 0.0, "folds": 0, "spearman": 0.0}
        Xa = np.asarray(X, dtype=float)
        correct = total = 0
        for g in sorted(set(groups)):
            test = [i for i, gi in enumerate(groups) if gi == g]
            train = [i for i, gi in enumerate(groups) if gi != g]
            if not train or len(set(y[i] for i in train)) < 2:
                continue
            mean = Xa[train].mean(axis=0)
            std = Xa[train].std(axis=0)
            std[std == 0] = 1.0
            w = fit_logistic(((Xa[train] - mean) / std).tolist(), [y[i] for i in train])
            for i in test:
                z = (Xa[i] - mean) / std
                if (z @ np.asarray(w) > 0) == (y[i] == 1):
                    correct += 1
                total += 1
        return {"accuracy": round(100.0 * correct / total, 1) if total else 0.0, "folds": len(set(groups)),
                "spearman": 0.0}

    # 固定权重：在同一局内比较"我们算的名次"和"引擎的名次"
    correlations = []
    correct = total = 0
    for record in records:
        if not record.get("scorable"):
            continue
        rows = gamelog.read_stats(record["stats"])
        if not rows:
            continue
        by_civ = scoring.group_by_civ(rows)
        if len(by_civ) < 2:
            continue
        last = {c: r[-1] for c, r in by_civ.items()}
        ours = [scoring.power(last[c], {k: v for k, v in (weights or {}).items() if k != "engineScore"})
                for c in last]
        alive = {c: e for c, e in last.items() if int(e.get("cities", 0) or 0) > 0}
        pool = alive or last
        winner = max(pool, key=lambda c: (
            int(pool[c].get("cities", 0) or 0),
            int(pool[c].get("population", 0) or 0),
        ))
        theirs = [float(int(last[c].get("cities", 0) or 0)) for c in last]
        correlations.append(spearman(ours, theirs))
        pick = max(last, key=lambda c: scoring.power(
            last[c], {k: v for k, v in (weights or scoring.HAND_WEIGHTS).items() if k != "engineScore"}))
        correct += 1 if pick == winner else 0
        total += 1
    return {
        "accuracy": round(100.0 * correct / total, 1) if total else 0.0,
        "folds": total,
        "spearman": round(fmean(correlations), 3) if correlations else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="从对局结果拟合 power 权重")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--l2", type=float, default=1.0)
    parser.add_argument("--prior", default=str(paths.ARENA / "prior-current.json"),
                        help="旧榜 JSON（nation_strength 的 nations / report 的 ranking 都能读）；空串关闭")
    parser.add_argument("--prior-weight", type=float, default=scoring.PRIOR_WEIGHT_DEFAULT,
                        help=f"旧榜分数乘数（默认 {scoring.PRIOR_WEIGHT_DEFAULT}；0 = 不加先验列）")
    parser.add_argument("--allow-partial", action="store_true",
                        help="连没跑完的局也拿来训练；默认只算跑完的局")
    parser.add_argument("--maps", default=None,
                        help="只用这些地图的局（逗号分隔）；不同图不得混表")
    args = parser.parse_args()

    try:
        import numpy  # noqa: F401
    except ImportError:
        print("[fit] 需要 numpy（本机应有；pip install numpy）")
        return

    records = gamelog.build_index() if args.rebuild else (gamelog.load_index() or gamelog.build_index())
    maps = [m.strip() for m in (args.maps or "").split(",") if m.strip()]
    if maps:
        before = len(records)
        records = [r for r in records if r.get("map") in maps]
        print(f"  分集：只留 map∈{maps} 的局（{len(records)}/{before}）")
    cov = gamelog.coverage(records)
    print(f"=== 数据 ===")
    print(f"  索引 {cov['games']} 局 | 有 stats {cov['stats']} | 可评分 {sum(1 for r in records if r.get('scorable'))}")
    if cov["missingStats"]:
        print(f"  [!] 缺 stats 的局 {len(cov['missingStats'])} 个（不入拟合）：{', '.join(cov['missingStats'][:6])}")

    X, y, groups = build_dataset(records, require_complete=not args.allow_partial)
    if len(X) < 8:
        print(f"  [x] 样本太少（{len(X)} 行 / {len(set(groups))} 局），先多跑几局再拟合")
        return
    print(f"  样本 {len(X)} 行 / {len(set(groups))} 局，正例 {sum(y)}"
          f"（标签 = 末回合存活且城数最多者，不含 engineScore）")

    # 旧榜分数 × 0.1 作为额外特征列（用户 2026-09-15 定）。
    prior_path = Path(args.prior) if args.prior else None
    prior = report_mod.load_prior(prior_path) if prior_path else {}
    prior_weight = args.prior_weight if prior else 0.0
    if prior:
        print(f"  先验：{prior_path.name} 里 {len(prior)} 国 × {prior_weight:g}"
              f"（命中本批文明 {sum(1 for c in scoring.FIT_FACTORS)} 列之外的第 {len(scoring.FIT_FEATURES)} 列）")
    else:
        print("  先验：无（--prior 未给或读不到）")
    n_factor = len(scoring.FIT_FACTORS)

    Xp, yp, gp = build_dataset(records, require_complete=not args.allow_partial,
                               prior=prior, prior_weight=prior_weight)
    fitted = fit_logistic(Xp, yp, l2=args.l2)
    fitted_weights = {f: round(v, 4) for f, v in zip(scoring.FIT_FACTORS, fitted[:n_factor])}
    prior_coef = round(fitted[n_factor], 4) if len(fitted) > n_factor else None
    equal_weights = {f: 1.0 for f in scoring.FIT_FACTORS}

    print("\n=== 拟合权重 vs 手写权重 ===")
    print(f"  {'因子':<14} {'手写':>8} {'拟合':>8} {'等权':>6}")
    for factor in scoring.FIT_FACTORS:
        print(f"  {factor:<14} {scoring.HAND_WEIGHTS[factor]:>8.2f} {fitted_weights[factor]:>8.3f} {1.0:>6.2f}")

    print("\n=== 谁能预测局内存活城数最多者（与 engineScore 脱钩）===")
    hand_eval = _accuracy(records, scoring.HAND_WEIGHTS)
    equal_eval = _accuracy(records, equal_weights)
    fitted_eval = _accuracy(records, None, fitted_vector=fitted,
                            prior=prior, prior_weight=prior_weight)
    no_prior_eval = _accuracy(records, None, fitted_vector=fit_logistic(
        *build_dataset(records, require_complete=not args.allow_partial)[:2], l2=args.l2),
        prior=None, prior_weight=0.0)
    print(f"  {'权重':<14} {'命中率':>8} {'局数':>5} {'秩相关':>8}")
    print(f"  {'hand':<14} {hand_eval['accuracy']:>7.1f}% {hand_eval['folds']:>5} {hand_eval['spearman']:>8.3f}")
    print(f"  {'equal':<14} {equal_eval['accuracy']:>7.1f}% {equal_eval['folds']:>5} {equal_eval['spearman']:>8.3f}")
    print(f"  {'fitted':<14} {fitted_eval['accuracy']:>7.1f}% {fitted_eval['folds']:>5} {'(LOO)':>8}")
    print(f"  {'fitted-无先验':<14} {no_prior_eval['accuracy']:>7.1f}% {no_prior_eval['folds']:>5} {'(LOO)':>8}")
    if prior_coef is not None:
        gain = fitted_eval['accuracy'] - no_prior_eval['accuracy']
        print(f"  先验列系数：{prior_coef:+.4f}（正=旧榜信念与「城数最多」同向）；"
              f"加入先验的命中率变化 {gain:+.1f} 个百分点")

    best = max((("hand", hand_eval), ("equal", equal_eval), ("fitted", fitted_eval)),
               key=lambda kv: kv[1]["accuracy"])
    print(f"\n  结论：本批数据里 **{best[0]}** 预测最准（{best[1]['accuracy']}%）。"
          f"{'手写权重仍然更优，拟合结果仅供参考。' if best[0] == 'hand' else '可考虑改用拟合权重。'}")

    out = paths.ARENA / "fitted-weights.json"
    out.write_text(json.dumps({
        "weights": fitted_weights,
        "hand": scoring.HAND_WEIGHTS,
        "validation": {"hand": hand_eval, "equal": equal_eval,
                       "fitted": fitted_eval, "fittedNoPrior": no_prior_eval},
        "samples": len(Xp),
        "games": len(set(gp)),
        "label": "末回合存活且城数最多者",
        "factors": list(scoring.FIT_FACTORS),
        "features": list(scoring.FIT_FEATURES),
        "prior": {"path": str(prior_path) if prior_path else None,
                  "nations": len(prior), "weight": prior_weight, "coefficient": prior_coef},
        "l2": args.l2,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写入 {out}（用 python arena/report.py --weights fitted 查看对应排名）")


if __name__ == "__main__":
    main()
