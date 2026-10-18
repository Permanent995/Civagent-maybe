"""轮换赛交叉报告：国家 × 槽位表现矩阵（rotation）。

用法：python arena/cross_report.py [--method median|z] [--weights hand|fitted] [--rebuild]

改动（相对旧版）
----------------
- 旧版靠 `%TEMP%/rotation-{tag}.log` 正则抓 gameId 找 stats，日志一被清就全部 SKIP，
  报告静默缩水。现在读 `arena/index.jsonl`（runner 每局写入，日志也在仓库内）。
- 旧版把征服加成算了两次（rel+bonus 后再 mean+0.2*城+90/500*国，且 median 写死 500）。
  现在加成只在 report._game_scores 里加一次，median 用当回合真实值。
- 旧版自己抄了一份 power 公式。现在统一走 scoring.py。
排名逻辑复用 report.py，保持"总排名"只有一处实现。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:  # 包内导入：python -m arena.cross_report / from arena import cross_report
    from . import gamelog, paths
    from . import report as report_mod
except ImportError:  # 直接脚本：python arena/cross_report.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gamelog  # noqa: E402
    import paths  # noqa: E402
    import report as report_mod  # noqa: E402


def slot_of(record: dict) -> dict[str, str]:
    """由 roster 文件推出 nation -> 槽位（rotation 才有）。"""
    tag = record.get("tag")
    if not tag:
        return {}
    roster_path = paths.ARENA / f"roster-{tag}.json"
    slots_path = paths.ARENA / "slots-64x64.json"
    if not roster_path.exists() or not slots_path.exists():
        return {}
    slots = json.loads(slots_path.read_text(encoding="utf-8"))
    by_pos = {(s["x"], s["y"]): s["slot"] for s in slots}
    roster = json.loads(roster_path.read_text(encoding="utf-8"))
    return {e["nation"]: by_pos.get((e["x"], e["y"]), "?") for e in roster}


def main() -> None:
    parser = argparse.ArgumentParser(description="轮换赛交叉报告")
    parser.add_argument("--method", default="median", choices=["median", "z"])
    parser.add_argument("--weights", default="hand")
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()

    weights = report_mod.load_weights(args.weights)
    records = gamelog.build_index() if args.rebuild else (gamelog.load_index() or gamelog.build_index())
    data = report_mod.report(records, kind="rotation", method=args.method, weights=weights)

    report_mod.print_report(data)

    slot_types = {}
    spawn_report = paths.ARENA / "spawn-report.json"
    if spawn_report.exists():
        slot_types = {r["slot"]: r.get("评级", "?") for r in json.loads(spawn_report.read_text(encoding="utf-8"))}

    print("\n=== 国家 × 槽位表现矩阵（局内归一化均分，1.0=局内中位）===")
    matrix: dict[str, list[dict]] = {}
    for game in data["games"]:
        mapping = slot_of(game)
        for civ, info in game["civs"].items():
            slot = mapping.get(civ, "?")
            matrix.setdefault(civ, []).append({
                "game": game.get("tag"), "slot": slot,
                "slotType": slot_types.get(slot, "?"),
                "score": info["score"], "placement": info["placement"],
                "checkpoints": info["checkpoints"],
            })
    for civ in sorted(matrix):
        cells = " | ".join(f"{g['game']} {g['slot']}[{g['slotType']}]: {g['score']}" for g in matrix[civ])
        print(f"{civ:<14} {cells}")
    if not matrix:
        print("  （无 rotation 局可评分）")

    out = paths.ARENA / "rotation-cross-report.json"
    out.write_text(json.dumps({
        "coverage": data["coverage"],
        "method": args.method,
        "weights": args.weights,
        "matrix": matrix,
        "games": data["games"],
        "ranking": data["ranking"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n写入 {out}")


if __name__ == "__main__":
    main()
