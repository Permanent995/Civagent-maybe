"""残留进程清理。

原实现只用 `wmic process where name='java.exe' get ProcessId,CommandLine`：
`wmic` 自 Win11 起被弃用（部分机器已移除），而且只匹配 java.exe —— 训练场重启时
残留的 java 会和本次抢 CPU。这里优先用 psutil（本机已装 7.2.2），失败再退回 wmic。
"""

from __future__ import annotations

import os
import subprocess

MATCH = "ArenaLauncher"


def _via_psutil(match: str) -> list[tuple[int, str]]:
    import psutil  # type: ignore

    found: list[tuple[int, str]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except Exception:
            continue
        if match in cmdline:
            found.append((int(proc.info["pid"]), cmdline))
    return found


def _via_wmic(match: str) -> list[tuple[int, str]]:
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='java.exe'", "get", "ProcessId,CommandLine"],
            capture_output=True, text=True, encoding="gbk", errors="replace", timeout=30,
        ).stdout
    except Exception:
        return []
    found: list[tuple[int, str]] = []
    for line in out.splitlines():
        if match in line:
            parts = line.strip().split()
            if parts and parts[-1].isdigit():
                found.append((int(parts[-1]), line.strip()))
    return found


def list_arena_processes(match: str = MATCH) -> list[tuple[int, str]]:
    """只认 java 进程。

    血的教训：早先只按 cmdline 匹配，于是**把正在执行这条命令的 bash 自己也杀了**
    （任何解释这条字符串的命令行都会命中）—— 连出错两次。必须同时限定进程名。
    """
    try:
        import psutil  # type: ignore

        found: list[tuple[int, str]] = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                cmdline = " ".join(proc.info.get("cmdline") or [])
            except Exception:
                continue
            if name.startswith("java") and match in cmdline:
                found.append((int(proc.info["pid"]), cmdline))
        return found
    except Exception:
        return _via_wmic(match)


def kill_stale(match: str = MATCH, verbose: bool = True) -> list[int]:
    """杀掉命令行里含 `match` 的残留进程，返回被杀的 PID。"""
    killed: list[int] = []
    for pid, cmdline in list_arena_processes(match):
        if pid == os.getpid():
            continue
        try:
            import psutil  # type: ignore

            psutil.Process(pid).kill()
        except Exception:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        killed.append(pid)
        if verbose:
            print(f"[cleanup] killed stale {match} pid={pid}", flush=True)
    return killed


def kill_pid(pid: int) -> bool:
    """杀单个进程（runner 收工用：JVM 有时打完局不退出）。"""
    if pid == os.getpid():
        return False
    try:
        import psutil  # type: ignore

        psutil.Process(pid).kill()
        return True
    except Exception:
        return subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True).returncode == 0


if __name__ == "__main__":
    for pid, cmdline in list_arena_processes():
        print(pid, cmdline[:160])
