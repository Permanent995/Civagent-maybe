"""把"起点"注入到一张现成的地图里（不重新生成地形/资源）。

为什么需要：用户手工改好的测试图（`测试arena-64x64-rotation.json`，36 种资源 577 处，
含模组 Bronze/Jade/Tea/醴）**没有 startingLocations**，而轮换赛靠固定 6 槽位保证公平。
`map_generator.py` 会连地形资源一起重造（且它的资源清单是硬编码的 26 种，摆不出模组资源），
所以这里只做"注入起点"，其余一概不动。

用法
----
    python arena/map_fix.py --base <源地图> --roster-file <roster.json> --out <目标地图>
    # roster.json = [{"nation":"Sheng Tang","x":-26,"y":-2}, ...]

输出是**明文 JSON**（MapSaver.loadMap 先试 Gzip、失败则按原文解析，所以明文可用且便于 review）。
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
from pathlib import Path


def load_map(path: Path) -> dict:
    raw = Path(path).read_bytes()
    for decode in (
        lambda b: json.loads(b.decode("utf-8")),
        lambda b: json.loads(gzip.decompress(base64.b64decode(b)).decode("utf-8")),
        lambda b: json.loads(gzip.decompress(b).decode("utf-8")),
    ):
        try:
            return decode(raw)
        except Exception:
            continue
    raise ValueError(f"无法解析地图（既不是明文 JSON，也不是 Gzip/Base64+Gzip）: {path}")


def coords_of(doc: dict) -> set[tuple[int, int]]:
    coords: set[tuple[int, int]] = set()
    for tile in doc.get("tileList") or []:
        pos = tile.get("position") or {}
        x, y = pos.get("x"), pos.get("y")
        if isinstance(x, int) and isinstance(y, int):
            coords.add((x, y))
    return coords


def inject(base: Path, roster: list[dict], out: Path, mods: list[str] | None = None) -> dict:
    doc = load_map(base)
    coords = coords_of(doc)
    missing = [(e["nation"], e["x"], e["y"]) for e in roster if (e["x"], e["y"]) not in coords]
    if missing:
        raise ValueError(f"这些起点在地图里不存在: {missing}")
    doc["startingLocations"] = [
        {"position": {"x": int(e["x"]), "y": int(e["y"])},
         "nation": e["nation"], "usage": "Normal"}
        for e in roster
    ]
    # 地图里记录的 mods 要和实际启用的模组一致，否则引擎可能拒绝加载/要求补模组
    if mods is not None:
        doc.setdefault("mapParameters", {})["mods"] = list(mods)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description="把起点注入现成地图")
    parser.add_argument("--base", required=True)
    parser.add_argument("--roster-file", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mods", default="", help="逗号分隔；写入 mapParameters.mods（默认不动）")
    args = parser.parse_args()

    roster = json.loads(Path(args.roster_file).read_text(encoding="utf-8"))
    mods = [m.strip() for m in args.mods.split(",") if m.strip()] or None
    doc = inject(Path(args.base), roster, Path(args.out), mods)
    tiles = len(doc.get("tileList") or [])
    kinds = {t.get("resource") for t in (doc.get("tileList") or []) if t.get("resource")}
    print(f"[map_fix] {Path(args.base).name} -> {args.out}  格子={tiles} 资源种类={len(kinds)} "
          f"起点={len(doc['startingLocations'])}" + (f" mods={mods}" if mods else ""))


if __name__ == "__main__":
    main()
