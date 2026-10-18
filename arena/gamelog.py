"""对局索引：把"局"变成一条可查记录，建立 tag -> gameId -> stats/final 的稳定映射。

**为什么需要它（原实现的两个坑）**

1. `cross_report.py` 之前靠 `%TEMP%/rotation-{tag}.log` 里的正则 `gameId=([0-9a-f]{8})`
   去找 stats 文件。`%TEMP%` 会被系统/清理工具清掉 —— 实测 49 个终局存档只剩 12 个日志，
   一清就全部 `SKIP`，报告静默变成"只有几局"的排名。
2. 1v1 批次（`run_batch.py`）根本不写 gameId，产出的 `batch-results.json` 只有
   `pair/seconds/tail`，所以**智能体对打那一批从来没有进入过评分链路**。

现在：对局日志落到仓库内 `arena/logs/`（不会被清），每局结束立刻把记录追加到
`arena/index.jsonl`；另外还能从磁盘打扫出"有存档但没日志"的历史局（标记 `log=None`、
`outcome=None`），让覆盖率缺口**可见**而不是被静默丢掉。

记录字段
--------
tag, kind(rotation|batch), gameId, map, players[], roles{civ: civagent|native},
difficulty, maxTurns, mods[], seed, log, stats, final, finishedTurn, winner, outcome,
turnCount, source(log|disk), 以及派生布尔 statsExists/finalExists/hasOutcome/complete
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator, Sequence

try:  # 包内导入：python -m arena.gamelog / from arena import gamelog
    from . import paths
except ImportError:  # 直接脚本：python arena/gamelog.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import paths  # noqa: E402

try:  # 包内导入：python -m arena.gamelog / from arena import gamelog
    from . import paths
except ImportError:  # 直接脚本：python arena/gamelog.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import paths  # noqa: E402

_ID = re.compile(r"gameId=([0-9a-f]{8})")
_DIFF = re.compile(r"difficulty=(\S+)")
_SEATS = re.compile(r"civagentSeats=\[([^\]]*)\]")
_NATIVE = re.compile(r"native=\[([^\]]*)\]")
_MAP = re.compile(r"map=(\S+)")
_PLAYERS = re.compile(r"players=([^\n]*)")
_FINISH = re.compile(r"finished at turn (\d+):\s*(.*)")
_FINAL = re.compile(r"final save:\s*(.+)")
_STATS = re.compile(r"stats:\s*(.+)")
_TURN_PRINT = re.compile(r"^\[arena\] turn (\d+) - ", re.M)
_VICTORY = re.compile(r"VICTORY:\s*(.+)")


def _names(raw: str) -> list[str]:
    return [n.strip() for n in raw.split(",") if n.strip()]


def parse_log(path: Path | str, kind: str = "rotation", tag: str | None = None,
              max_turns: int | None = None, mods: Sequence[str] | None = None,
              seed: int | None = None) -> dict | None:
    """解析一次 ArenaLauncher 的 stdout。没有任何可识别内容时返回 None。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")

    id_match = _ID.search(text)
    if not id_match:
        return None

    roles: dict[str, str] = {}
    seats = _SEATS.search(text)
    if seats:
        for name in _names(seats.group(1)):
            roles[name] = "civagent"
    native = _NATIVE.search(text)
    if native:
        for name in _names(native.group(1)):
            roles.setdefault(name, "native")

    players: list[str] = []
    players_match = _PLAYERS.search(text)
    if players_match:
        players = _names(players_match.group(1))

    turn_numbers = [int(t) for t in _TURN_PRINT.findall(text)]
    finished = _FINISH.search(text)
    final = _FINAL.search(text)
    stats = _STATS.search(text)
    winner = None
    outcome = None
    if finished:
        outcome = finished.group(2).strip()
        victory = _VICTORY.search(outcome)
        winner = victory.group(1).strip() if victory else None

    record = {
        "tag": tag or path.stem,
        "kind": kind,
        "gameId": id_match.group(1),
        "map": _MAP.search(text).group(1) if _MAP.search(text) else None,
        "players": players,
        "roles": roles,
        "difficulty": _DIFF.search(text).group(1) if _DIFF.search(text) else None,
        "maxTurns": max_turns,
        "mods": list(mods) if mods else None,
        "seed": seed,
        "log": str(path),
        "stats": stats.group(1).strip() if stats else str(paths.SAVE_DIR / f"arena-stats-{id_match.group(1)}.jsonl"),
        "final": final.group(1).strip() if final else str(paths.SAVE_DIR / f"arena-final-{id_match.group(1)}"),
        "finishedTurn": int(finished.group(1)) if finished else (max(turn_numbers) if turn_numbers else None),
        "turnCount": max(turn_numbers) if turn_numbers else None,
        "winner": winner,
        "outcome": outcome,
        "source": "log",
    }
    return _decorate(record)


