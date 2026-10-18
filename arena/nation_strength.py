"""国家强度组合榜（可复现）。

主榜（2026-09-14 三轴）
----------------------
均名次     越小越好；局内：存活按后程势排，灭国垫底。跨局算术平均。
势         后程相对势（100/150），不含征服加成。诊断列。
征服       占城 / 灭国 / 存活率。诊断列。

混战 + mk 两列仍分开展示；**duel150 默认不进主榜**（孤儿列，仅 --include-duel 附录）。
按地图/模组过滤，禁止混表。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import statistics
import sys
from pathlib import Path

try:  # 包内导入：python -m arena.nation_strength / from arena import nation_strength
    from . import gamelog, paths, scoring
    from . import report as report_mod
except ImportError:  # 直接脚本：python arena/nation_strength.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gamelog  # noqa: E402
    import paths  # noqa: E402
    import scoring  # noqa: E402
    import report as report_mod  # noqa: E402

DUEL_CAP_DEFAULT = 3.0
DUEL_LEGACY = paths.ARENA / "legacy" / "duel150-legacy.json"
FORMULA = "主榜=均名次（混战/mk 分列，duel150 默认不计入）"
OUT_PATH = paths.ARENA / "nation-strength-current.json"


# --------------------------------------------------------------- 三个来源

def melee_scores(records=None, kinds=("rotation",), method: str = "median", weights=None,
               where=None, require_complete: bool = True) -> dict[str, dict]:
    """每局单局分 -> 国家均分（纯本图，不含旧图先验）。

    kinds 可选：rotation / save / batch。
    where 可选的额外过滤（record -> bool），用于按**地图/模组集**拆条件 —— 索引里
    `kind=rotation` 其实混了三种条件（旧图-cathay / 测试图-cathay / 测试图-cathay+SongDynasty）。
    require_complete=True（默认）：只算跑完的局（见 `gamelog.is_complete`）。
    """
    if records is None:
        records = gamelog.load_index() or gamelog.build_index()
    acc: dict[str, list[float]] = {}
    games: dict[str, int] = {}
    for record in records:
        if record.get("kind") not in kinds:
            continue
        if require_complete and not gamelog.is_complete(record):
            continue
        if where is not None and not where(record):
            continue
        # 不依赖跑在前面的 gamelog 标记（那样直接传 record 就筛空了）；读不到 stats 时 _game_scores 会返回 {}
        scores = report_mod._game_scores(record, method, weights)
        if not scores:
            continue
        for civ, info in scores.items():
            acc.setdefault(civ, []).append({
                "score": float(info["powerRel"]),
                "placement": int(info["placement"]),
                "alive": bool(info.get("alive", True)),
                "captured": int(info.get("capturedFinal") or info.get("captured") or 0),
                "eliminated": int(info.get("eliminated") or 0),
            })
            games[civ] = games.get(civ, 0) + 1
    out = {}
    for civ, rows in acc.items():
        out[civ] = {
            "score": round(statistics.fmean(r["score"] for r in rows), 4),
            "meanPlacement": round(statistics.fmean(r["placement"] for r in rows), 2),
            "survivalRate": round(sum(1 for r in rows if r["alive"]) / len(rows), 3),
            "captured": sum(r["captured"] for r in rows),
            "eliminated": sum(r["eliminated"] for r in rows),
            "games": games[civ],
        }
    return out


def _civ_set(stats_path: Path) -> frozenset[str]:
    rows = gamelog.read_stats(stats_path)
    return frozenset(str(r.get("civID")) for r in rows)


def mk_scores(roster_glob: str = "roster-mk*.json", method: str = "median", weights=None,
              mode: str = "checkpoints", roster_dir: Path | None = None) -> dict[str, dict]:
    """按 roster 文件里的国家集合，去 SaveFiles 里找出对应的对局（用文明集合匹配，不信文件名）。

    mode="checkpoints"：与混战列同一套（50/100/150 均值 + 征服加成）—— 默认，口径统一。
    mode="final"：只看末回合归一化 —— 旧 nation-strength-current.json 就是这套
        （实测 Spain T150-only rel=1.759 而旧文件记 1.76，三点均值则是 1.515），
        保留它是为了能逐行复现旧值，不是为了推荐。
    """
    per_nation: dict[str, list[float]] = {}
    detail: dict[str, list[dict]] = {}
    stats_files = sorted(paths.SAVE_DIR.glob("arena-stats-*.jsonl"))
    for roster_path in sorted(Path(roster_dir or paths.ARENA).glob(roster_glob)):
        roster = json.loads(roster_path.read_text(encoding="utf-8"))
        nations = frozenset(e["nation"] for e in roster)
        matched = next((p for p in stats_files if _civ_set(p) == nations), None)
        if matched is None:
            print(f"  [!] {roster_path.name}: 找不到文明集合匹配的 stats（{sorted(nations)}）")
            continue
        record = {"stats": str(matched), "gameId": matched.stem.split("-")[-1],
                  "tag": roster_path.stem, "kind": "mk"}
        if mode == "final":
            rows = gamelog.read_stats(matched)
            by_civ = scoring.group_by_civ(rows)
            turn = max(int(r["turn"]) for r in rows)
            # 终局存档能拿到净占领/存活 → 用真实征服口径；没有就退回 stats 近似
            final = paths.SAVE_DIR / f"arena-final-{record['gameId']}"
            metrics: dict = {}
            if final.exists():
                try:
                    import savedata
                    metrics = savedata.conquest_metrics(final)
                except Exception:
                    metrics = {}
            scores = {}
            for civ in by_civ:
                met = metrics.get(civ) or {}
                got = scoring.relative_at(
                    by_civ, civ, turn, method, weights,
                    captured=met.get("captured"), lost=met.get("lost"),
                )
                if got is None:
                    continue
                entry = scoring.latest_at(by_civ[civ], turn)
                scores[civ] = {
                    "score": round(got[0], 4), "base": round(got[0] - got[3], 4),
                    "captured": got[1], "eliminated": got[2],
                    "finalPower": round(scoring.power(entry, weights, civ=civ)),
                }
        else:
            scores = report_mod._game_scores(record, method, weights)
        for civ, info in scores.items():
            per_nation.setdefault(civ, []).append(float(info["score"]))
            detail.setdefault(civ, []).append({
                "game": roster_path.stem, "gameId": record["gameId"], "score": round(float(info["score"]), 4),
                "base": info["base"], "captured": info["captured"], "power": info["finalPower"],
            })
    return {civ: {"score": round(statistics.fmean(v), 4), "games": len(v), "detail": detail[civ]}
            for civ, v in per_nation.items()}


def load_duel(path: Path | None = None) -> dict[str, float]:
    p = Path(path or DUEL_LEGACY)
    if not p.exists():
        print(f"  [!] 缺 {p}，duel150 一律按 0 处理")
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: float(v) for k, v in (data.get("duel150") or {}).items() if v is not None}


# --------------------------------------------------------------- 组合

def combine(melee: dict, mk: dict, duel: dict, cap: float = DUEL_CAP_DEFAULT,
            zero_as_missing: bool = False, include_duel: bool = False) -> list[dict]:
    """zero_as_missing=True 时，未参加混战的国家按 混战=0 计入均值（旧口径）。

    include_duel=False（默认）：主榜不把 duel150 加进 combined，只作附录列。
    include_duel=True：旧公式 combined = mean(混战势, mk) + 0.1*min(duel, cap)。
    """
    nations = sorted(set(melee) | set(mk) | set(duel))
    rows = []
    for nation in nations:
        f = melee.get(nation, {}).get("score")
        m = mk.get(nation, {}).get("score")
        if f is None and zero_as_missing:
            f = 0.0
        present = [v for v in (f, m) if v is not None]
        base = statistics.fmean(present) if present else 0.0
        raw = duel.get(nation)
        clipped = min(raw, cap) if raw is not None else None
        duel_part = 0.1 * (clipped or 0.0) if include_duel else 0.0
        combined = base + duel_part
        places = [melee.get(nation, {}).get("meanPlacement"), mk.get(nation, {}).get("meanPlacement")]
        places = [p for p in places if p is not None]
        mean_place = round(statistics.fmean(places), 2) if places else None
        surv = melee.get(nation, {}).get("survivalRate")
        legacy_base = None
        if m is not None:
            legacy_base = statistics.fmean([v for v in (0.0 if f is None else f, m) if v is not None])
        rows.append({
            "nation": nation,
            "混战": round(f, 4) if f is not None else None,
            "mk": round(m, 4) if m is not None else None,
            "meanPlacement": mean_place,
            "survivalRate": surv,
            "captured": melee.get(nation, {}).get("captured"),
            "eliminated": melee.get(nation, {}).get("eliminated"),
            "duel150": round(clipped, 4) if clipped is not None else None,
            "duel150Raw": round(raw, 4) if raw is not None else None,
            "base": round(base, 4),
            "combined": round(combined, 4),
            "combinedNoDuel": round(base, 4),
            "duelInflation": round(duel_part, 4),
            "baseLegacyZero": None if legacy_base is None else round(legacy_base, 4),
            "games": {"混战": melee.get(nation, {}).get("games", 0), "mk": mk.get(nation, {}).get("games", 0)},
        })
    rows.sort(key=lambda r: (
        r["meanPlacement"] if r["meanPlacement"] is not None else 99,
        -r["combined"],
        -(r["混战"] or 0),
    ))
    return rows


def build(cap: float = DUEL_CAP_DEFAULT, method: str = "median", weights=None,
          duel_path: Path | None = None, mk_mode: str = "checkpoints",
          zero_as_missing: bool = False, kinds=("rotation",), where=None,
          include_duel: bool = False, require_complete: bool = True) -> dict:
    melee = melee_scores(method=method, weights=weights, kinds=kinds, where=where,
                         require_complete=require_complete)
    mk = mk_scores(method=method, weights=weights, mode=mk_mode)
    duel = load_duel(duel_path) if include_duel else {}
    rows = combine(melee, mk, duel, cap, zero_as_missing=zero_as_missing, include_duel=include_duel)
    return {
        "as_of": dt.date.today().isoformat(),
        "generatedBy": "arena/nation_strength.py",
        "formula": FORMULA,
        "rankingAxis": "meanPlacement",
        "requireComplete": require_complete,
        "duelCap": cap,
        "includeDuel": include_duel,
        "mkMode": mk_mode,
        "zeroAsMissing": zero_as_missing,
        "normalization": method,
        "weightSet": "hand" if weights is None else "custom",
        "sources": {
            "混战": f"arena/index.jsonl 里 kind∈{list(kinds)} 的局（本图，不含旧图先验）",
            "mk": f"roster-mk*.json 指定的补测局（按文明集合匹配 stats；聚合模式={mk_mode}）",
            "duel150": "默认不进主榜；--include-duel 才附录",
        },
        "notes": [
            "主榜 = 均名次（越小越好）。势与征服只做并列诊断，不叠进总分。",
            "duel150 是孤儿列（源数据已丢失），默认不计。",
            "按 --maps / --mods-filter 分集，禁止不同图混表。",
        ],
        "nations": rows,
    }


def render_markdown(data: dict) -> str:
    """把 build() 的结果渲染成可复现的 markdown 榜（取代手写维护）。

    主榜按 meanPlacement（combine 已排好）；势/征服/duel150 全部只作并列诊断列。
    """
    rows = data.get("nations") or []
    weights = scoring.HAND_WEIGHTS
    order = ["militaryMight", "engineScore", "population", "techs", "cultureStored", "wonders", "faith", "cities"]
    zh = {"militaryMight": "军力", "engineScore": "引擎", "population": "人口", "techs": "科技",
          "cultureStored": "文化", "wonders": "奇观", "faith": "信仰", "cities": "城市"}
    weight_line = " · ".join(f"{zh[k]} {weights[k]:g}" for k in order if k in weights)

    out = [
        f"# 国家强度总排名（{data.get('as_of', '')}）",
        "",
        "- 主榜 = **均名次**（越小越好）。局内：存活者按后程势排，灭国垫底；跨局算术平均。",
        f"- 公式：`{data.get('formula', FORMULA)}`，includeDuel={data.get('includeDuel')}，"
        f"归一化={data.get('normalization')}，mkMode={data.get('mkMode')}",
        "- 势（混战/mk）与征服（占城/灭国/存活）**只做诊断列**，不叠进总分。",
        "- duel150 是孤儿列（源数据已丢失），默认不计入主榜；仅 `--include-duel` 时进 combined。",
        f"- 因子权重（{data.get('weightSet')}）：{weight_line}",
        "- 军力刻意不计派系平衡修正（用裁决器简化定义 Σ max(基础强度, 远程强度)）；信仰型国家信仰项 ×2",
        "- 征服口径（诊断）：每占一城 +3 奇观；丢城 > 10 座 −3 奇观；灭国 +1 奇观（1 奇观 = 20 国力，按最终归属计）",
        f"- 分集：{data.get('sources', {}).get('混战', '')}；要按图/模组分集需显式 `--maps` / `--mods-filter`（默认合算）",
        "",
        "| # | 国家 | 均名次 | 势混战 | mk | 存活 | 占城 | 灭国 | 局数(混战/mk) | duel150 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        def fmt(v, spec="g"):
            return "—" if v is None else format(v, spec)
        mp = fmt(r.get("meanPlacement"), ".2f")
        sr = "—" if r.get("survivalRate") is None else f"{r['survivalRate']:.0%}"
        games = r.get("games") or {}
        g = f"{games.get('混战', 0)}/{games.get('mk', 0)}"
        out.append(f"| {i} | {r['nation']} | {mp} | {fmt(r.get('混战'), '.4f')} | {fmt(r.get('mk'), '.4f')} "
                   f"| {sr} | {fmt(r.get('captured'))} | {fmt(r.get('eliminated'))} | {g} | {fmt(r.get('duel150'), '.4f')} |")
    out.append("")
    out.append("> 本文件由 `python arena/nation_strength.py --write --md arena/ranking-total.md` 生成，请勿手改。")
    return "\n".join(out) + "\n"


def backup_existing() -> Path | None:
    if not OUT_PATH.exists():
        return None
    stamp = dt.datetime.fromtimestamp(OUT_PATH.stat().st_mtime).strftime("%Y%m%d-%H%M")
    target = paths.ARENA / "archive" / f"nation-strength-current-{stamp}.json"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUT_PATH, target)
    return target


def compare_with_old(new: dict, old_path: Path = OUT_PATH) -> None:
    if not old_path.exists():
        print("（没有旧文件可对比）")
        return
    old = {r["nation"]: r for r in json.loads(old_path.read_text(encoding="utf-8")).get("nations", [])}
    print(f"\n=== 与旧 {old_path.name} 逐行对比 ===")
    print(f"  {'国家':<14}{'混战旧':>8}{'混战新':>8}{'mk旧':>8}{'mk新':>8}{'duel旧':>8}{'duel新':>8}{'comb旧':>8}{'comb新':>8}")
    for r in new["nations"]:
        o = old.get(r["nation"], {})
        def cell(key, newkey=None):
            ov = o.get(key)
            if ov is None and key == "混战":
                ov = o.get("ffa")   # 旧文件（2026-09-13 及更早）用的是 ffa 键
            nv = r.get(newkey or key)
            return ("" if ov is None else f"{ov:g}"), ("" if nv is None else f"{nv:g}")
        f_o, f_n = cell("混战"); m_o, m_n = cell("mk"); d_o, d_n = cell("duel150"); c_o, c_n = cell("combined")
        print(f"  {r['nation']:<14}{f_o:>8}{f_n:>8}{m_o:>8}{m_n:>8}{d_o:>8}{d_n:>8}{c_o:>8}{c_n:>8}")


def main() -> None:
    parser = argparse.ArgumentParser(description="国家强度组合榜（可复现）")
    parser.add_argument("--duel-cap", type=float, default=DUEL_CAP_DEFAULT, help=f"duel150 截断上限（默认 {DUEL_CAP_DEFAULT}）")
    parser.add_argument("--duel", default=None, help="替换冻结的 duel150 文件")
    parser.add_argument("--method", default="median", choices=["median", "z"])
    parser.add_argument("--weights", default="hand")
    parser.add_argument("--write", action="store_true", help="写 nation-strength-current.json（先备份旧文件）")
    parser.add_argument("--compare", action="store_true", help="与旧文件逐行对比")
    parser.add_argument("--kinds", default="rotation",
                        help="混战 纳入哪些来源（逗号分隔）：rotation / save / batch")
    parser.add_argument("--maps", default=None,
                        help="只算这些地图（逗号分隔，如 测试arena.json）；默认全部")
    parser.add_argument("--mods-filter", default=None,
                        help="只算启用包含这些模组的局（逗号分隔，如 Song Dynasty）")
    parser.add_argument("--mk-final-only", action="store_true",
                        help="mk 只看末回合（旧口径；默认与 混战 同口径 50/100/150 均值）")
    parser.add_argument("--legacy-zero", action="store_true",
                        help="未参加 混战 的国家按 混战=0 计入均值（旧口径，会把 mk 除 2）")
    parser.add_argument("--include-duel", action="store_true",
                        help="把冻结的 duel150 按 0.1×cap 加进 combined（旧口径；默认不加）")
    parser.add_argument("--out", default=None)
    parser.add_argument("--allow-partial", action="store_true",
                        help="连没跑完的局（无 outcome）也计分；默认只算跑完的局")
    parser.add_argument("--md", default=None,
                        help="把榜单渲染成 markdown 写到该路径（如 arena/ranking-total.md）")
    args = parser.parse_args()

    weights = report_mod.load_weights(args.weights)
    duel_path = Path(args.duel) if args.duel else None
    kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
    want_maps = [m.strip() for m in (args.maps or "").split(",") if m.strip()]
    want_mods = [m.strip() for m in (args.mods_filter or "").split(",") if m.strip()]

    def where(record: dict) -> bool:
        if want_maps and record.get("map") not in want_maps:
            return False
        if want_mods:
            mods = record.get("mods") or []
            return all(m in mods for m in want_mods)
        return True

    data = build(cap=args.duel_cap, method=args.method, weights=weights, duel_path=duel_path,
                 mk_mode="final" if args.mk_final_only else "checkpoints",
                 zero_as_missing=args.legacy_zero, kinds=kinds, where=where,
                 include_duel=args.include_duel,
                 require_complete=not args.allow_partial)

    print(f"{data['formula']}   归一化={data['normalization']}  includeDuel={data['includeDuel']}")
    print(f"\n{'#':<4}{'国家':<14}{'均名次':>8}{'势混战':>8}{'mk':>8}{'存活':>6}{'占城':>5}{'局数':>5}")
    for i, r in enumerate(data["nations"], 1):
        fmt = lambda v: "" if v is None else f"{v:g}"  # noqa: E731
        mp = "" if r.get("meanPlacement") is None else f"{r['meanPlacement']:.2f}"
        sr = "" if r.get("survivalRate") is None else f"{r['survivalRate']:.0%}"
        games = (r.get("games") or {}).get("混战", 0)
        print(f"{i:<4}{r['nation']:<14}{mp:>8}{fmt(r['混战']):>8}{fmt(r['mk']):>8}"
              f"{sr:>6}{fmt(r.get('captured')):>5}{games:>5}")

    if not args.legacy_zero:
        halved = [r for r in data["nations"]
                  if r["baseLegacyZero"] is not None and abs(r["baseLegacyZero"] - r["base"]) > 1e-9]
        if halved:
            print("\n[!] 这些国没参加 混战，旧口径把它们的 mk 除以 2（--legacy-zero 可复现该口径）：")
            for r in halved:
                print(f"    {r['nation']:<14} mk={r['mk']:g} → 新 base={r['base']:.3f} / 旧 base={r['baseLegacyZero']:.3f}")

    if args.compare:
        compare_with_old(data, Path(args.out) if args.out else OUT_PATH)

    if args.write or args.out:
        out = Path(args.out) if args.out else OUT_PATH
        if out == OUT_PATH:
            saved = backup_existing()
            if saved:
                print(f"\n旧文件已备份 -> {saved}")
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"写入 {out}")
    else:
        print("\n（未写文件；要写加 --write 或 --out <路径>）")

    if args.md:
        md_path = Path(args.md)
        md_path.write_text(render_markdown(data), encoding="utf-8")
        print(f"写入 {md_path}")


if __name__ == "__main__":
    main()
