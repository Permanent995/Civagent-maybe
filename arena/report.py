"""训练场总报告：覆盖率 + 国家 × 槽位矩阵 + 总排名（rotation / batch 统一入口）。

用法
----
    python arena/report.py                        # 用 arena/index.jsonl（没有就重建）
    python arena/report.py --rebuild              # 强制从 logs/ + SaveFiles 重建索引
    python arena/report.py --kind batch           # 只看智能体对打那批
    python arena/report.py --method z             # 局内归一化用 z-score
    python arena/report.py --weights fitted       # 用手写权重或拟合权重（fit_weights.py 产物）

修复的三件事
------------
1. **覆盖率显式输出**：以前只有 final 没有 stats 的局会被静默跳过，读者看到的是"某几局"的
   排名却以为
是全量。现在任何排名旁边都先打印 `stats / final / outcome / 可评分` 的计数与缺口 gameId。
2. **征服加成被算了两次**：cross_report 先 `rel + bonus`（真 median），再 `mean + 0.2*城 + 90/500*国`
   （硬编码 median），等于加成翻倍且口径不一致。这里只加一次，median 用同一局同一回合的真实值。
3. **多人局没有名次**：evaluate.py 只在 `len(civs)==2` 时给"对比"，6 人轮换局拿不到名次。
   现在按局内名次（placement）汇总，1v1 与多人局同一套。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import fmean

try:  # 包内导入：python -m arena.report / from arena import report
    from . import gamelog, paths, savedata, scoring
except ImportError:  # 直接脚本：python arena/report.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gamelog  # noqa: E402
    import paths  # noqa: E402
    import savedata  # noqa: E402
    import scoring  # noqa: E402


def _conquest_for(record: dict) -> tuple[dict, str]:
    """一局的征服指标：优先终局存档（能算净占领/存活），否则退回 stats 近似。"""
    final = record.get("final")
    if final:
        try:
            if Path(final).exists():
                return savedata.conquest_metrics(final), "save"
        except Exception:
            pass
    return {}, "stats-only"


def _power_turns(last_turn: int, by_civ: dict) -> list[int]:
    """后程检查点：100/150 能取到数据的；否则退回 last_turn。"""
    wanted = [t for t in scoring.POWER_CHECKPOINTS if t <= last_turn]
    usable = [t for t in wanted
              if any(scoring.latest_at(civ_rows, t) is not None for civ_rows in by_civ.values())]
    return usable or [last_turn]


def _game_scores(record: dict, method: str = "median", weights=None) -> dict[str, dict]:
    """一局三轴：powerRel（后程势，不含征服）、conquest 诊断、placement（主轴）。

    placement：存活者按 powerRel 降序排，灭国者一律排在存活者之后。
    score 字段 = powerRel（兼容旧读取方）；征服不再加进 score。
    """
    rows = gamelog.read_stats(record["stats"])
    if not rows:
        return {}
    by_civ = scoring.group_by_civ(rows)
    last_turn = max(int(r.get("turn", 0)) for r in rows)
    checkpoints = _power_turns(last_turn, by_civ)

    metrics, conquest_source = _conquest_for(record)

    result: dict[str, dict] = {}
    for civ, civ_rows in by_civ.items():
        # 采样对（turn, 相对势）：必须成对保存，否则某文明跳过某个检查点时
        # zip(checkpoints, bases) 会把后一个检查点的值贴到前一个检查点的键上。
        samples: list[tuple[int, float]] = []
        captured = 0
        eliminated = 0
        for turn in checkpoints:
            entry = scoring.latest_at(civ_rows, turn)
            if entry is None:
                continue
            same_turn = {c: scoring.latest_at(r, turn) for c, r in by_civ.items()}
            powers = {c: scoring.power(e, weights, civ=c) for c, e in same_turn.items() if e is not None}
            if not powers:
                continue
            samples.append((turn, scoring.normalize(powers, method).get(civ, 0.0)))
            captured = max(captured, int(entry.get("capturedCities", 0) or 0))
            eliminated = max(eliminated, int(entry.get("eliminatedNations", 0) or 0))
        if not samples:
            continue
        final_entry = civ_rows[-1]
        base = fmean([value for _turn, value in samples])
        met = metrics.get(civ) or {}
        alive = bool(met.get("alive", True)) if met else bool(int(final_entry.get("cities", 0) or 0) > 0)
        lost = int(met.get("lost") or 0)
        captured_final = met.get("captured")
        cap_used = int(captured_final) if captured_final is not None else captured
        result[civ] = {
            "base": round(base, 4),
            "powerRel": round(base, 4),
            "score": round(base, 4),
            "captured": captured,
            "capturedFinal": (int(captured_final) if captured_final is not None else None),
            "lost": met.get("lost"),
            "net": met.get("net"),
            "alive": alive,
            "conquestSource": conquest_source,
            "conquestWonders": scoring.conquest_wonders(cap_used, lost, eliminated),
            "eliminated": eliminated,
            "bonus": 0.0,
            "checkpoints": {str(t): round(value, 4) for t, value in samples},
            "finalEngineScore": int(final_entry.get("engineScore", 0) or 0),
            "finalPower": round(scoring.power(final_entry, weights, civ=civ)),
            "lastTurn": int(final_entry.get("turn", 0) or 0),
            "finalCities": int(final_entry.get("cities", 0) or 0),
        }
    living = {c: v["powerRel"] for c, v in result.items() if v["alive"]}
    dead = {c: v["powerRel"] for c, v in result.items() if not v["alive"]}
    ranks_live = scoring.placement(living) if living else {}
    ranks_dead = scoring.placement(dead) if dead else {}
    offset = len(living)
    for civ, rank in ranks_live.items():
        result[civ]["placement"] = rank
    for civ, rank in ranks_dead.items():
        result[civ]["placement"] = offset + rank
    return result


def load_weights(name: str):
    """hand（默认）/ fitted / 或者一个 json 路径。"""
    if not name or name == "hand":
        return None
    if name == "fitted":
        path = paths.ARENA / "fitted-weights.json"
        if not path.exists():
            raise FileNotFoundError(f"没有拟合权重，先跑 python arena/fit_weights.py（缺 {path}）")
        return json.loads(path.read_text(encoding="utf-8"))["weights"]
    return json.loads(Path(name).read_text(encoding="utf-8"))["weights"]


def load_prior(path: Path | None) -> dict[str, float]:
    """读旧榜当先验：nation -> 分数。

    兼容三种产物形状（2026-09-15 扩展，之前只认 report 的 `ranking`）：
      report 输出            {"ranking": [{"nation", "score"|"powerRel"}]}
      nation_strength 输出   {"nations": [{"nation", "combined"|"base"|"混战"}]}
      纯列表                 [{"nation", "finalScore"|"score"}]  (archive/t0-wrap-legacy)
    """
    if path is None or not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("ranking") or doc.get("nations") if isinstance(doc, dict) else doc
    if isinstance(rows, dict):
        rows = rows.get("ranking") or rows.get("nations") or []
    out: dict[str, float] = {}
    for row in rows or []:
        nation = row.get("nation")
        if not nation:
            continue
        for key in ("finalScore", "score", "powerRel", "combined", "base", "混战"):
            if row.get(key) is not None:
                out[nation] = float(row[key])
                break
    return out


def report(records=None, kind: str = "all", method: str = "median", weights=None,
           rebuild: bool = False, prior: dict[str, float] | None = None,
           prior_weight: float = 0.0, require_complete: bool = True,
           maps: list[str] | None = None, mods_filter: list[str] | None = None) -> dict:
    """跨局榜。

    require_complete=True（默认，2026-09-15 用户定）：**只算跑完的局** —— 日志没有
    outcome（被 timeout 切断/半途而废）的一律不进榜。宽松口径，提前胜利算跑完；
    要旧行为（连半局一起算）传 `--allow-partial`。
    maps：只算这些地图的局（None/空 = 全算）。PLAN「不同地图不得混表」靠它落地。
    mods_filter：只算同时启用这些模组的局（与 nation_strength 的 `--mods-filter` 同义）。
    地图 + 模组是两个独立的对照条件（`测试arena.json` 上同时存在
    cathay-only 与 cathay+SongDynasty 两批），要分集必须两个都给。
    """
    if records is None:
        records = gamelog.build_index(index_path=paths.INDEX_PATH) if rebuild else (
            gamelog.load_index() or gamelog.build_index(index_path=paths.INDEX_PATH)
        )
    cov = gamelog.coverage(records)

    selected = [r for r in records if kind == "all" or r.get("kind") == kind]
    if maps:
        selected = [r for r in selected if r.get("map") in maps]
    if mods_filter:
        selected = [r for r in selected
                    if all(m in (r.get("mods") or []) for m in mods_filter)]
    scorable = [r for r in selected if r.get("scorable")]
    if require_complete:
        usable = [r for r in scorable if gamelog.is_complete(r)]
    else:
        usable = list(scorable)
    dropped_incomplete = len(scorable) - len(usable)
    if dropped_incomplete:
        print(f"[report] 完整口径：{dropped_incomplete}/{len(scorable)} 局未跑完，已剔除"
              f"（--allow-partial 可改回旧行为）", flush=True)

    per_nation: dict[str, dict] = {}
    games_out: list[dict] = []
    for record in usable:
        scores = _game_scores(record, method, weights)
        if not scores:
            continue
        games_out.append({
            "tag": record.get("tag"), "kind": record.get("kind"), "gameId": record.get("gameId"),
            "winner": record.get("winner"), "outcome": record.get("outcome"),
            "players": record.get("players"), "civs": scores,
        })
        for civ, info in scores.items():
            agg = per_nation.setdefault(civ, {
                "nation": civ, "games": 0, "scoreSum": 0.0, "placementSum": 0,
                "wins": 0, "engineLeads": 0, "kinds": {}, "weightSum": 0.0,
                "aliveCount": 0, "capturedSum": 0, "eliminatedSum": 0,
            })
            agg["games"] += 1
            agg["weightSum"] += 1.0
            agg["scoreSum"] += info["powerRel"]
            agg["placementSum"] += info["placement"]
            if info["alive"]:
                agg["aliveCount"] += 1
            agg["capturedSum"] += int(info.get("capturedFinal") or info.get("captured") or 0)
            agg["eliminatedSum"] += int(info.get("eliminated") or 0)
            if info["placement"] == 1:
                agg["engineLeads"] += 1
            if record.get("winner") == civ:
                agg["wins"] += 1
            agg["kinds"][record.get("kind")] = agg["kinds"].get(record.get("kind"), 0) + 1

    # 主榜默认不加旧图先验。--prior-weight 显式 >0 时才掺（仅 rotation/all）。
    prior = prior or {}
    applied_prior_weight = 0.0
    if prior and prior_weight > 0 and kind in ("all", "rotation"):
        applied_prior_weight = prior_weight
        for civ, prior_score in prior.items():
            agg = per_nation.setdefault(civ, {
                "nation": civ, "games": 0, "scoreSum": 0.0, "placementSum": 0,
                "wins": 0, "engineLeads": 0, "kinds": {}, "weightSum": 0.0,
                "aliveCount": 0, "capturedSum": 0, "eliminatedSum": 0,
            })
            agg["scoreSum"] += prior_weight * float(prior_score)
            agg["weightSum"] += prior_weight
            agg["kinds"]["prior"] = agg["kinds"].get("prior", 0) + 1

    ranking = []
    for agg in per_nation.values():
        games = agg["games"]
        denom = agg.get("weightSum") or max(1, games)
        mean_place = round(agg["placementSum"] / max(1, games), 2) if games else None
        ranking.append({
            "nation": agg["nation"],
            "meanPlacement": mean_place,
            "powerRel": round(agg["scoreSum"] / denom, 4),
            "score": round(agg["scoreSum"] / denom, 4),
            "survivalRate": round(agg["aliveCount"] / max(1, games), 3) if games else None,
            "captured": agg["capturedSum"],
            "eliminated": agg["eliminatedSum"],
            "games": games,
            "weightSum": round(agg.get("weightSum", float(games)), 3),
            "wins": agg["wins"],
            "firstPlaces": agg["engineLeads"],
            "engineLeads": agg["engineLeads"],
            "kinds": agg["kinds"],
        })
    ranking.sort(key=lambda r: (
        r["meanPlacement"] if r["meanPlacement"] is not None else 99,
        -(r["survivalRate"] or 0),
        -r["powerRel"],
    ))
    for i, row in enumerate(ranking, 1):
        row["rank"] = i

    return {
        "generatedAt": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "method": method,
        "weights": "hand" if weights is None else "custom",
        "priorWeight": applied_prior_weight,
        "priorNations": len(prior) if applied_prior_weight > 0 else 0,
        "rankingAxis": "meanPlacement",
        "coverage": cov,
        "indexed": len(selected),
        "scorable": len(usable),
        "requireComplete": require_complete,
        "maps": list(maps) if maps else None,
        "modsFilter": list(mods_filter) if mods_filter else None,
        "droppedIncomplete": dropped_incomplete,
        "ranking": ranking,
        "games": games_out,
    }


def build_summary(data: dict) -> dict:
    """文件/控制台结尾用：完整榜 + 口径说明（怎么排的）。"""
    ranking = data.get("ranking") or []
    board = [
        {
            "rank": row.get("rank", i),
            "nation": row["nation"],
            "meanPlacement": row.get("meanPlacement"),
            "powerRel": row.get("powerRel", row.get("score")),
            "survivalRate": row.get("survivalRate"),
            "captured": row.get("captured"),
            "eliminated": row.get("eliminated"),
            "games": row["games"],
            "firstPlaces": row.get("firstPlaces", row.get("engineLeads", 0)),
        }
        for i, row in enumerate(ranking, 1)
    ]
    lines = [
        "【怎么排的】",
        f"1) 纳入：kind={data.get('kind')}，可评分 {data.get('scorable')}/{data.get('indexed')} 局"
        f"（须有 arena-stats；coverage.complete={data.get('coverage', {}).get('complete')}）。",
        "2) 三轴分列，禁止揉成一张神秘总分：",
        "   主榜 = 均名次（越小越好）。局内名次：存活者按后程势(100/150)排，灭国垫底。",
        "   势（诊断）= 后程相对势均值（1.0=局内中位）；占城已含在城/人/军里，不再叠征服。",
        "   征服（诊断）= 净占城 / 灭国 / 存活率。",
        "3) 跨局：各轴分别算术平均。旧 wrap 先验与 duel150 默认不进主榜"
        + (f"（本次 priorWeight={data.get('priorWeight')}）" if data.get("priorWeight") else "。"),
        "4) 排序键：meanPlacement 升序 → survivalRate 降序 → powerRel 降序。",
        "5) 势的因子权重："
        + ("手写 HAND_WEIGHTS（scoring.py）" if data.get("weights") == "hand" else str(data.get("weights"))),
        "",
        "【完整总榜】",
    ]
    for row in board:
        mp = row["meanPlacement"]
        mp_s = f"{mp:.2f}" if mp is not None else "-"
        sr = row.get("survivalRate")
        sr_s = f"{sr:.0%}" if sr is not None else "-"
        lines.append(
            f"  #{row['rank']:<2} {row['nation']:<14} place={mp_s}  "
            f"power={row['powerRel']:.4f}  survive={sr_s}  "
            f"cap={row.get('captured')}  elim={row.get('eliminated')}  "
            f"games={row['games']}  #1s={row['firstPlaces']}"
        )
    if board:
        lines += [
            "",
            f"头（均名次最好）：{board[0]['nation']}  |  "
            f"尾：{board[-1]['nation']}",
        ]
    return {
        "howRanked": [
            "scorable games only (stats required)",
            "primary: mean intra-game placement (alive by late powerRel, dead last)",
            "diagnostic: late relative power (checkpoints 100/150), no conquest stacked",
            "diagnostic: captured / eliminated / survivalRate",
            "sort: meanPlacement asc, survivalRate desc, powerRel desc",
        ],
        "board": board,
        "plainText": "\n".join(lines),
    }


def print_report(data: dict) -> None:
    cov = data["coverage"]
    print("=== 覆盖率（排名只对可评分的局有效）===")
    print(f"  索引 {cov['games']} 局 | 有 stats {cov['stats']} ({cov['statsPct']}%) | "
          f"有存档 {cov['final']} | 有胜负 {cov['outcome']} | stats+胜负 {cov['complete']} ({cov['completePct']}%)")
    if cov["missingStats"]:
        print(f"  [!] 有存档但缺 stats（无法评分，需重跑）：{len(cov['missingStats'])} 局 "
              f"{', '.join(cov['missingStats'][:8])}{' ...' if len(cov['missingStats']) > 8 else ''}")
    if cov["interrupted"]:
        print(f"  [!] 有 stats 但没跑完（被 timeout/kill）：{len(cov['interrupted'])} 局 "
              f"{', '.join(cov['interrupted'][:8])}{' ...' if len(cov['interrupted']) > 8 else ''}")
    print(f"  本次纳入评分：{data['scorable']} / {data['indexed']} 局（kind={data['kind']}, "
          f"归一化={data['method']}, 权重={data['weights']}）")
    if data.get("priorWeight"):
        print(f"  旧图先验：{data.get('priorNations', 0)} 国 × weight={data['priorWeight']}")

    print("\n=== 国家总榜（主轴=均名次，势/征服分列）===")
    print(f"  {'#':<3} {'国家':<14} {'均名次':>7} {'势':>8} {'存活':>6} {'占城':>5} {'灭国':>5} {'局数':>5} {'局内第一':>8}")
    for row in data["ranking"]:
        i = row.get("rank") or 0
        mp = row["meanPlacement"]
        mp_s = f"{mp:>7.2f}" if mp is not None else f"{'—':>7}"
        sr = row.get("survivalRate")
        sr_s = f"{sr:>6.0%}" if sr is not None else f"{'—':>6}"
        print(f"  {i:<3} {row['nation']:<14} {mp_s} {row.get('powerRel', row.get('score', 0)):>8.3f} "
              f"{sr_s} {row.get('captured', 0):>5} {row.get('eliminated', 0):>5} "
              f"{row['games']:>5} {row.get('firstPlaces', row.get('engineLeads', 0)):>8}")
    if not data["ranking"]:
        print("  （无可评分对局）")

    summary = data.get("summary") or build_summary(data)
    print("\n=== 总结（口径）===")
    print(summary["plainText"])


def main() -> None:
    parser = argparse.ArgumentParser(description="训练场总报告")
    parser.add_argument("--kind", default="all", choices=["all", "rotation", "batch", "save", "unknown"])
    parser.add_argument("--method", default="median", choices=["median", "z"])
    parser.add_argument("--weights", default="hand", help="hand | fitted | <json 路径>")
    parser.add_argument("--rebuild", action="store_true", help="从 logs/ + SaveFiles 重建索引")
    parser.add_argument("--prior", default=str(paths.ARENA / "archive" / "t0-wrap-legacy" / "conclusions.json"),
                        help="旧图结论 JSON；空字符串关闭")
    parser.add_argument("--prior-weight", type=float, default=0.0,
                        help="旧图结论等效局数权重（主榜默认 0，不掺）")
    parser.add_argument("--allow-partial", action="store_true",
                        help="连没跑完的局（无 outcome）也计分；默认只算跑完的局")
    parser.add_argument("--maps", default=None,
                        help="只算这些地图的局（逗号分隔）；不同图不得混表")
    parser.add_argument("--mods-filter", default=None,
                        help="只算同时启用这些模组的局（逗号分隔）；与 --maps 组合锁定一个对照条件")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    try:
        weights = load_weights(args.weights)
    except FileNotFoundError as exc:
        print(f"[report] {exc}")
        return

    prior_path = Path(args.prior) if args.prior else None
    prior = load_prior(prior_path) if prior_path else {}
    data = report(kind=args.kind, method=args.method, weights=weights, rebuild=args.rebuild,
                  prior=prior, prior_weight=args.prior_weight if prior else 0.0,
                  require_complete=not args.allow_partial,
                  maps=[m.strip() for m in (args.maps or "").split(",") if m.strip()] or None,
                  mods_filter=[m.strip() for m in (getattr(args, "mods_filter", "") or "").split(",") if m.strip()] or None)
    data["summary"] = build_summary(data)
    print_report(data)
    out = Path(args.out) if args.out else paths.ARENA / "ranking.json"
    # summary 放文件末尾字段，方便直接翻到结尾看口径+全榜
    payload = {k: v for k, v in data.items() if k != "summary"}
    payload["summary"] = data["summary"]
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_txt = out.with_name(out.stem + "-summary.txt")
    summary_txt.write_text(data["summary"]["plainText"] + "\n", encoding="utf-8")
    print(f"\n写入 {out}")
    print(f"写入 {summary_txt}")


if __name__ == "__main__":
    main()
