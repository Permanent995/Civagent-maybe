"""槽位评级：发展型 / 早战型 / 混合（arena/spawn_classify.py）

用法: python arena/spawn_classify.py maps/arena-64x64.json arena/slots-64x64.json
评级因子（半径 4 内）:
  发展分: 食物地形(Grassland+小麦/牛/鹿/香蕉) + 奢侈资源种数 + 河流
  早战分: 产能地形(Hill/Forest) + 战略资源(马/铁) + 距中立区(地图中轴)的距离
输出每槽位的评级与因子明细。
"""

import json
import sys
from pathlib import Path


def analyze(map_path, slots_path):
    doc = json.loads(Path(map_path).read_text(encoding="utf-8"))
    slots = json.loads(Path(slots_path).read_text(encoding="utf-8"))
    tiles = {(t["position"]["x"], t["position"]["y"]): t for t in doc["tileList"]}

    def radius(key, r=4):
        x, y = key
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                t = tiles.get((x + dx, y + dy))
                if t:
                    yield t

    FOOD_TERRAIN = {"Grassland"}
    PROD_TERRAIN = {"Hill", "Forest"}
    FOOD_RES = {"Wheat", "Cattle", "Sheep", "Deer", "Bananas", "Fish"}
    STRAT_RES = {"Horses", "Iron", "Coal", "Oil", "Aluminum"}

    report = []
    for slot in slots:
        key = (slot["x"], slot["y"])
        area = list(radius(key))
        food = sum(1 for t in area
                   if t["baseTerrain"] in FOOD_TERRAIN
                   or t.get("resource") in FOOD_RES)
        production = sum(1 for t in area
                         if any(f in PROD_TERRAIN for f in t.get("terrainFeatures", []))
                         or t.get("resource") in STRAT_RES)
        luxuries = {t["resource"] for t in area
                    if t.get("resource") and t["resource"] not in FOOD_RES | STRAT_RES}
        rivers = sum(1 for t in area
                     if t.get("hasBottomRiver") or t.get("hasBottomLeftRiver")
                     or t.get("hasBottomRightRiver"))
        development = food + len(luxuries) * 2 + (3 if rivers >= 2 else 0)
        early_war = production + len([r for r in STRAT_RES
                                      if any(t.get("resource") == r for t in area)]) * 4
        # 早战加成：离地图中轴（无人争夺区）越近越容易早期接敌
        center_distance = abs(key[0] + key[1] / 2)
        early_war += max(0, 8 - center_distance) / 2
        kind = ("发展型" if development > early_war * 1.3
                else "早战型" if early_war > development * 1.3 else "混合型")
        report.append({
            "slot": slot["slot"], "pos": [slot["x"], slot["y"]],
            "评级": kind,
            "发展分": development, "早战分": round(early_war, 1),
            "食物地形": food, "产能地形": production,
            "奢侈种类": sorted(luxuries), "河流瓦": rivers,
        })
    return report


if __name__ == "__main__":
    map_path = sys.argv[1] if len(sys.argv) > 1 else "maps/arena-64x64.json"
    slots_path = sys.argv[2] if len(sys.argv) > 2 else "arena/slots-64x64.json"
    result = analyze(map_path, slots_path)
    for r in result:
        print(f"{r['slot']} @ {r['pos']}: {r['评级']} (发展{r['发展分']} / 早战{r['早战分']}) "
              f"食物{r['食物地形']} 产能{r['产能地形']} 奢侈{r['奢侈种类']} 河流{r['河流瓦']}")
    out = Path("arena/spawn-report.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {out}")
