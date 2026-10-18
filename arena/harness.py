"""单局执行层：地图注入 → 启动 JVM → 收尸 → 写索引。

与 `run_rotation.py`（只排赛程）分开，这样：
- 换地图/换槽位/换难度只改 `LaunchConfig`，不动轮换逻辑；
- 「JVM 打完局不退出」「超时」「崩溃」这些进程细节集中在一处，跑批不会因为一局失败而中断。

退出机制（三层，缺一不可）
--------------------------
1. 完成标记：日志出现 `finished at turn` / `[arena] stats: ` → 再等 2s 让 final save 落盘，然后**主动杀**。
   （实测 JVM 打完局常常不退出，不主动杀会把 subprocess 拖到超时。）
2. 墙钟超时：`max(120, max_turns * 12)` 秒，到点强杀并标 `timeout`。
3. `finally` 兜底：无论正常返回、抛异常、还是 Ctrl-C，都保证该 PID 被收掉；批末再 `kill_stale()` 清残留。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

try:  # 包内导入：python -m arena.harness / from arena import harness
    from . import gamelog, paths, procs
except ImportError:  # 直接脚本：python arena/harness.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gamelog  # noqa: E402
    import paths  # noqa: E402
    import procs  # noqa: E402

DEFAULT_MAP_NAME = "arena-64x64-rotation.json"
DEFAULT_SLOTS_FILE = "slots-64x64.json"
DONE_MARKERS = ("[arena] stats: ", "finished at turn")
CRASH_NEEDLES = ("Exception", "OutOfMemory", "FATAL", "Error:", "NullPointer")


@dataclass(frozen=True)
class LaunchConfig:
    """一批评测的固定条件（对照实验三固定：地图 / 对手 / 种子）。"""

    map_name: str = DEFAULT_MAP_NAME
    slots_file: str = DEFAULT_SLOTS_FILE
    base_map: Path | None = None      # 非 None：只注入起点，不重造地形/资源
    max_turns: int = 150
    difficulty: str = "Emperor"
    seed: int = 99
    stats_interval: int = 24
    mods: tuple[str, ...] = field(default_factory=lambda: tuple(paths.MODS))
    tag_prefix: str = "sym"
    kind: str = "rotation"
    era: str = "symmetric-vmidline"
    width: int = 64
    height: int = 64
    timeout_per_turn_s: int = 12

    def timeout_s(self) -> float:
        return max(120.0, self.max_turns * self.timeout_per_turn_s)


def load_slots(slots_file: str) -> list[tuple[int, int]]:
    doc = json.loads((paths.ARENA / slots_file).read_text(encoding="utf-8"))
    return [(int(s["x"]), int(s["y"])) for s in doc]


def generate_map(roster: list[tuple[str, tuple[int, int]]], game_tag: str, cfg: LaunchConfig) -> Path | None:
    """注入起点并落盘。失败返回 None（由调用方跳过本局，不炸整批）。"""
    roster_file = paths.ARENA / f"roster-{game_tag}.json"
    roster_file.write_text(json.dumps(
        [{"nation": n, "x": x, "y": y} for n, (x, y) in roster], ensure_ascii=False),
        encoding="utf-8")
    map_out = paths.MAP_DIR / cfg.map_name
    map_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if cfg.base_map is not None:
            subprocess.run([sys.executable, str(paths.ARENA / "map_fix.py"),
                            "--base", str(cfg.base_map), "--roster-file", str(roster_file),
                            "--out", str(map_out), "--mods", ",".join(cfg.mods)], check=True)
        else:
            subprocess.run([sys.executable, str(paths.ARENA / "map_generator.py"),
                            "--out", str(map_out), "--width", str(cfg.width), "--height", str(cfg.height),
                            "--wonder-pairs", "5", "--seed", str(cfg.seed),
                            "--roster-file", str(roster_file)], check=True)
            subprocess.run([sys.executable, str(paths.ARENA / "map_generator.py"),
                            "--verify", "--out", str(map_out)], check=True)
    except Exception as exc:
        print(f"[harness {game_tag}] [!] 地图生成失败，跳过本局：{exc}", flush=True)
        return None
    if paths.CLIENT_MAPS:
        dest = Path(paths.CLIENT_MAPS)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(map_out, dest / cfg.map_name)
    return map_out


def check_spawn(roster: list[tuple[str, tuple[int, int]]], tag: str) -> bool:
    """出生点成对：右席 = 左席镜像 (x,y)->(-x-y,y)；并打印槽位。"""
    print(f"[harness {tag}] spawn check:", flush=True)
    by_pos = {pos: n for n, pos in roster}
    ok = True
    for nation, (x, y) in roster:
        mirror = (-x - y, y)
        partner = by_pos.get(mirror)
        side = "L" if (x + y / 2.0) < 0 else "R"
        print(f"  {side} {nation:<14} ({x},{y}) mirror->{mirror} partner={partner or '?'}", flush=True)
        if partner is None and mirror != (x, y):
            ok = False
    if not ok:
        print(f"[harness {tag}] [!] spawn mirror incomplete", flush=True)
    return ok


def _scan_log(tag: str, text: str) -> None:
    for needle in CRASH_NEEDLES:
        if needle in text and "no victory" not in needle.lower():
            for line in text.splitlines():
                if needle in line:
                    print(f"[harness {tag}] [!] log hit '{needle}': {line[:160]}", flush=True)
                    break
            break
    if "turn 1" not in text and "[arena] starting" in text:
        print(f"[harness {tag}] [!] no turn 1 in log — spawn/start may have failed", flush=True)


def _launch(cmd: list[str], log_path: Path, tag: str, timeout_s: float) -> tuple[int | None, str, float]:
    """跑一局，返回 (rc, outcome, elapsed)。**保证**任何路径下进程都被收掉。"""
    started = time.time()
    rc: int | None = None
    outcome = "exited"
    proc: subprocess.Popen | None = None
    with open(log_path, "w", encoding="utf-8") as handle:
        proc = subprocess.Popen(cmd, cwd=str(paths.GAME_CWD), stdout=handle, stderr=subprocess.STDOUT)
        try:
            deadline = time.time() + timeout_s
            while True:
                if proc.poll() is not None:
                    rc = proc.returncode
                    break
                if time.time() > deadline:
                    print(f"[harness {tag}] [!] TIMEOUT {timeout_s:.0f}s — 强杀", flush=True)
                    rc, outcome = -9, "timeout"
                    break
                try:
                    text = log_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    text = ""
                if any(m in text for m in DONE_MARKERS):
                    time.sleep(2)  # 留时间写完尾部（final save 行）
                    outcome = "killed-after-finish"
                    break
                time.sleep(1)
        finally:
            # 兜底收尸：正常退出/超时/异常/Ctrl-C 都走到这里
            if proc is not None and proc.poll() is None:
                procs.kill_pid(proc.pid)
                if rc is None:
                    rc = -9
    return rc, outcome, time.time() - started


def run_game(round_index: int, game_index: int, nations: list[str], cfg: LaunchConfig) -> dict | None:
    """跑一局并写入索引。**任何异常都被吞掉并返回 None**，保证批次不中断。"""
    tag = f"{cfg.tag_prefix}-r{round_index}g{game_index}"
    try:
        slots = load_slots(cfg.slots_file)
        if len(nations) > len(slots):
            print(f"[harness {tag}] [!] 需要 {len(nations)} 个槽位，只有 {len(slots)}，跳过", flush=True)
            return None
        roster = list(zip(nations, slots[:len(nations)]))
        if generate_map(roster, tag, cfg) is None:
            return None
        check_spawn(roster, tag)
        procs.kill_stale()

        log_path = paths.LOGS_DIR / f"{tag}.log"
        paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        cmd = [paths.java_exe(), f"-Xmx{paths.XMX}", "-cp", str(paths.jar_path()),
               "com.unciv.app.desktop.ArenaLauncher",
               "--map", cfg.map_name,
               "--players", ",".join(f"{n}:native" for n in nations),
               "--mods", ",".join(cfg.mods),
               "--difficulty", cfg.difficulty,
               "--max-turns", str(cfg.max_turns),
               "--stats-interval", str(cfg.stats_interval)]
        print(f"[harness {tag}] {' vs '.join(nations)}", flush=True)
        rc, outcome, elapsed = _launch(cmd, log_path, tag, cfg.timeout_s())
        print(f"[harness {tag}] done in {elapsed:.0f}s rc={rc} ({outcome})", flush=True)
    except KeyboardInterrupt:
        procs.kill_stale()
        raise
    except Exception as exc:
        print(f"[harness {tag}] [!] 本局异常，已跳过：{type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        procs.kill_stale()
        return None

    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    _scan_log(tag, text)

    record = gamelog.parse_log(log_path, kind=cfg.kind, tag=tag, max_turns=cfg.max_turns,
                               mods=list(cfg.mods), seed=cfg.seed)
    if record is None:
        print(f"[harness {tag}] [!] 日志里没有 gameId，索引未记录", flush=True)
        return None
    record["seconds"] = round(elapsed)
    record["map"] = cfg.map_name
    record["era"] = cfg.era
    gamelog.append_record(record)
    print(f"[harness {tag}] gameId={record['gameId']} outcome={record.get('outcome')} "
          f"stats={'OK' if record['statsExists'] else '缺失'} "
          f"finishedTurn={record.get('finishedTurn')} complete={record.get('complete')}", flush=True)
    return record
