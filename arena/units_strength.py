"""从规则集 Units.json（含 // 注释）提取 单位名 → 基础强度 映射，缓存为 arena/units-strength.json

规则集路径通过环境变量指定（不再写死本机路径）：
    CIVAGENT_UNITS_JSON  以 os.pathsep 分隔的 Units.json 列表
    CIVAGENT_GAME_ROOT   未给 CIVAGENT_UNITS_JSON 时的根目录（默认当前目录）
"""
import json
import os
import re
from pathlib import Path

DEFAULT_RULESET_PATHS = [
    'Unciv-Source/android/assets/jsons/Civ V - Gods & Kings/Units.json',
    'Unciv-SongDynasty/mods/unciv unrepentant cathay/jsons/Units.json',
]


def ruleset_paths():
    explicit = os.environ.get('CIVAGENT_UNITS_JSON', '')
    if explicit:
        return [p for p in explicit.split(os.pathsep) if p]
    root = Path(os.environ.get('CIVAGENT_GAME_ROOT') or Path.cwd())
    return [str(root / rel) for rel in DEFAULT_RULESET_PATHS]


def strip_comments(text):
    out = []
    for line in text.splitlines():
        res = []
        in_str = False
        i = 0
        while i < len(line):
            ch = line[i]
            if in_str:
                res.append(ch)
                if ch == '"' and (i == 0 or line[i - 1] != '\\'):
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                    res.append(ch)
                elif ch == '/' and line[i:i + 2] == '//':
                    break
                else:
                    res.append(ch)
            i += 1
        out.append(''.join(res))
    stripped = '\n'.join(out)
    return re.sub(r',(\s*[}\]])', r'\1', stripped)


def load_jsonc(path):
    text = open(path, encoding='utf-8').read()
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)  # 块注释
    return json.loads(strip_comments(text))


def build_units_map():
    units_map = {}
    paths = ruleset_paths()
    for p in paths:
        try:
            for x in load_jsonc(p):
                if isinstance(x, dict) and 'name' in x:
                    units_map.setdefault(x['name'], x)
        except Exception as e:
            print(f'skip {p}: {e}')
    return units_map


if __name__ == '__main__':
    m = build_units_map()
    out = Path(__file__).resolve().parent / 'units-strength.json'
    out.write_text(json.dumps(m, ensure_ascii=False), encoding='utf-8')
    print(f'saved {out} ({len(m)} units)')
