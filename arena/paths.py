"""训练场路径与环境配置（单一来源）。

原来 JDK、`Desktop/Unciv-Source`、`%TEMP%` 日志、客户端 maps 目录全部硬编码在各脚本里，
换机器或换目录就断。这里集中一处，全部可用环境变量覆盖，默认值从本文件位置推导：

    arena/  ->  Civagent_repo/  ->  <Desktop>/

环境变量
--------
ARENA_UNCIV_ROOT   Unciv-Source 仓库根          默认 <Desktop>/Unciv-Source
ARENA_GAME_CWD     引擎工作目录（必须含 maps/）  默认 <Unciv>/android/assets
ARENA_SAVE_DIR     SaveFiles（stats/存档）      默认 <GAME_CWD>/SaveFiles
ARENA_MAP_DIR      引擎读地图目录               默认 <GAME_CWD>/maps
ARENA_JAR          desktop jar                  默认 <Unciv>/desktop/build/libs/Unciv.jar
ARENA_JAVA         java.exe 绝对路径            默认 JAVA_HOME 或已知 Adoptium JDK17
ARENA_LOGS         对局日志目录                 默认 arena/logs（入库，不会被 %TEMP% 清掉）
ARENA_INDEX        对局索引文件                 默认 arena/index.jsonl
ARENA_CLIENT_MAPS  额外把生成的地图拷一份到这里 默认空 = 不写运行中的客户端
ARENA_MODS         模组列表（逗号分隔）         默认 "unciv unrepentant cathay"
ARENA_XMX          JVM 堆                       默认 2g
"""

import os
import sys
from pathlib import Path


def make_stdout_safe() -> None:
    """Windows 控制台默认 GBK（cp936）：一个非 GBK 符号（⚠/✗）就能让长批次崩在 print 上
    （实测 UnicodeEncodeError 打断了一整次评测）。把错误策略改成 replace —— 字形丢失
    可以接受，崩掉整场评测不可以。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass  # pytest 捕获的流不一定支持 reconfigure


make_stdout_safe()

ARENA = Path(__file__).resolve().parent
CIVAGENT_ROOT = ARENA.parent
DESKTOP = CIVAGENT_ROOT.parent


def _path(env_name: str, default: Path) -> Path:
    raw = os.environ.get(env_name, "").strip()
    return Path(raw).expanduser() if raw else default


UNCIV_ROOT = _path("ARENA_UNCIV_ROOT", DESKTOP / "Unciv-Source")
GAME_CWD = _path("ARENA_GAME_CWD", UNCIV_ROOT / "android" / "assets")
SAVE_DIR = _path("ARENA_SAVE_DIR", GAME_CWD / "SaveFiles")
MAP_DIR = _path("ARENA_MAP_DIR", GAME_CWD / "maps")
JAR = _path("ARENA_JAR", UNCIV_ROOT / "desktop" / "build" / "libs" / "Unciv.jar")

LOGS_DIR = _path("ARENA_LOGS", ARENA / "logs")
INDEX_PATH = _path("ARENA_INDEX", ARENA / "index.jsonl")

# 空 = 不往运行中的客户端写文件（离线评测不该碰在线客户端目录）
CLIENT_MAPS = os.environ.get("ARENA_CLIENT_MAPS", "").strip()

MODS = [m.strip() for m in os.environ.get("ARENA_MODS", "unciv unrepentant cathay").split(",") if m.strip()]
XMX = os.environ.get("ARENA_XMX", "2g")

_JAVA_CANDIDATES = (
    r"C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot\bin\java.exe",
    r"C:\Program Files\Eclipse Adoptium\jdk-17.0.18.8-hotspot\bin\java.exe",
)


def java_exe() -> str:
    """ARENA_JAVA > JAVA_HOME > 已知 Adoptium 路径 > PATH 上的 java。"""
    explicit = os.environ.get("ARENA_JAVA", "").strip()
    if explicit:
        return explicit
    home = os.environ.get("JAVA_HOME", "").strip()
    if home:
        candidate = Path(home) / "bin" / "java.exe"
        if candidate.exists():
            return str(candidate)
    for candidate in _JAVA_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return "java"


def jar_path() -> Path:
    return JAR


def require(what: str, path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"{what} 不存在: {path}（用环境变量覆盖，见 arena/paths.py 顶部说明）")
    return path


def ensure_dirs() -> None:
    for d in (LOGS_DIR, SAVE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def describe() -> str:
    lines = [
        f"ARENA          {ARENA}",
        f"UNCIV_ROOT     {UNCIV_ROOT}",
        f"GAME_CWD       {GAME_CWD}",
        f"SAVE_DIR       {SAVE_DIR}",
        f"MAP_DIR        {MAP_DIR}",
        f"JAR            {JAR}  [{'OK' if JAR.exists() else '缺失'}]",
        f"JAVA           {java_exe()}",
        f"LOGS_DIR       {LOGS_DIR}",
        f"INDEX_PATH     {INDEX_PATH}",
        f"CLIENT_MAPS    {CLIENT_MAPS or '(不写客户端)'}",
        f"MODS           {', '.join(MODS)}   XMX={XMX}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