def record_from_disk(game_id: str, kind: str = "unknown") -> dict:
    """没有日志的历史局：从 SaveFiles 的文件名反推。"""
    record = {
        "tag": None,
        "kind": kind,
        "gameId": game_id,
        "map": None,
        "players": [],
        "roles": {},
        "difficulty": None,
        "maxTurns": None,
        "mods": None,
        "seed": None,
        "log": None,
        "stats": str(paths.SAVE_DIR / f"arena-stats-{game_id}.jsonl"),
        "final": str(paths.SAVE_DIR / f"arena-final-{game_id}"),
        "finishedTurn": None,
        "turnCount": None,
        "winner": None,
        "outcome": None,
        "source": "disk",
    }
    return _decorate(record)


def _decorate(record: dict) -> dict:
    stats = Path(record["stats"]) if record.get("stats") else None
    final = Path(record["final"]) if record.get("final") else None
    record["statsExists"] = bool(stats and stats.exists())
    record["finalExists"] = bool(final and final.exists())
    record["hasOutcome"] = record.get("outcome") is not None
    # 可用于评分的局：有 stats（评分骨干）+ 有胜负标签（自动权重需要标签）
    record["scorable"] = bool(record["statsExists"])
    record["complete"] = bool(record["statsExists"] and record["hasOutcome"])
    return record


def is_complete(record: dict) -> bool:
    """完成口径（2026-09-15 用户定，**宽松**）。

    跑完 = 日志给出了一个结果：胜利（VICTORY）或回合上限（no victory (turn limit)）。
    被墙钟 timeout 切断、日志里没有 outcome 的局**不算**（它可能只走到 50/100 就被砍）。
    提前胜利（<150 回合）**算跑完**——否则会系统性惩罚早战型国家（用户拍板）。
    严格口径（必须走到回合上限）由调用方自己判 finishedTurn。
    """
    return bool(record.get("hasOutcome") or record.get("outcome"))


def key_of(record: dict) -> str:
    """去重键：**永远是 gameId**（一局唯一）。

    早先版本用 `tag or gameId`，导致同一局被计两次：日志记录键是 `r0g0`，
    磁盘记录键是 `4949f3e7` —— 索引直接翻倍（100 条里一半是重复的）。
    tag 只是元数据（轮换赛的 r0g0 / 批次的 batch-3），不能当主键。
    """
    return str(record.get("gameId") or record.get("tag") or "?")


def _merge(base: dict, extra: dict) -> dict:
    """合并同一局的两个视图：None 不覆盖已知值；source 优先 log；存在性布尔取或。"""
    out = dict(base)
    for key, value in extra.items():
        if value is None:
            continue
        if key == "source":
            if base.get("source") == "log":
                continue
            out[key] = value
            continue
        if key == "kind" and out.get("kind") not in (None, "unknown"):
            continue
        out[key] = value
    return _decorate(out)


LEGACY_GLOBS = ("rotation-*.log", "arena-game-*.log")


def legacy_log_dirs() -> list[Path]:
    """历史日志目录（**必须显式指定**，默认不扫）。

    老版本把日志写在 `%TEMP%`（会被系统清掉，实测 49 局只剩 12 个）。刚修这层时默认扫
    `%TEMP%`，结果那里有 170 个无关 `.log`：索引被污染、单测直接被真实数据干扰。
    现在只有显式给了 ARENA_LEGACY_LOGS 才扫（多个目录用 os.pathsep 分隔），
    并且用 `python arena/gamelog.py --import-legacy <目录>` 把存量日志抄进 arena/logs/。
    """
    raw = os.environ.get("ARENA_LEGACY_LOGS", "").strip()
    if not raw:
        return []
    dirs: list[Path] = []
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        path = Path(entry)
        if path.exists() and path not in dirs:
            dirs.append(path)
    return dirs


