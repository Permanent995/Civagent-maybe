"""读存档 → 国家强度指标 + 可直接进排名的 stats jsonl。

为什么需要：客户端手打的局没有 `arena-stats`（那只有无头裁决器 ArenaLauncher 会写），
所以以前这些局完全进不了排名。存档里有足够的信息重建同样的指标。

存档结构（实测，2026-09-14，4.21.4-patch1）
------------------------------------------
    GameInfo: civilizations[], gameId, turns, victoryData{winningCiv,victoryType,victoryTurn},
              gameParameters{mods,difficulty}, tileMap{tileList[]}
    civ     : civID, civName, cities[], gold,
              policies{adoptedPolicies,storedCulture}, tech{techsResearched},
              religionManager{storedFaith}, statsHistory{turn -> "S..N..C..P..G..T..F..H..W..A.."}
    city    : population{population}, cityConstructions{builtBuildings[]}, foundingCiv
    单位    : **不在 civ 里**，挂在格子上：tileList[].militaryUnit{owner,name,...}

三个实测踩过的坑
----------------
1. `civ.stats` 和 `civ.units` **都不存在** —— 所以 arena 那套 `goldPerTurn/sciencePerTurn` 拿不到；
   单位必须扫格子并按 `owner` 归属（军力用 `units-strength.json` 按单位名反查强度）。
2. `statsHistory` 是**逐回合的排行快照**，来源 `CivRankingHistory.recordRankingStats()` →
   `civ.getStatForRanking(RankingType)`。token 字母**已从源码逐项核实**
   （`RankingType.idForSerialization`，按枚举声明顺序）：

   | token | RankingType | 取什么 |
   |---|---|---|
   | `S` | Score | `calculateTotalScore()`（**就是 ArenaLauncher 写的 `engineScore`**）|
   | `N` | Population | Σ 城市人口 |
   | `C` | Growth | 下回合食物 |
   | `P` | Production | 下回合产能 |
   | `G` | Gold | 金币存量 |
   | `T` | Territory | Σ 城市地块数 |
   | `F` | Force | `getMilitaryMight()`（**带 √金币加成封顶 2×、派系强度修正、海军减半**）|
   | `H` | Happiness | 快乐度 |
   | `W` | Technologies | 已研究科技数 |
   | `A` | Culture | 已采纳（非完整分支）政策数 |

   ⚠ `S` 在**被灭国时记 0**（`getStatForRanking` 开头就是 `if (isDefeated()) 0`），
   所以灭亡文明的 engineScore 会读成 0 —— 与游戏内排行一致，不是读错。
   ⚠ 另一个易混点：`arena-stats` 里的 `militaryMight` 是 **ArenaLauncher 自己的简化定义**
   （Σ max(基础强度, 远程强度)），**不是**引擎的 `getMilitaryMight()`（那个只出现在 `F` token 里）。
3. 存档是 **明文 JSON**（客户端手打）或 **Base64+Gzip**（引擎 MapSaver/存档器），两种都要能读。

用法
----
    python arena/savedata.py "C:\\...\\测试存档9-13"        # 整个目录出榜
    python arena/savedata.py <单个存档> --json             # 明细 JSON
    python arena/savedata.py <目录> --write-index          # 生成 stats jsonl + 写进 arena/index.jsonl
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import re
import sys
from pathlib import Path

try:  # 包内导入：python -m arena.savedata / from arena import savedata
    from . import gamelog, paths, scoring
except ImportError:  # 直接脚本：python arena/savedata.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gamelog  # noqa: E402
    import paths  # noqa: E402
    import scoring  # noqa: E402

WATER = {"Ocean", "Coast"}
TOKEN = re.compile(r"([A-Z])(\d+)")
STATS_DIR = paths.ARENA / "save_stats"
UNITS_CACHE = paths.ARENA / "units-strength.json"
WONDERS_CACHE = paths.ARENA / "wonders.json"
SPECTATOR = "Spectator"
BARBARIANS = "Barbarians"


def is_major(name: str, row: dict) -> bool:
    """主要文明判据（实测 6 局 56 个有城文明全部判对）。

    存档实测（2026-09-14）：
    - `isCityState` 在存档里**恒为 None**，不能用。
    - `cityStatePersonality` 只有**部分局**的城邦才有（17/24），单靠它会漏。
    - `statsHistory` / `espionageManager`：城邦 **0/24** 有，主要文明 **32/32** 有 —— 完美区分。
    - 名字不可靠：这几局里 Venice 就是当城邦用的。
    注意：**0 城的主要文明（已被灭国）仍算主要文明**，不能当城邦过滤掉。
    """
    if name in (SPECTATOR, BARBARIANS) or "旁观者" in name:
        return False
    if row.get("cityStatePersonality"):
        return False
    return bool(row.get("hasStatsHistory")) or bool(row.get("hasEspionage"))
RULESET_DIRS = (
    paths.UNCIV_ROOT / "android" / "assets" / "jsons" / "Civ V - Gods & Kings",
    Path(r"C:\Users\<user>\Desktop\Unciv-SongDynasty\mods\unciv unrepentant cathay\jsons"),
)


# ------------------------------------------------------------------ 读文件

def load_save(path: Path) -> dict:
    """明文 JSON 与 Base64+Gzip 都能读。"""
    raw = Path(path).read_bytes()
    for decode in (
        lambda b: json.loads(b.decode("utf-8")),
        lambda b: json.loads(gzip.decompress(base64.b64decode(b)).decode("utf-8")),
        lambda b: json.loads(gzip.decompress(b).decode("utf-8")),
    ):
        try:
            doc = decode(raw)
        except Exception:
            continue
        if isinstance(doc, dict) and "civilizations" in doc:
            return doc
    raise ValueError(f"无法解析存档（既非明文 JSON 也非 Gzip/Base64+Gzip）: {path}")


def _jsonc(text: str) -> list:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    out = []
    for line in text.splitlines():
        res, in_str, i = [], False, 0
        while i < len(line):
            ch = line[i]
            if in_str:
                res.append(ch)
                if ch == '"' and (i == 0 or line[i - 1] != "\\"):
                    in_str = False
            elif ch == '"':
                in_str = True
                res.append(ch)
            elif ch == "/" and line[i:i + 2] == "//":
                break
            else:
                res.append(ch)
            i += 1
        out.append("".join(res))
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "\n".join(out)))


def unit_strengths() -> dict:
    if UNITS_CACHE.exists():
        return json.loads(UNITS_CACHE.read_text(encoding="utf-8"))
    return {}


def wonders_set(force: bool = False) -> set[str]:
    """从基础规则集 + 模组的 Buildings.json 里取 isWonder 的建筑名。"""
    if WONDERS_CACHE.exists() and not force:
        return set(json.loads(WONDERS_CACHE.read_text(encoding="utf-8")))
    found: set[str] = set()
    for d in RULESET_DIRS:
        f = d / "Buildings.json"
        if not f.exists():
            continue
        try:
            for entry in _jsonc(f.read_text(encoding="utf-8", errors="replace")):
                if isinstance(entry, dict) and entry.get("isWonder") and entry.get("name"):
                    found.add(entry["name"])
        except Exception:
            continue
    if found:
        WONDERS_CACHE.write_text(json.dumps(sorted(found), ensure_ascii=False, indent=1), encoding="utf-8")
    return found


# ------------------------------------------------------------------ 指标

def _tokens(entry: str) -> dict[str, int]:
    return {k: int(v) for k, v in TOKEN.findall(entry or "")}


def standings(doc: dict, wonders: set[str] | None = None) -> dict:
    wonders = wonders if wonders is not None else wonders_set()
    strengths = unit_strengths()

    owners: dict[str, list[str]] = {}
    for tile in (doc.get("tileMap") or {}).get("tileList") or []:
        for key in ("militaryUnit", "civilianUnit"):
            unit = tile.get(key)
            if isinstance(unit, dict) and unit.get("owner") and unit.get("name"):
                owners.setdefault(unit["owner"], []).append(unit["name"])

    out: dict[str, dict] = {}
    for civ in doc.get("civilizations") or []:
        name = civ.get("civName") or civ.get("civID")
        cities = civ.get("cities") or []
        population = sum(int((c.get("population") or {}).get("population", 0) or 0) for c in cities)
        built = [b for c in cities
                 for b in ((c.get("cityConstructions") or {}).get("builtBuildings") or [])]
        civ_units = owners.get(name, [])
        might = 0
        for unit_name in civ_units:
            base = strengths.get(unit_name) or {}
            might += max(float(base.get("strength", 0) or 0), float(base.get("rangedStrength", 0) or 0))
        captured = sum(1 for c in cities if (c.get("foundingCiv") or name) not in ("", name))
        history = civ.get("statsHistory") or {}
        last_turn = max((int(t) for t in history), default=None)
        entry = history.get(str(last_turn), "") if last_turn is not None else ""
        is_cs = bool(civ.get("isCityState")) or bool(civ.get("cityStatePersonality"))

        out[name] = {
            "civ": name,
            "playerType": civ.get("playerType"),
            "isCityState": is_cs,
            "cityStatePersonality": civ.get("cityStatePersonality"),
            "cities": len(cities),
            "population": population,
            "techs": len((civ.get("tech") or {}).get("techsResearched") or []),
            "policies": len((civ.get("policies") or {}).get("adoptedPolicies") or []),
            "gold": civ.get("gold"),
            "storedCulture": (civ.get("policies") or {}).get("storedCulture"),
            "storedFaith": (civ.get("religionManager") or {}).get("storedFaith"),
            "wonders": sum(1 for b in built if b in wonders),
            "militaryUnits": len(civ_units),
            "militaryMight": int(might),
            "capturedCities": captured,
            "engineScore": _tokens(entry).get("S"),
            "defeated": bool(civ.get("isDefeated")) or not cities,
            # 城邦判据用（实测城邦 0/24 有、主要文明 32/32 有）
            "hasStatsHistory": bool(history),
            "hasEspionage": bool(civ.get("espionageManager")),
            "statsHistoryRaw": entry,
        }
    # 灭国数：其他**主要文明**里已灭亡的数量（城邦不算）；与 ArenaLauncher 的"在场者都计入"同一口径
    majors = [n for n, r in out.items() if not r["isCityState"]]
    for name, row in out.items():
        row["eliminatedNations"] = sum(1 for other in majors
                                       if other != name and out[other].get("defeated"))
    return out


def to_stats_rows(doc: dict, rows: dict, majors_only: bool = True) -> list[dict]:
    """合成一行/civ 的 stats 记录（字段与 arena-stats jsonl 对齐），让 scoring/report 直接可用。

    majors_only=True 时只写主要文明 —— 与 ArenaLauncher 一致，否则城邦会拉低局内中位、污染归一化。
    """
    turn = int(doc.get("turns") or 0)
    out = []
    for name, r in rows.items():
        if majors_only and not is_major(name, r):
            continue
        out.append({
            "turn": turn, "civID": name,
            "gold": r.get("gold") or 0, "goldPerTurn": 0,
            "sciencePerTurn": 0, "culturePerTurn": 0,
            "cultureStored": r.get("storedCulture") or 0,
            "faith": r.get("storedFaith") or 0,
            "techs": r.get("techs", 0), "cities": r.get("cities", 0),
            "population": r.get("population", 0), "wonders": r.get("wonders", 0),
            "engineScore": r.get("engineScore") or 0,
            "militaryUnits": r.get("militaryUnits", 0),
            "militaryMight": r.get("militaryMight", 0),
            "capturedCities": r.get("capturedCities", 0),
            "eliminatedNations": r.get("eliminatedNations", 0),
        })
    return out


def save_id(doc: dict) -> str:
    return str(doc.get("gameId") or "?")[:8]


def process(path: Path, write: bool = False, wonders: set[str] | None = None) -> dict:
    doc = load_save(path)
    rows = standings(doc, wonders)
    synced_rows = to_stats_rows(doc, rows)
    gid = save_id(doc)
    victory = doc.get("victoryData") or {}
    result = {
        "save": str(path),
        "gameId": gid,
        "turns": doc.get("turns"),
        "mods": (doc.get("gameParameters") or {}).get("mods"),
        "difficulty": doc.get("gameParameters", {}).get("difficulty") or doc.get("difficulty"),
        "winner": victory.get("winningCiv"),
        "victoryType": victory.get("victoryType"),
        "victoryTurn": victory.get("victoryTurn"),
        "standings": rows,
        "conquest": conquest_metrics(path),
        "statsRows": synced_rows,
    }
    if write:
        STATS_DIR.mkdir(parents=True, exist_ok=True)
        stats_path = STATS_DIR / f"{gid}.jsonl"
        stats_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in synced_rows),
                              encoding="utf-8")
        result["statsPath"] = str(stats_path)
        record = gamelog._decorate({
            "tag": f"save-{gid}", "kind": "save", "gameId": gid,
            "map": ((doc.get("tileMap") or {}).get("mapParameters") or {}).get("name"),
            "players": list(rows), "roles": {},
            "difficulty": result["difficulty"],
            "maxTurns": None, "mods": result["mods"], "seed": None,
            "log": None, "stats": str(stats_path), "final": str(path),
            "finishedTurn": doc.get("turns"), "turnCount": doc.get("turns"),
            "winner": result["winner"],
            "outcome": (f"{result['victoryType']} victory by {result['winner']}"
                        if result["winner"] else "no victory recorded"),
            "source": "save", "note": "single-snapshot from save (no per-turn arena-stats)",
        })
        gamelog.append_record(record)
        result["indexed"] = True
    return result


def conquest_metrics(path: Path | str) -> dict[str, dict]:
    """终局征服指标——按【最终归属】算，不按易手次数。

    净占领 = 占领 − 被占领：
      占领   = 手里的城且 foundingCiv ≠ 自己（"持外国城"）
      被占领 = citiesCreated − 手里自建城（我创立但已不在我手里）

    为什么拉锯不膨胀：`foundingCiv` 永不改变、`citiesCreated` 只计一次，
    所以 A→B→A 的城最终归 A（A/B 各计 0），A→B 不还则 B +1 占领、A +1 被占领。
    存活口径（用户定）：`isDefeated` 为假且仍有城。

    数据来源：**终局存档**（`arena-final-*` / 客户端存档）。arena-stats 里没有
    `citiesCreated`，所以拿不到存档的局只能退回"只算占领"（高估）。
    """
    doc = load_save(Path(path))
    out: dict[str, dict] = {}
    for civ in doc.get("civilizations") or []:
        name = civ.get("civName") or civ.get("civID")
        flags = {
            "cityStatePersonality": civ.get("cityStatePersonality"),
            "hasStatsHistory": bool(civ.get("statsHistory")),
            "hasEspionage": bool(civ.get("espionageManager")),
        }
        if not is_major(name, flags):
            continue
        cities = civ.get("cities") or []
        created = civ.get("citiesCreated")
        own_now = sum(1 for c in cities if (c.get("foundingCiv") or name) in ("", name))
        captured_now = len(cities) - own_now
        lost = (int(created) - own_now) if isinstance(created, int) else None
        out[name] = {
            "citiesCreated": created if isinstance(created, int) else None,
            "cities": len(cities),
            "ownFounded": own_now,
            "captured": captured_now,
            "lost": lost,
            "net": (captured_now - lost) if lost is not None else None,
            "alive": (not bool(civ.get("isDefeated"))) and len(cities) > 0,
            "source": "save",
        }
    return out


def rank(results: list[dict], method: str = "median", include_minors: bool = False) -> list[tuple[str, float, int]]:
    """把多个存档按"局内相对势 + 征服加成"算国家均分（与 arena 同一口径）。

    默认只算主要文明：城邦参与归一化会把局内中位拉低，使强者显得更弱。
    """
    import statistics
    acc: dict[str, list[float]] = {}
    for res in results:
        flags = res.get("standings") or {}
        rows_all = scoring.group_by_civ(res["statsRows"])
        by_civ = {c: rows for c, rows in rows_all.items()
                  if include_minors or is_major(c, flags.get(c, {}))}
        if len(by_civ) < 2:
            continue
        powers = {c: scoring.power(rows[-1], civ=c) for c, rows in by_civ.items()}
        norm = scoring.normalize(powers, method)
        median = statistics.median(list(powers.values()))
        for civ, rows in by_civ.items():
            last = rows[-1]
            met = (res.get("conquest") or {}).get(civ) or {}
            cap = met.get("captured")
            cap_used = int(cap) if cap is not None else int(last.get("capturedCities", 0) or 0)
            lost = int(met.get("lost") or 0)
            score = norm[civ] + scoring.conquest_ratio(cap_used, lost,
                                                       int(last.get("eliminatedNations", 0) or 0), median)
            acc.setdefault(civ, []).append(score)
    return sorted(((c, round(statistics.fmean(v), 4), len(v)) for c, v in acc.items()),
                  key=lambda t: -t[1])


def main() -> None:
    parser = argparse.ArgumentParser(description="读存档出榜（客户端手打局也能进排名）")
    parser.add_argument("target", help="存档文件或目录")
    parser.add_argument("--method", default="median", choices=["median", "z"])
    parser.add_argument("--write-index", action="store_true",
                        help="生成 stats jsonl 并写入 arena/index.jsonl（kind=save，report.py 能直接纳入）")
    parser.add_argument("--json", action="store_true", help="打印完整明细 JSON")
    args = parser.parse_args()

    target = Path(args.target)
    files = sorted(p for p in target.iterdir() if p.is_file()) if target.is_dir() else [target]
    if not files:
        print("没有存档文件")
        return
    wonders = wonders_set()
    print(f"存档 {len(files)} 个 | 奇迹表 {len(wonders)} 条 | 单位强度表 {len(unit_strengths())} 条\n")

    results: list[dict] = []
    for path in files:
        try:
            res = process(path, write=args.write_index, wonders=wonders)
        except Exception as exc:
            print(f"  [!] {path.name}: {exc}")
            continue
        results.append(res)
        ranked = sorted(res["statsRows"], key=lambda r: -scoring.power(r))
        head = ", ".join(f"{r['civID']}({scoring.power(r):.0f})" for r in ranked[:4])
        print(f"  {path.name[:26]:<28} T{res['turns']:<4} 胜者={str(res['winner'] or '-'):<14} {head}")

    if len(results) > 1:
        print("\n=== 汇总排名（局内归一化均分 + 征服加成，与 arena 同一口径）===")
        for i, (civ, score, games) in enumerate(rank(results, args.method), 1):
            print(f"  {i:<3} {civ:<16} {score:<8.3f} 局数={games}")

    if args.write_index:
        print(f"\n已写入 arena/index.jsonl（kind=save）；合成的 stats jsonl -> {STATS_DIR}")
        print("看榜：python arena/report.py --kind save")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
