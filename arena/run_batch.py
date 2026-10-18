"""智能体对打批次（1v1，双方都是 civagent 席位）—— T1/T2 用。

用法
----
    python arena/run_batch.py --minutes 30 [--max-turns 250] [--mods ...] [--map ...]

每局产物
    arena/logs/batch-{n}.log                   完整 stdout（仓库内）
    SaveFiles/arena-stats-{gameId8}.jsonl      每回合国力采样
    SaveFiles/arena-final-{gameId8}            终局存档
    arena/index.jsonl                          索引记录（kind=batch）
    deployment/data/{gameId}/                  大脑轨迹（决策/应用/事件）
    arena/batch-results.json                   本批汇总（含 gameId / 胜负 / 耗时）

修复：旧版只写 `pair/seconds/tail`，**没有 gameId**，所以这批对局从来没进过评分链路
（cross_report 只认 rotation 的 roster 文件）。现在每局结束就写索引，`python arena/report.py --kind batch`
可直接出排名。停局策略仍是墙钟预算；被预算切断的局在报告里显示为"有 stats 但没跑完"。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

try:  # 包内导入：python -m arena.run_batch / from arena import run_batch
    from . import gamelog, paths, procs
except ImportError:  # 直接脚本：python arena/run_batch.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gamelog  # noqa: E402
    import paths  # noqa: E402
    import procs  # noqa: E402

ROSTER = ["Sheng Tang", "MongolEmpire", "Qindynasty", "Late Tang",
          "Babylon", "Ethiopia", "Sweden", "Rome", "Greece", "Egypt"]


def pairings(round_index: int) -> list[tuple[str, str]]:
    """轮转配对：每轮换对手。"""
    ordered = ROSTER[round_index % len(ROSTER):] + ROSTER[:round_index % len(ROSTER)]
    return [(ordered[i], ordered[i + 1]) for i in range(0, len(ordered) - 1, 2)]


def run_game(nation_a: str, nation_b: str, max_turns: int, timeout_s: float,
             game_index: int, map_name: str, mods: list[str]) -> dict:
    tag = f"batch-{game_index}"
    log_path = paths.LOGS_DIR / f"{tag}.log"
    paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [paths.java_exe(), f"-Xmx{paths.XMX}", "-cp", str(paths.jar_path()),
           "com.unciv.app.desktop.ArenaLauncher",
           "--map", map_name,
           "--players", f"{nation_a}:civagent,{nation_b}:civagent",
           "--mods", ",".join(mods),
           "--max-turns", str(max_turns)]
    started = time.time()
    timed_out = False
    try:
        with open(log_path, "w", encoding="utf-8") as handle:
            subprocess.run(cmd, cwd=str(paths.GAME_CWD), stdout=handle,
                           stderr=subprocess.STDOUT, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True  # 预算切断：stats/存档到这一刻的部分仍可用

    record = gamelog.parse_log(log_path, kind="batch", tag=tag, max_turns=max_turns, mods=mods)
    result = {
        "tag": tag,
        "pair": f"{nation_a} vs {nation_b}",
        "seconds": round(time.time() - started),
        "timedOut": timed_out,
        "gameId": record.get("gameId") if record else None,
        "winner": record.get("winner") if record else None,
        "outcome": record.get("outcome") if record else None,
        "finishedTurn": record.get("finishedTurn") if record else None,
        "log": str(log_path),
    }
    if record:
        record["seconds"] = result["seconds"]
        gamelog.append_record(record)
        result["stats"] = record["stats"]
        result["statsExists"] = record["statsExists"]
    tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
    result["tail"] = tail
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=30.0)
    parser.add_argument("--max-turns", type=int, default=250)
    parser.add_argument("--mods", default=",".join(paths.MODS))
    parser.add_argument("--map", default=__import__("os").environ.get("ARENA_BATCH_MAP", "arena-51x50.json"))
    args = parser.parse_args()

    paths.ensure_dirs()
    mods = [m.strip() for m in args.mods.split(",") if m.strip()]
    print(paths.describe(), flush=True)

    deadline = time.time() + args.minutes * 60
    procs.kill_stale()
    results: list[dict] = []
    game_index = 0
    round_index = 0
    while time.time() < deadline:
        for pair in pairings(round_index):
            if time.time() >= deadline:
                break
            procs.kill_stale()
            print(f"[batch] game {game_index + 1}: {pair[0]} vs {pair[1]}", flush=True)
            result = run_game(pair[0], pair[1], args.max_turns,
                              timeout_s=max(60, deadline - time.time()),
                              game_index=game_index, map_name=args.map, mods=mods)
            results.append(result)
            print(f"[batch] done in {result['seconds']}s gameId={result['gameId']} "
                  f"outcome={result['outcome']}", flush=True)
            game_index += 1
        round_index += 1

    out = paths.ARENA / "batch-results.json"
    out.write_text(json.dumps({"minutes": args.minutes, "games": results}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    scored = sum(1 for r in results if r.get("statsExists"))
    print(f"[batch] {len(results)} 局 / {args.minutes} 分钟，{scored} 局有 stats — {out}")
    print("[batch] 下一步 python arena/report.py --kind batch", flush=True)


if __name__ == "__main__":
    main()