def classify_log(stem: str) -> tuple[str, str]:
    """日志文件名 -> (kind, tag)。`rotation-r0g0` / `r0g0` / `arena-game-3` / `batch-3`。"""
    if stem.startswith("rotation-"):
        return "rotation", stem[len("rotation-"):]
    if stem.startswith("sym-"):        # 竖中线对称图时代的轮换局（run_rotation 的默认前缀）
        return "rotation", stem
    if stem.startswith("arena-game-"):
        return "batch", stem
    if stem.startswith("batch-"):
        return "batch", stem
    if re.fullmatch(r"r\d+g\d+", stem):
        return "rotation", stem
    return "unknown", stem


def discover_sources(logs_dir: Path | None = None) -> list[tuple[Path, str, str]]:
    """产出去重后的 (日志路径, kind, tag)。仓库内日志优先于 %TEMP% 里的旧同名日志。"""
    primary = Path(logs_dir or paths.LOGS_DIR)
    found: dict[tuple[str, str], Path] = {}
    dirs = ([primary] if primary.exists() else []) + legacy_log_dirs()
    for d in dirs:
        # 仓库内可以放任意 .log；历史目录只认训练场自己的命名，别把别人的日志也捞进来
        patterns = ("*.log",) if d == primary else LEGACY_GLOBS
        for pattern in patterns:
            for path in sorted(d.glob(pattern)):
                if not path.is_file():
                    continue
                kind, tag = classify_log(path.stem)
                found.setdefault((kind, tag), path)
    return [(path, kind, tag) for (kind, tag), path in sorted(found.items(), key=lambda kv: str(kv[0]))]


def discover_logs(logs_dir: Path | None = None) -> list[Path]:
    return [path for path, _, _ in discover_sources(logs_dir)]


def discover_disk_games(save_dir: Path | None = None) -> Iterator[dict]:
    """扫 SaveFiles，产出磁盘上有 stats 或 final 的 gameId 记录。"""
    d = Path(save_dir or paths.SAVE_DIR)
    if not d.exists():
        return
    ids: set[str] = set()
    for pattern in ("arena-stats-*.jsonl", "arena-final-*"):
        for p in d.glob(pattern):
            name = p.name
            if name.startswith("arena-stats-"):
                ids.add(name[len("arena-stats-"):-len(".jsonl")])
            else:
                ids.add(name[len("arena-final-"):])
    for game_id in sorted(ids):
        yield record_from_disk(game_id)


def load_index(index_path: Path | None = None) -> list[dict]:
    path = Path(index_path or paths.INDEX_PATH)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(_decorate(json.loads(line)))
    return records


