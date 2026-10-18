"""轮换赛**排赛层**：拉丁方轮换 + 墙钟预算。单局执行在 `arena/harness.py`。

用法
----
    python arena/run_rotation.py [--rounds 2] [--max-turns 150] [--difficulty Emperor] [--seed 99]
    python -m arena.run_rotation --minutes 20 \
        --base-map "C:/.../maps/测试arena.json" --map-name 测试arena.json \
        --slots slots-测试arena.json

- 6 个固定槽位（默认 `arena/slots-64x64.json`，左右镜像成对）
- 国家按拉丁方轮换：每国每轮都上场，且每轮在**不同**槽位
- `--base-map` 给一张现成地图时只注入起点，保住手摆的地形/资源（`arena/map_fix.py`）

产物与退出机制见 `arena/harness.py` 顶部；路径与 java 一律走 `arena/paths.py`。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

try:  # 包内导入：python -m arena.run_rotation / from arena import run_rotation
    from . import harness, paths, procs
except ImportError:  # 直接脚本：python arena/run_rotation.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import harness  # noqa: E402
    import paths  # noqa: E402
    import procs  # noqa: E402

NATIONS = ["Sheng Tang", "MongolEmpire", "Qindynasty", "Late Tang",
           "Babylon", "Ethiopia", "Sweden", "Greece", "Egypt", "Persia", "India", "Songhai",
           "Rome", "America", "France", "Russia"]  # 内置 16；--nations-file 可换成全名单


def plan_games(nations: list[str], rounds: int, start_round: int,
               per_game: int = 6) -> list[tuple[int, int, list[str]]]:
    """拉丁方排赛：每轮 N 国切成 ceil(N/6) 组，各 6 国；每国每轮都上场且换槽位。"""
    games_per_round = math.ceil(len(nations) / per_game)
    planned: list[tuple[int, int, list[str]]] = []
    for offset in range(rounds):
        round_index = start_round + offset
        shift = round_index % len(nations)
        shifted = nations[shift:] + nations[:shift]
        for game_index in range(games_per_round):
            start = game_index * per_game
            chunk = [shifted[(start + k) % len(shifted)] for k in range(per_game)]
            planned.append((round_index, game_index, chunk))
    return planned


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--start-round", type=int, default=0,
                        help="拉丁方轮次起点（继续测时用 2、4… 避免覆盖已有 sym-r* 日志）")
    parser.add_argument("--max-turns", type=int, default=150)
    parser.add_argument("--difficulty", default="Emperor")
    parser.add_argument("--seed", type=int, default=99)
    parser.add_argument("--stats-interval", type=int, default=24,
                        help="国力采样/日志间隔；AI 仍每回合原生 automate（默认 24）")
    parser.add_argument("--minutes", type=float, default=None, help="墙钟预算（分钟），到点停")
    parser.add_argument("--base-map", default=None, help="用这张现成地图（只注入起点，不重造地形/资源）")
    parser.add_argument("--map-name", default=None, help="引擎读的地图文件名（assets/maps/ 下）")
    parser.add_argument("--slots", default=harness.DEFAULT_SLOTS_FILE, help="槽位文件")
    parser.add_argument("--mods", default=None, help="覆盖模组列表（逗号分隔；默认走 ARENA_MODS）")
    parser.add_argument("--nations-file", default=None,
                        help="国家名单 json（数组）；默认内置 16 国。36 席/轮 的轮换会自动按 ceil(N/6) 局拆分")
    args = parser.parse_args()

    nations = list(NATIONS)
    if args.nations_file:
        loaded = json.loads(Path(args.nations_file).read_text(encoding="utf-8"))
        if not isinstance(loaded, list) or len(loaded) < 2:
            raise SystemExit(f"--nations-file 需要至少 2 个国家的 json 数组: {args.nations_file}")
        nations = [str(n) for n in loaded]

    base_map = None
    map_name = args.map_name or harness.DEFAULT_MAP_NAME
    if args.base_map:
        base_map = Path(args.base_map)
        if not base_map.exists():
            raise SystemExit(f"--base-map 不存在: {base_map}")
        map_name = args.map_name or "测试arena.json"
    mods = tuple(m.strip() for m in args.mods.split(",") if m.strip()) if args.mods else tuple(paths.MODS)
    cfg = harness.LaunchConfig(
        map_name=map_name, slots_file=args.slots, base_map=base_map,
        max_turns=args.max_turns, difficulty=args.difficulty, seed=args.seed,
        stats_interval=args.stats_interval, mods=mods,
    )
    deadline = (time.time() + args.minutes * 60) if args.minutes else None

    paths.ensure_dirs()
    print(paths.describe(), flush=True)
    planned = plan_games(nations, args.rounds, args.start_round)
    covered = sorted({n for _, _, c in planned for n in c})
    print(f"[rotation] plan: {len(planned)} games, rounds "
          f"{args.start_round}..{args.start_round + args.rounds - 1}, "
          f"nations={len(covered)}/{len(nations)}, difficulty={args.difficulty}, "
          f"maxTurns={args.max_turns}, map={map_name} (always 6/game), mods={','.join(mods)}"
          + (f", base_map={base_map.name}, slots={args.slots}" if base_map else "")
          + (f", budget={args.minutes}min" if deadline else ""), flush=True)
    if covered != sorted(nations):
        missing = sorted(set(nations) - set(covered))
        print(f"[rotation] [!] missing nations: {missing}", flush=True)

    records: list[dict] = []
    failed = 0
    try:
        for round_index, game_index, chunk in planned:
            if deadline is not None and time.time() >= deadline:
                print(f"[rotation] 预算到，停止（已完成 {len(records)} 局）", flush=True)
                break
            try:
                record = harness.run_game(round_index, game_index, chunk, cfg)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # 双保险：harness 已兜底，这里再兜一层
                failed += 1
                print(f"[rotation] [!] r{round_index}g{game_index} 异常（继续下一局）："
                      f"{type(exc).__name__}: {exc}", flush=True)
                procs.kill_stale()
                continue
            if record:
                records.append(record)
            else:
                failed += 1
    except KeyboardInterrupt:
        print("\n[rotation] Ctrl-C：清理残留进程后退出", flush=True)
        procs.kill_stale()
        raise SystemExit(130)
    finally:
        procs.kill_stale()

    print(f"[rotation] ALL DONE — 成功 {len(records)} 局，失败 {failed} 局，已入索引；"
          f"下一步 python arena/report.py", flush=True)


if __name__ == "__main__":
    main()
