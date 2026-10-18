"""单局评估：从一局的 stats jsonl 算出三套分（A 国力增速 / B 征服进度 / C 性格加权）。

用法
----
    python arena/evaluate.py SaveFiles/arena-stats-xxxxxxxx.jsonl [--personality money] [--method median]
    python arena/evaluate.py --tag r0g0          # 用索引里的 tag（gameId 自动找）

方法论语系（EXECUTION 决策 6.1 档 1 - 评分用于迭代提示词/阈值，不是奖励模型训练）
  A 国力增速   power slope：每回合综合国力的最小二乘斜率
  B 征服进度   占城总量、军力峰值、灭国数
  C 性格达标   按 --personality 给三族不同加权

不裁决唯一真理：三套并列输出。**权重与公式来自 scoring.py（唯一来源）**，
本文件只做聚合与呈现，避免再出现"同一公式三份拷贝互相漂移"。
多人局按局内名次（placement）排名，不再只支持 1v1。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:  # 包内导入：python -m arena.evaluate / from arena import evaluate
    from . import gamelog, paths, scoring
except ImportError:  # 直接脚本：python arena/evaluate.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gamelog  # noqa: E402
    import paths  # noqa: E402
    import scoring  # noqa: E402


def resolve_target(target: str) -> Path:
    """接受 stats jsonl 路径、gameId（8 位）、或 tag（r0g0）。"""
    path = Path(target)
    if path.exists():
        return path
    candidate = paths.SAVE_DIR / f"arena-stats-{target}.jsonl"
    if candidate.exists():
        return candidate
    for record in gamelog.load_index():
        if record.get("tag") == target or record.get("gameId") == target:
            return Path(record["stats"])
    raise FileNotFoundError(f"找不到对局 {target}（既不是文件，也不在 arena/index.jsonl 里）")


def evaluate(path: Path, personality: str = "balanced", method: str = "median", weights=None) -> dict:
    rows = gamelog.read_stats(path)
    by_civ = scoring.group_by_civ(rows)
    report: dict = {
        "game": str(path),
        "personality": personality,
        "method": method,
        "weightSet": "hand" if weights is None else "custom",
        "civs": {},
    }
    for civ, entries in by_civ.items():
        report["civs"][civ] = scoring.summarize_civ(entries, personality, weights, civ=civ)

    # 局内相对势（同一回合归一化），给多人局用
    checkpoints = [t for t in scoring.CHECKPOINTS if t <= max((int(r.get("turn", 0)) for r in rows), default=0)]
    for civ, entries in by_civ.items():
        relatives = {}
        for turn in checkpoints:
            got = scoring.relative_at(by_civ, civ, turn, method, weights)
            if got is not None:
                relatives[str(turn)] = round(got[0], 3)
        report["civs"][civ]["相对势"] = relatives
        report["civs"][civ]["相对势均值"] = round(
            sum(relatives.values()) / len(relatives), 3) if relatives else None

    # 名次：多人局也能排（旧版只有 len(civs)==2 才给"对比"）
    if len(report["civs"]) >= 2:
        score_key = "C_性格加权分"
        ranks = scoring.placement({c: float(v.get(score_key, 0) or 0) for c, v in report["civs"].items()})
        for civ, rank in ranks.items():
            report["civs"][civ]["名次"] = rank
        report["名次"] = {c: r for c, r in sorted(ranks.items(), key=lambda kv: kv[1])}
        if len(report["civs"]) == 2:
            (a, ca), (b, cb) = list(report["civs"].items())
            report["对比"] = {
                "国力增速更快": a if ca["A_国力增速"] > cb["A_国力增速"] else b,
                "征服更多": (a if ca["B_征服进度"]["capturedCities"] > cb["B_征服进度"]["capturedCities"]
                             else b),
                f"性格加权({personality})更高": a if ca[score_key] > cb[score_key] else b,
            }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="单局评估")
    parser.add_argument("stats_jsonl", nargs="?", help="stats jsonl 路径 / gameId / tag")
    parser.add_argument("--tag", help="索引里的对局 tag（如 r0g0）")
    parser.add_argument("--personality", default="balanced", choices=sorted(scoring.PERSONALITIES))
    parser.add_argument("--method", default="median", choices=["median", "z"])
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    target = args.tag or args.stats_jsonl
    if not target:
        parser.error("需要 stats_jsonl 或 --tag")
    path = resolve_target(target)
    result = evaluate(path, args.personality, args.method)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