def save_index(records: Iterable[dict], index_path: Path | None = None) -> Path:
    path = Path(index_path or paths.INDEX_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def build_index(kind_by_tag: dict[str, str] | None = None, logs_dir: Path | None = None,
                save_dir: Path | None = None, index_path: Path | None = None,
                keep_meta: bool = True) -> list[dict]:
    """合并三个来源：旧索引（保留 runner 附带的元数据）+ 日志 + 磁盘。

    旧索引在前，因此 runner 写入的 kind/maxTurns/mods/seed 会被保留；
    日志能补出 winner/outcome/stats 路径；磁盘补出"两样都没有"的孤儿局。
    """
    kind_by_tag = kind_by_tag or {}
    merged: dict[str, dict] = {}

    def put(record: dict) -> None:
        key = key_of(record)
        merged[key] = _merge(merged[key], record) if key in merged else _decorate(record)

    if keep_meta:
        for record in load_index(index_path):
            put(record)

    for log_path, kind, tag in discover_sources(logs_dir):
        kind = kind_by_tag.get(tag, kind)
        parsed = parse_log(log_path, kind=kind, tag=tag)
        if parsed is not None:
            put(parsed)

    for record in discover_disk_games(save_dir):
        put(record)

    records = sorted(merged.values(),
                     key=lambda r: (str(r.get("kind") or ""), str(r.get("tag") or ""), str(r.get("gameId"))))
    save_index(records, index_path)
    return records


def append_record(record: dict, index_path: Path | None = None) -> Path:
    """runner 每局结束时调用：追加（按 key 覆盖）。"""
    path = Path(index_path or paths.INDEX_PATH)
    records = [r for r in load_index(path) if key_of(r) != key_of(record)]
    records.append(_decorate(record))
    return save_index(records, path)


def coverage(records: Sequence[dict]) -> dict:
    """覆盖率统计 —— 报告必须显式打印，避免"只算了几局"被当成全量排名。"""
    total = len(records)
    with_stats = sum(1 for r in records if r.get("statsExists"))
    with_final = sum(1 for r in records if r.get("finalExists"))
    with_outcome = sum(1 for r in records if r.get("hasOutcome"))
    complete = sum(1 for r in records if r.get("complete"))
    with_log = sum(1 for r in records if r.get("source") == "log" or r.get("log"))
    return {
        "games": total,
        "stats": with_stats,
        "final": with_final,
        "outcome": with_outcome,
        "complete": complete,
        "withLog": with_log,
        "statsPct": round(100.0 * with_stats / total, 1) if total else 0.0,
        "completePct": round(100.0 * complete / total, 1) if total else 0.0,
        # 只有 final 没有 stats = 评分骨干缺失（无法复原，必须重跑或标为缺失）
        "missingStats": [r["gameId"] for r in records if r.get("finalExists") and not r.get("statsExists")],
        # 只有 stats 没有 final = 对局被中断（timeout/kill），部分可用于趋势但不该进终局排名
        "interrupted": [r["gameId"] for r in records if r.get("statsExists") and not r.get("finalExists")],
    }


def read_stats(path: Path | str) -> list[dict]:
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def import_legacy(source_dir: Path | str, dest_dir: Path | None = None,
                  verbose: bool = True) -> list[Path]:
    """把历史日志从 `%TEMP%` 抄进 arena/logs/（改名成 tag），让胜负标签不再随系统清理丢失。

    只认 LEGACY_GLOBS 命名，已存在的不覆盖（仓库内的是新跑的，优先）。
    """
    source = Path(source_dir)
    dest = Path(dest_dir or paths.LOGS_DIR)
    copied: list[Path] = []
    if not source.exists():
        return copied
    dest.mkdir(parents=True, exist_ok=True)
    for pattern in LEGACY_GLOBS:
        for path in sorted(source.glob(pattern)):
            if not path.is_file():
                continue
            _, tag = classify_log(path.stem)
            target = dest / f"{tag}.log"
            if target.exists():
                continue
            target.write_bytes(path.read_bytes())
            copied.append(target)
            if verbose:
                print(f"[legacy] {path.name} -> {target.name}", flush=True)
    return copied


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="对局索引：重建 / 导入历史日志")
    parser.add_argument("--import-legacy", metavar="DIR", default=None,
                        help="把该目录（如 %%TEMP%%）里的历史训练场日志抄进 arena/logs/ 后再重建索引")
    parser.add_argument("--rebuild", action="store_true", help="强制重建（默认也重建）")
    args = parser.parse_args()

    if args.import_legacy:
        copied = import_legacy(args.import_legacy)
        print(f"[legacy] 共抄入 {len(copied)} 个日志 -> {paths.LOGS_DIR}")

    records = build_index()
    cov = coverage(records)
    print(f"索引 {paths.INDEX_PATH}")
    print(f"  局数 {cov['games']} | 有 stats {cov['stats']} ({cov['statsPct']}%) | "
          f"有存档 {cov['final']} | 有胜负 {cov['outcome']} | 完整 {cov['complete']} ({cov['completePct']}%)")
    if cov["missingStats"]:
        print(f"  缺 stats（无法评分，需重跑）：{len(cov['missingStats'])} 局 {', '.join(cov['missingStats'][:8])}")
    if cov["interrupted"]:
        print(f"  未跑完（被 timeout/kill）：{len(cov['interrupted'])} 局 {', '.join(cov['interrupted'][:8])}")


if __name__ == "__main__":
    main()
