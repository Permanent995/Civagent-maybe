"""Arena map generator — true vertical-midline left/right mirror (screen space).

Unciv renders pointy-top hexes at screenX = x + y/2, screenY = y (axial coords).
A visual flip across the **vertical center line** is therefore:

    (x, y) -> (-x - y, y)     # screenX -> -screenX, same row

This generator:
  * uses that pure mirror (no cylinder wrap)
  * sets worldWrap=false so the midline you see in the UI is the real symmetry axis
  * writes content only on mirror-closed pairs that actually cross screenX=0
  * migrates spawn slots in L/R pairs so the right seat is always the exact mirror
    of the left seat (fixes the old “Ethiopia walked off to (27,0)” bug)

Usage:
  python arena/map_generator.py [--out maps/arena-64x64.json] [--width 64] [--height 64]
                                [--wonder-pairs 5] [--seed 42] [--no-starts] [--players 4p]
                                [--roster-file path.json] [--verify]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

LAND_WONDERS = ["Barringer Crater", "Mount Everest", "El Dorado", "Solomon's Mines",
                "Fountain of Youth", "Cerro de Potosi"]
SEA_WONDERS = ["Great Barrier Reef"]
LUXURIES = ["Silk", "Dyes", "Gems", "Ivory", "Marble", "Wine", "Incense", "Sugar",
            "Cotton", "Furs", "Spices", "Whales", "Pearls"]
STRATEGICS = ["Horses", "Iron", "Coal", "Oil", "Aluminum"]
BONUSES = ["Wheat", "Cattle", "Sheep", "Deer", "Fish", "Bananas", "Stone", "Game"]

CONTENT_KEYS = ("baseTerrain", "terrainFeatures", "resource", "resourceAmount",
                "naturalWonder", "improvement")


def tile_position(column: int, row: int) -> dict:
    two_rows = row * 2 + (1 if abs(column) % 2 == 1 else 0)
    return {"x": (two_rows - column) // 2, "y": (two_rows + column) // 2}


def column_range(width: int):
    return range(-(width // 2), (width - 1) // 2 + 1)


def row_range(height: int):
    return range(-(height // 2), (height - 1) // 2 + 1)


def content_sig(tile: dict) -> tuple:
    feats = tuple(sorted(tile.get("terrainFeatures") or []))
    return (
        tile.get("baseTerrain"),
        feats,
        tile.get("resource"),
        tile.get("resourceAmount"),
        tile.get("naturalWonder"),
        tile.get("improvement"),
    )


class ArenaMap:
    def __init__(self, width: int, height: int, seed: int):
        if width % 2 != 0:
            raise ValueError(f"width must be even for a clean midline (got {width})")
        self.width = width
        self.height = height
        self.rng = random.Random(seed)
        self.tiles = {}
        for column in column_range(width):
            for row in row_range(height):
                pos = tile_position(column, row)
                self.tiles[(pos["x"], pos["y"])] = {
                    "position": pos, "baseTerrain": "Ocean", "terrainFeatures": []
                }

    # ---- screen-space geometry ----
    def screen_x(self, key) -> float:
        return key[0] + key[1] / 2

    def mirror_key(self, key):
        """True visual left-right mirror across screenX=0 (no wrap)."""
        x, y = key
        return (-x - y, y)

    def pairs(self):
        """Mirror-closed pairs that actually straddle the vertical midline."""
        out = []
        for key in sorted(self.tiles):
            mkey = self.mirror_key(key)
            if mkey not in self.tiles or key == mkey:
                continue
            if self.screen_x(key) < 0 and self.screen_x(mkey) > 0:
                out.append((key, mkey))
        return out

    # ---- content ----
    def build_terrain(self):
        half_h = self.height // 2
        # LEFT half only — right half is copied by mirror assignment
        blob_centers = [
            (-self.width // 4, -half_h // 2, self.width // 6, half_h // 2 + 2),
            (-self.width // 4, half_h // 2, self.width // 6, half_h // 2 + 2),
            # mid-latitude bands — slots sit on y≈±8 for 64×64; without these the
            # third L/R pair finds no settleable land after the midline rewrite
            (-self.width // 4, -half_h // 4, self.width // 5, half_h // 3),
            (-self.width // 4, half_h // 4, self.width // 5, half_h // 3),
            (-self.width // 2 + 6, 0, self.width // 10, half_h - 4),
            (-self.width // 5, 0, self.width // 8, half_h // 3),
        ]
        neighbours = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1)]
        for (key, mkey) in self.pairs():
            x, y = key
            tile, mtile = self.tiles[key], self.tiles[mkey]
            for (bcx, bcy, brx, bry) in blob_centers:
                noise = 1.0 + 0.25 * self.rng.random()
                if (((x - bcx) / (brx * noise)) ** 2 + ((y - bcy) / (bry * noise)) ** 2) < 1.0:
                    tile["_land"] = True
                    mtile["_land"] = True
            lat = min(1.0, abs(y) / (self.height / 2))
            roll = self.rng.random()
            coast = any(self.tiles.get(n, {}).get("_land")
                        for n in [(x + dx, y + dy) for dx, dy in neighbours] +
                                 [self.mirror_key((x + dx, y + dy)) for dx, dy in neighbours])
            is_land = tile.get("_land", False)
            if not is_land:
                terrain = "Coast" if coast else "Ocean"
            elif lat > 0.82:
                terrain = "Snow" if roll < 0.5 else "Tundra"
            elif lat > 0.62:
                terrain = "Tundra" if roll < 0.55 else "Plains"
            elif 0.3 < lat <= 0.42 and roll < 0.6:
                terrain = "Desert"
            else:
                terrain = "Grassland" if roll < 0.6 else "Plains"
            features = []
            if is_land:
                fr = self.rng.random()
                if terrain == "Grassland" and 0.3 < lat < 0.55 and fr < 0.25:
                    features = ["Jungle"]
                elif fr < 0.22:
                    features = ["Forest"]
                elif fr < 0.3:
                    features = ["Hill"]
                elif terrain == "Grassland" and fr < 0.34:
                    features = ["Marsh"]
                elif terrain == "Desert" and fr < 0.06:
                    features = ["Oasis"]
            elif lat > 0.9:
                features = ["Ice"]
            tile["baseTerrain"] = mtile["baseTerrain"] = terrain
            tile["terrainFeatures"] = features
            mtile["terrainFeatures"] = list(features)

        # orphans / on-axis tiles: no crossing mirror → plain ocean
        for key in list(self.tiles):
            mkey = self.mirror_key(key)
            if mkey not in self.tiles or key == mkey or not (
                (self.screen_x(key) < 0 and self.screen_x(mkey) > 0)
                or (self.screen_x(key) > 0 and self.screen_x(mkey) < 0)
            ):
                self.tiles[key] = {
                    "position": {"x": key[0], "y": key[1]},
                    "baseTerrain": "Ocean", "terrainFeatures": [],
                }

        land = [k for k, _ in self.pairs()
                if self.tiles[k]["baseTerrain"] not in ("Coast", "Ocean", "Mountain")]

        def pin(key, terrain, features):
            for k in (key, self.mirror_key(key)):
                self.tiles[k]["baseTerrain"] = terrain
                self.tiles[k]["terrainFeatures"] = list(features)

        catalog = [("Desert", []), ("Tundra", []), ("Snow", []), ("Plains", ["Hill"]),
                   ("Grassland", ["Forest"]), ("Grassland", ["Jungle"]),
                   ("Grassland", ["Marsh"]), ("Desert", ["Oasis"]),
                   ("Tundra", ["Hill"]), ("Plains", ["Forest", "Hill"])]
        for i, (terrain, features) in enumerate(catalog):
            if i < len(land):
                pin(land[i], terrain, features)
        ridge = [k for k, _ in self.pairs()
                 if self.tiles[k].get("_land") and abs(self.screen_x(k)) > self.width / 4]
        for k in ridge[2:4]:
            for m in (k, self.mirror_key(k)):
                self.tiles[m]["baseTerrain"] = "Mountain"
                self.tiles[m]["terrainFeatures"] = []

    def place_wonders(self, count_pairs: int = 5):
        def usable(k):
            t = self.tiles[k]
            m = self.tiles.get(self.mirror_key(k))
            if m is None:
                return False
            clean = "naturalWonder" not in t and "resource" not in t
            mclean = "naturalWonder" not in m and "resource" not in m
            k_land = t["baseTerrain"] not in ("Coast", "Ocean", "Mountain")
            m_land = m["baseTerrain"] not in ("Coast", "Ocean", "Mountain")
            in_band = abs(self.screen_x(k)) < self.width / 4
            return (clean and mclean and k_land == m_land
                    and self.screen_x(k) < 0 and self.screen_x(self.mirror_key(k)) > 0
                    and in_band)

        land_pool = sorted(k for k in self.tiles if usable(k)
                           and self.tiles[k]["baseTerrain"] not in ("Coast", "Ocean"))
        sea_pool = sorted(k for k in self.tiles if usable(k)
                          and self.tiles[k]["baseTerrain"] in ("Coast", "Ocean"))
        for i in range(count_pairs):
            use_sea = (i % 2 == 1 and sea_pool) or (i >= 2 and not land_pool)
            pool = sea_pool if use_sea else land_pool
            if not pool:
                print(f"WARNING: wonder pair {i + 1} skipped (no usable slots)")
                continue
            base = pool[self.rng.randrange(len(pool))]
            mirror = self.mirror_key(base)
            wonder = SEA_WONDERS[0] if use_sea else LAND_WONDERS[(i * 2) % len(LAND_WONDERS)]
            for k in (base, mirror):
                tile = self.tiles[k]
                tile["naturalWonder"] = wonder
                tile.pop("resource", None)
                tile["terrainFeatures"] = []
                if not use_sea and tile["baseTerrain"] == "Mountain":
                    tile["baseTerrain"] = "Grassland"
            land_pool = [k for k in land_pool if usable(k)]
            sea_pool = [k for k in sea_pool if usable(k)]

    def scatter_resources(self):
        def pair_pool(predicate):
            return sorted(k for k, t in self.tiles.items()
                          if predicate(t) and "naturalWonder" not in t and "resource" not in t
                          and self.mirror_key(k) in self.tiles
                          and self.screen_x(k) < 0
                          and self.screen_x(self.mirror_key(k)) > 0
                          and abs(self.screen_x(k)) < self.width / 4)

        land_keys = pair_pool(lambda t: t["baseTerrain"] not in ("Coast", "Ocean", "Mountain"))
        sea_keys = pair_pool(lambda t: t["baseTerrain"] in ("Coast", "Ocean"))

        def scatter(pool, count, pick):
            for _ in range(count):
                pool = [k for k in pool if "resource" not in self.tiles[k]
                        and "resource" not in self.tiles[self.mirror_key(k)]]
                if not pool:
                    return
                key = pool[self.rng.randrange(len(pool))]
                mirror = self.mirror_key(key)
                resource = pick()
                amount = 2 if resource in STRATEGICS else None
                for k in (key, mirror):
                    self.tiles[k]["resource"] = resource
                    if amount:
                        self.tiles[k]["resourceAmount"] = amount
                    else:
                        self.tiles[k].pop("resourceAmount", None)

        scatter(land_keys, max(6, len(land_keys) // 18), lambda: self.rng.choice(LUXURIES))
        scatter(land_keys, max(6, len(land_keys) // 20), lambda: self.rng.choice(STRATEGICS))
        scatter(land_keys, max(8, len(land_keys) // 12),
                lambda: self.rng.choice(BONUSES[:-1]))
        scatter(sea_keys, max(4, len(sea_keys) // 30), lambda: "Fish")

    def _land_in_radius(self, key, radius: int = 3) -> int:
        x, y = key
        count = 0
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                k = (x + dx, y + dy)
                t = self.tiles.get(k)
                if t and t["baseTerrain"] not in ("Coast", "Ocean", "Mountain"):
                    count += 1
        return count

    def add_starting_locations(self, roster):
        """Migrate LEFT seats; RIGHT seats become exact mirrors of the left seats.

        Roster entries are paired by row (y): each left (screenX<0) request is matched
        to one right (screenX>=0) request on the same row. Migration only searches the
        left half; the right nation is forced to mirror_key(best_left).
        """
        by_row: dict[int, list] = {}
        for nation, (x, y) in roster:
            by_row.setdefault(y, []).append((nation, (x, y)))

        migrated = []
        # Per-row x occupancy only — seats on different latitudes must not block each other
        # (L1 at y=-8 x=-11 used to veto L3 at y=8 x=-14 when spacing was global).
        used_left_x_by_row: dict[int, list[int]] = {}
        for y in sorted(by_row):
            entries = by_row[y]
            lefts = [(n, p) for n, p in entries if self.screen_x(p) < 0]
            rights = [(n, p) for n, p in entries if self.screen_x(p) >= 0]
            if len(lefts) != len(rights):
                print(f"WARNING: row y={y} has {len(lefts)} left / {len(rights)} right seats "
                      f"— cannot form mirror pairs")
            row_used = used_left_x_by_row.setdefault(y, [])
            for i, (nation_l, (x, _)) in enumerate(lefts):
                if i >= len(rights):
                    break
                nation_r, _ = rights[i]
                best, best_dist, best_land = None, None, 0
                for cand_x in range(x - 16, x + 17):
                    cand = (cand_x, y)
                    mcand = self.mirror_key(cand)
                    if cand not in self.tiles or mcand not in self.tiles:
                        continue
                    if not (self.screen_x(cand) < 0 and self.screen_x(mcand) > 0):
                        continue
                    if (self.tiles[cand]["baseTerrain"] in ("Coast", "Ocean", "Mountain")
                            or self.tiles[mcand]["baseTerrain"] in ("Coast", "Ocean", "Mountain")):
                        continue
                    land_pair = self._land_in_radius(cand) + self._land_in_radius(mcand)
                    if land_pair < 30:
                        continue
                    if any(abs(cand_x - ux) < 8 for ux in row_used):
                        continue
                    dist = abs(cand_x - x)
                    if best is None or (dist, -land_pair) < (best_dist, -best_land):
                        best, best_dist, best_land = cand, dist, land_pair
                if best is None:
                    print(f"WARNING: no viable mirrored spawn for {nation_l}/{nation_r} on row {y}")
                    continue
                right = self.mirror_key(best)
                row_used.append(best[0])
                for k in (best, right):
                    tile = self.tiles[k]
                    if tile["baseTerrain"] in ("Coast", "Ocean", "Mountain"):
                        tile["baseTerrain"] = "Grassland"
                    tile["terrainFeatures"] = [f for f in tile.get("terrainFeatures", []) if f == "Hill"]
                    tile.pop("naturalWonder", None)
                migrated.append((nation_l, best))
                migrated.append((nation_r, right))
                print(f"[spawn] {nation_l}: ({x},{y}) -> {best} | {nation_r} -> {right} "
                      f"(land r3 = {self._land_in_radius(best)}/{self._land_in_radius(right)})")
        return migrated

    def verify_symmetry(self) -> list[str]:
        """Return a list of human-readable failures (empty = OK)."""
        failures = []
        left_land = right_land = 0
        mismatches = 0
        for key, tile in self.tiles.items():
            mkey = self.mirror_key(key)
            sx = self.screen_x(key)
            is_land = tile.get("baseTerrain") not in ("Coast", "Ocean")
            if is_land:
                if sx < 0:
                    left_land += 1
                elif sx > 0:
                    right_land += 1
            if mkey not in self.tiles:
                if is_land:
                    failures.append(f"land tile {key} has no mirror cell")
                continue
            if key >= mkey:
                continue
            if content_sig(tile) != content_sig(self.tiles[mkey]):
                mismatches += 1
                if mismatches <= 5:
                    failures.append(
                        f"content mismatch {key}<->{mkey}: "
                        f"{content_sig(tile)} vs {content_sig(self.tiles[mkey])}"
                    )
        if mismatches:
            failures.append(f"total content mismatches: {mismatches}")
        if left_land != right_land:
            failures.append(f"land count L/R = {left_land}/{right_land} (must be equal)")
        # both-on-same-half check for land
        both_left = 0
        for key, tile in self.tiles.items():
            if tile.get("baseTerrain") in ("Coast", "Ocean"):
                continue
            mkey = self.mirror_key(key)
            if mkey not in self.tiles:
                continue
            if self.screen_x(key) < 0 and self.screen_x(mkey) < 0:
                both_left += 1
        if both_left:
            failures.append(f"{both_left} land tiles have mirror still on left half (wrap bug)")
        return failures

    def render(self, roster, wonder_pairs: int = 5) -> dict:
        self.build_terrain()
        self.place_wonders(wonder_pairs)
        self.scatter_resources()
        roster = self.add_starting_locations(roster)
        failures = self.verify_symmetry()
        if failures:
            raise RuntimeError("symmetry check failed:\n  - " + "\n  - ".join(failures))
        tile_list = []
        for key in sorted(self.tiles.keys()):
            tile_list.append({k: v for k, v in self.tiles[key].items()
                              if v is not None and not str(k).startswith("_")})
        return {
            "mapParameters": {
                "name": "arena",
                "shape": "Rectangular",
                "mapSize": {"name": f"Arena{self.width}x{self.height}", "radius": 0,
                            "width": self.width, "height": self.height},
                "worldWrap": False,
                "createdWithVersion": "4.21.4-patch1 (Build 1240)",
                "seed": self.rng.randrange(1 << 30),
            },
            "tileList": tile_list,
            "startingLocations": [
                {"position": {"x": x, "y": y}, "nation": nation, "usage": "Normal"}
                for nation, (x, y) in roster if (x, y) in self.tiles
            ],
        }


def mirror_of(x: int, y: int) -> tuple[int, int]:
    return (-x - y, y)


# left seats; right seats are exact mirrors under screenX flip
DEFAULT_ROSTER_2P = [
    ("Sheng Tang", (-14, -8)),
    ("MongolEmpire", mirror_of(-14, -8)),
]
DEFAULT_ROSTER_4P = [
    ("Sheng Tang", (-14, -10)),
    ("MongolEmpire", mirror_of(-14, -10)),
    ("Qindynasty", (-14, 8)),
    ("Late Tang", mirror_of(-14, 8)),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="maps/arena-64x64.json")
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--height", type=int, default=64)
    parser.add_argument("--wonder-pairs", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-starts", action="store_true",
                        help="omit starting locations (editor-friendly)")
    parser.add_argument("--players", default="4p", choices=["2p", "4p"])
    parser.add_argument("--roster-file",
                        help='JSON [{"nation":..,"x":..,"y":..}] - overrides --players')
    parser.add_argument("--verify", action="store_true",
                        help="only verify an existing map file (--out) and exit")
    args = parser.parse_args()

    if args.verify:
        path = Path(args.out)
        doc = json.loads(path.read_text(encoding="utf-8"))
        w = doc["mapParameters"]["mapSize"]["width"]
        h = doc["mapParameters"]["mapSize"]["height"]
        am = ArenaMap(w, h, seed=0)
        am.tiles = {}
        for t in doc["tileList"]:
            key = (t["position"]["x"], t["position"]["y"])
            am.tiles[key] = t
        fails = am.verify_symmetry()
        wrap = doc["mapParameters"].get("worldWrap")
        starts = doc.get("startingLocations") or []
        print(f"{path.name}: wrap={wrap} starts={len(starts)} "
              f"pairs={len(am.pairs())}")
        for s in starts:
            k = (s["position"]["x"], s["position"]["y"])
            print(f"  {s.get('nation')} @ {k} screenX={am.screen_x(k):.1f} "
                  f"mirror={am.mirror_key(k)}")
        if fails:
            print("FAIL:")
            for f in fails:
                print("  -", f)
            raise SystemExit(1)
        print("OK: vertical-midline symmetric")
        return

    roster = [] if args.no_starts else (
        DEFAULT_ROSTER_4P if args.players == "4p" else DEFAULT_ROSTER_2P)
    if args.roster_file:
        roster = [(e["nation"], (e["x"], e["y"]))
                  for e in json.loads(Path(args.roster_file).read_text(encoding="utf-8"))]
    arena = ArenaMap(args.width, args.height, args.seed)
    doc = arena.render(roster, wonder_pairs=args.wonder_pairs)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    wonders = sum(1 for t in doc["tileList"] if "naturalWonder" in t)
    print(f"written {out}: {len(doc['tileList'])} tiles, {wonders} wonders "
          f"({args.wonder_pairs} pairs), {len(doc['startingLocations'])} starts, "
          f"worldWrap={doc['mapParameters']['worldWrap']}")


if __name__ == "__main__":
    main()
