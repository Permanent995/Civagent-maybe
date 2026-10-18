"""Poll rotation logs for hangs / crashes / spawn while runner is alive."""
from __future__ import annotations

import re
import time
from pathlib import Path

LOGS = Path(__file__).resolve().parent / "logs"
STATUS = LOGS / "_monitor-status.txt"
TURN_RE = re.compile(r"\[arena\] turn (\d+)")
BAD = ("Exception", "OutOfMemory", "NullPointer", "FATAL", "Error:")


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or (len(raw) > 2 and raw[1:2] == b"\x00"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def latest_game_log() -> Path | None:
    logs = sorted(LOGS.glob("sym-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def main() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    last_turn = -1
    last_change = time.time()
    stall_secs = 180
    while True:
        lines = []
        runner = LOGS / "_rotation-runner.out"
        if runner.exists():
            text = _read_text(runner)
            tail = text.splitlines()[-12:]
            lines.append("=== runner tail ===")
            lines.extend(tail)
            if "ALL DONE" in text:
                lines.append("MONITOR: runner finished")
                STATUS.write_text("\n".join(lines) + "\n", encoding="utf-8")
                break
        game = latest_game_log()
        if game:
            gtext = _read_text(game)
            turns = [int(m.group(1)) for m in TURN_RE.finditer(gtext)]
            turn = turns[-1] if turns else 0
            if turn != last_turn:
                last_turn = turn
                last_change = time.time()
            stalled = time.time() - last_change
            lines.append(f"=== {game.name} turn={turn} stalled={stalled:.0f}s ===")
            for needle in BAD:
                if needle in gtext:
                    for line in gtext.splitlines():
                        if needle in line:
                            lines.append(f"ALERT {needle}: {line[:160]}")
                            break
            if "[arena] starting" in gtext and turn == 0 and stalled > 60:
                lines.append("ALERT: started but no turn progress >60s")
            if stalled > stall_secs and turn > 0:
                lines.append(f"ALERT: possible hang — no turn advance for {stalled:.0f}s")
            roster = Path(__file__).resolve().parent / f"roster-{game.stem}.json"
            if roster.exists():
                lines.append(f"roster: {roster.name} exists")
        STATUS.write_text("\n".join(lines) + "\n", encoding="utf-8")
        time.sleep(20)


if __name__ == "__main__":
    main()
