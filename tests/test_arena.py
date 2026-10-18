"""训练场（arena/）单测：评分口径、索引去重、覆盖率、自动权重。

这些用例锁住的是**已经踩过的坑**，不是形式覆盖：
- 同一局被记两次（key_of 曾用 tag 当主键，索引从 54 涨到 100）
- 征服加成被算两次（cross_report 曾 `rel+bonus` 后再 `mean+0.2*城+90/500*国`）
- 征服再叠进主榜分数（占城已含在城/人/军里；score/powerRel 不再加 bonus）
- 主榜按势均值排序（一场雪崩会压过稳定名次；现主轴=均名次）
- 拟合标签用 engineScore（特征里也有它，会自证；现标签=存活且城数最多）
- 缺 stats 的局被静默跳过（现在必须出现在覆盖率里）
"""

import importlib
import json
from pathlib import Path

import pytest

# arena 是包（有 __init__.py）：不再往 sys.path 里塞目录，也不用 importlib 拼模块名。
from arena import gamelog, nation_strength, paths, scoring
from arena import report as report_mod

ARENA = Path(__file__).resolve().parents[1] / "arena"


def row(turn, civ, **overrides):
    base = {
        "turn": turn, "civID": civ, "gold": 0, "goldPerTurn": 0, "sciencePerTurn": 0,
        "culturePerTurn": 0, "cultureStored": 0, "faith": 0, "techs": 3, "cities": 1,
        "population": 1, "wonders": 0, "engineScore": 0, "militaryUnits": 0,
        "militaryMight": 0, "capturedCities": 0, "eliminatedNations": 0,
    }
    base.update(overrides)
    return base


def write_stats(path: Path, rows) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def write_log(path: Path, game_id="4949f3e7", players="France, Rome", seats="", native="France, Rome",
              winner=None, finished_turn=150, stats_path=None, finished: bool = True,
              map_name="arena-64x64.json") -> Path:
    """写一份 ArenaLauncher stdout 日志。finished=False 造「被预算切断」的半局（无 outcome）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"[arena] starting: map={map_name} players={players}, Spectator",
        f"[arena] gameId={game_id} difficulty=Emperor civagentSeats=[{seats}] native=[{native}]",
        "[arena] turn 1 - France: g3 c1 p1 | Rome: g0 c0 p0",
        f"[arena] turn {finished_turn} - France: g99 c3 p9 | Rome: g1 c1 p2",
    ]
    if finished:
        if winner:
            lines.append(f"[arena] finished at turn {finished_turn}: VICTORY: {winner}")
        else:
            lines.append(f"[arena] finished at turn {finished_turn}: no victory (turn limit)")
    lines.append(f"[arena] stats: {stats_path}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def arena_env(tmp_path, monkeypatch):
    """把 arena 的路径指向 tmp_path，并重载 paths（它在 import 时就读环境变量）。"""
    monkeypatch.setenv("ARENA_SAVE_DIR", str(tmp_path / "SaveFiles"))
    monkeypatch.setenv("ARENA_LOGS", str(tmp_path / "logs"))
    monkeypatch.setenv("ARENA_INDEX", str(tmp_path / "index.jsonl"))
    monkeypatch.setenv("ARENA_LEGACY_LOGS", str(tmp_path / "no-legacy-logs"))
    importlib.reload(paths)
    yield tmp_path
    monkeypatch.undo()
    importlib.reload(paths)


# ---------------------------------------------------------------- scoring

def test_power_matches_hand_formula():
    """power = HAND_WEIGHTS 的加权和。从常量推导，所以改权重时这个用例自动跟随。"""
    entry = row(1, "France", population=3, cities=2, militaryMight=10, wonders=1, techs=5,
                cultureStored=10, faith=4, engineScore=100)
    expected = sum(entry[k] * w for k, w in scoring.HAND_WEIGHTS.items())
    assert scoring.power(entry) == pytest.approx(expected)


def test_power_and_fit_factors_drop_engine_score_from_fit_only():
    """power 仍含 engineScore；拟合特征必须去掉，避免标签/特征自证。"""
    assert scoring.POWER_CHECKPOINTS == (100, 150)
    assert scoring.CHECKPOINTS == (50, 100, 150)
    assert "engineScore" in scoring.FACTORS
    assert "engineScore" not in scoring.FIT_FACTORS
    assert scoring.FIT_FACTORS == tuple(f for f in scoring.FACTORS if f != "engineScore")


def test_hand_weights_are_the_agreed_values():
    """商定权重钉在测试里：改它必须是刻意决定。

    2026-09-14 第四版：按“目标占比”反解 + **引擎分提权**（1% → 10%）：
    军力 60% / 人口 8% / 科技 8% / 文化 5% / 奇观 4% / 信仰 4% / 引擎 10% / 城市 1%。
    历史版：weights-legacy-hand / -mil15 / -engine1.json。
    """
    assert scoring.HAND_WEIGHTS == {
        "population": 1.0087,
        "cities": 1.7846,
        "militaryMight": 1.4815,
        "wonders": 20.0,
        "techs": 3.7565,
        "cultureStored": 0.1101,
        "faith": 0.2237,
        "engineScore": 0.3066,
    }


def test_faith_nations_get_boosted_faith_weight_only():
    """信仰型国家的信仰项权重更大（用户定），且只影响信仰项。"""
    cfg = scoring.faith_config()
    assert cfg["nations"], "faith-nations.json 应至少列出 1 个国家"
    faith_civ = sorted(cfg["nations"])[0]
    plain = scoring.power_weights_for("France")
    boosted = scoring.power_weights_for(faith_civ)
    assert boosted["faith"] == pytest.approx(plain["faith"] * cfg["multiplier"])
    assert boosted["faith"] > plain["faith"]
    assert {k: v for k, v in boosted.items() if k != "faith"} == \
           {k: v for k, v in plain.items() if k != "faith"}


def test_power_applies_faith_boost_for_faith_nation():
    cfg = scoring.faith_config()
    faith_civ = sorted(cfg["nations"])[0]
    entry = row(0, "x", population=1, cities=1, techs=0, faith=1000)
    assert scoring.power(entry, civ=faith_civ) > scoring.power(entry)
    assert scoring.power(entry, civ="France") == pytest.approx(scoring.power(entry))


def test_power_accepts_custom_weights():
    entry = row(1, "France", population=2, cities=0, militaryMight=0, techs=0)
    assert scoring.power(entry, {"population": 1.0}) == pytest.approx(2.0)


def test_normalize_median_centers_on_one_and_z_on_zero():
    values = {"a": 10.0, "b": 20.0, "c": 30.0}
    assert scoring.normalize(values, "median")["b"] == pytest.approx(1.0)
    z = scoring.normalize(values, "z")
    assert sum(z.values()) == pytest.approx(0.0, abs=1e-9)
    assert scoring.normalize({"a": 5.0, "b": 5.0}, "z") == {"a": 0.0, "b": 0.0}


def test_conquest_wonders_rule():
    """新规则（2026-09-14 二次重定）：每占一城 +3 奇观；丢城 > 10 扣 3；灭国 +1/国。"""
    assert scoring.CAPTURE_WONDERS_PER_CITY == 3.0
    assert scoring.LOSS_PENALTY_THRESHOLD == 10
    assert scoring.conquest_wonders(0, 0, 0) == 0
    assert scoring.conquest_wonders(3, 0, 0) == 9        # 3 城 × 3
    assert scoring.conquest_wonders(3, 10, 0) == 9       # 丢 10 座：不扣（阈值是 >10）
    assert scoring.conquest_wonders(3, 11, 0) == 6       # 丢 11 座：扣 3
    assert scoring.conquest_wonders(3, 11, 2) == 8       # 扣 3 + 灭国 2
    assert scoring.conquest_wonders(0, 20, 0) == -3      # 只丢不占 → 负分


def test_conquest_ratio_uses_supplied_median_and_wonders_weight():
    """回归：cross_report 曾把 median 写死（90/500）。现在完全由传入 median + 奇观权重决定。

    占 1 城（3 奇观）× 20 + 灭 1 国（1 奇观）× 20 = 80 power。
    """
    assert scoring.ELIMINATION_BONUS_WONDERS == 1.0
    assert scoring.conquest_ratio(1, 0, 1, 100.0) == pytest.approx(80.0 / 100.0)
    assert scoring.conquest_ratio(1, 0, 1, 400.0) == pytest.approx(80.0 / 400.0)
    assert scoring.conquest_ratio(1, 0, 0, 100.0) == pytest.approx(60.0 / 100.0)


def test_placement_shares_ranks_on_ties():
    assert scoring.placement({"a": 3.0, "b": 2.0, "c": 2.0, "d": 1.0}) == {"a": 1, "b": 2, "c": 2, "d": 4}


def test_latest_at_forward_fills_and_returns_none_early():
    rows = [row(0, "France"), row(10, "France", population=5)]
    assert scoring.latest_at(rows, 5)["turn"] == 0
    assert scoring.latest_at(rows, 10)["population"] == 5
    assert scoring.latest_at(rows, -1) is None


def test_summarize_civ_reports_slope_and_conquest():
    rows = [row(t, "France", population=t, capturedCities=1 if t >= 50 else 0) for t in (0, 50, 100)]
    summary = scoring.summarize_civ(rows)
    assert summary["A_国力增速"] > 0
    assert summary["B_征服进度"]["capturedTotal"] == 2
    assert summary["B_征服进度"]["conquestSource"] == "stats-only"   # 没传终局征服指标
    assert summary["turns"] == (0, 100)


def test_summarize_civ_uses_save_conquest_metrics():
    rows = [row(t, "France", population=1, cities=1) for t in (0, 100)]
    summary = scoring.summarize_civ(
        rows, conquest_metrics={"captured": 5, "lost": 11, "alive": True, "source": "save"})
    prog = summary["B_征服进度"]
    assert prog["capturedFinal"] == 5 and prog["lost"] == 11
    assert prog["conquestSource"] == "save"
    assert prog["conquestWonders"] == 5 * 3 - 3        # 15 − 3（丢 11 > 10）
    assert prog["conquestPowerBonus"] == (5 * 3 - 3) * 20


def test_group_by_civ_sorts_by_turn():
    grouped = scoring.group_by_civ([row(10, "A"), row(0, "A"), row(5, "B")])
    assert [r["turn"] for r in grouped["A"]] == [0, 10]
    assert list(grouped) == ["A", "B"]


# ---------------------------------------------------------------- gamelog

def test_classify_log_maps_names_to_kind_and_tag():
    assert gamelog.classify_log("rotation-r0g0") == ("rotation", "r0g0")
    assert gamelog.classify_log("r1g2") == ("rotation", "r1g2")
    assert gamelog.classify_log("sym-r0g0") == ("rotation", "sym-r0g0")   # run_rotation 默认前缀
    assert gamelog.classify_log("arena-game-3") == ("batch", "arena-game-3")
    assert gamelog.classify_log("batch-7") == ("batch", "batch-7")
    assert gamelog.classify_log("something-else") == ("unknown", "something-else")


def test_parse_log_extracts_ids_seats_and_outcome(tmp_path, arena_env):
    stats = write_stats(tmp_path / "SaveFiles" / "arena-stats-4949f3e7.jsonl", [row(0, "France")])
    log = write_log(tmp_path / "logs" / "r0g0.log", seats="France", native="Rome",
                    winner="France", stats_path=stats)
    record = gamelog.parse_log(log, kind="rotation", tag="r0g0", max_turns=150)

    assert record["gameId"] == "4949f3e7"
    assert record["tag"] == "r0g0"
    assert record["winner"] == "France"
    assert record["finishedTurn"] == 150
    assert record["roles"] == {"France": "civagent", "Rome": "native"}
    assert record["statsExists"] is True
    assert record["complete"] is True


def test_parse_log_returns_none_without_game_id(tmp_path, arena_env):
    log = tmp_path / "logs" / "junk.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("nothing useful here\n", encoding="utf-8")
    assert gamelog.parse_log(log) is None


def test_parse_log_marks_unfinished_game(tmp_path, arena_env):
    """被 timeout 切断的局：有 stats 路径但没胜负 → 可评分但不算完整。"""
    stats = write_stats(tmp_path / "SaveFiles" / "arena-stats-aaaaaaaa.jsonl", [row(0, "France")])
    log = tmp_path / "logs" / "batch-0.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        "[arena] starting: map=m.json players=France, Rome, Spectator\n"
        "[arena] gameId=aaaaaaaa difficulty=Emperor civagentSeats=[France, Rome] native=[]\n"
        "[arena] turn 1 - France: g3 c1 p1\n"
        f"[arena] stats: {stats}\n",
        encoding="utf-8")
    record = gamelog.parse_log(log, kind="batch", tag="batch-0")
    assert record["outcome"] is None
    assert record["hasOutcome"] is False
    assert record["scorable"] is True
    assert record["complete"] is False


def test_key_of_prefers_game_id():
    assert gamelog.key_of({"tag": "r0g0", "gameId": "4949f3e7"}) == "4949f3e7"
    assert gamelog.key_of({"tag": "r0g0"}) == "r0g0"


def test_build_index_dedupes_log_and_disk_views_of_same_game(tmp_path, arena_env):
    """回归：同一局既是日志记录（tag 键）又是磁盘记录（gameId 键），索引曾翻倍。"""
    game_id = "4949f3e7"
    stats = write_stats(tmp_path / "SaveFiles" / f"arena-stats-{game_id}.jsonl", [row(0, "France")])
    (tmp_path / "SaveFiles" / f"arena-final-{game_id}").write_text("save", encoding="utf-8")
    write_log(tmp_path / "logs" / "r0g0.log", game_id=game_id, winner="France", stats_path=stats)

    records = gamelog.build_index(index_path=tmp_path / "index.jsonl")

    assert len(records) == 1
    record = records[0]
    assert record["gameId"] == game_id
    assert record["tag"] == "r0g0"          # 日志提供
    assert record["source"] == "log"        # 日志视图优先
    assert record["statsExists"] and record["finalExists"]


def test_coverage_reports_missing_stats_and_interrupted(tmp_path, arena_env):
    save = tmp_path / "SaveFiles"
    save.mkdir(parents=True, exist_ok=True)
    # 有存档没 stats：评分骨干缺失
    (save / "arena-final-11111111").write_text("x", encoding="utf-8")
    # 有 stats 没存档：被中断
    write_stats(save / "arena-stats-22222222.jsonl", [row(0, "A")])
    # 完整
    write_stats(save / "arena-stats-33333333.jsonl", [row(0, "A")])
    (save / "arena-final-33333333").write_text("x", encoding="utf-8")

    cov = gamelog.coverage(gamelog.build_index(index_path=tmp_path / "index.jsonl"))

    assert cov["games"] == 3
    assert cov["stats"] == 2
    assert cov["missingStats"] == ["11111111"]
    assert cov["interrupted"] == ["22222222"]


def test_read_stats_skips_blank_and_broken_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps(row(0, "A")) + "\n\nnot json\n" + json.dumps(row(1, "A")) + "\n",
                    encoding="utf-8")
    rows = gamelog.read_stats(path)
    assert [r["turn"] for r in rows] == [0, 1]


# ---------------------------------------------------------------- report

def test_game_scores_does_not_stack_conquest_on_power(tmp_path, arena_env):
    """占城已含在城/人/军里，score/powerRel 不再加征服；征服只作诊断列。"""
    rows = []
    for turn in (0, 100, 150):
        rows.append(row(turn, "A", population=1, cities=1, capturedCities=5 if turn >= 100 else 0))
        rows.append(row(turn, "B", population=1, cities=1))
    stats = write_stats(tmp_path / "arena-stats-55555555.jsonl", rows)
    record = {"stats": str(stats), "gameId": "55555555", "tag": "r9g9", "kind": "rotation"}

    scores = report_mod._game_scores(record, method="median")

    assert scores["A"]["powerRel"] == pytest.approx(1.0)
    assert scores["A"]["score"] == pytest.approx(1.0)
    assert scores["A"]["bonus"] == 0.0
    assert scores["A"]["conquestSource"] == "stats-only"
    assert scores["A"]["conquestWonders"] == 15
    assert scores["B"]["conquestWonders"] == 0
    # 势相同且都存活 → 并列第 1
    assert scores["A"]["placement"] == 1
    assert scores["B"]["placement"] == 1


def test_game_scores_ranks_dead_after_alive(tmp_path, arena_env):
    rows = []
    for turn in (100, 150):
        rows.append(row(turn, "Alive", population=2, cities=2))
        rows.append(row(turn, "Dead", population=8, cities=0))
    stats = write_stats(tmp_path / "arena-stats-dead0001.jsonl", rows)
    scores = report_mod._game_scores(
        {"stats": str(stats), "gameId": "dead0001", "kind": "rotation"}, method="median")
    assert scores["Alive"]["alive"] is True
    assert scores["Dead"]["alive"] is False
    assert scores["Alive"]["placement"] == 1
    assert scores["Dead"]["placement"] == 2


def test_game_scores_uses_late_checkpoints_not_turn_50(tmp_path, arena_env):
    """中期肥、终局瘦的国家不能靠 t50 抬名次；势只用 100/150。"""
    rows = [
        row(50, "FatEarly", population=100, cities=20),
        row(50, "Late", population=1, cities=1),
    ]
    for turn in (100, 150):
        rows.append(row(turn, "FatEarly", population=1, cities=1))
        rows.append(row(turn, "Late", population=20, cities=8))
    stats = write_stats(tmp_path / "arena-stats-late0001.jsonl", rows)
    scores = report_mod._game_scores(
        {"stats": str(stats), "gameId": "late0001", "kind": "rotation"}, method="median")
    assert set(scores["Late"]["checkpoints"]) == {"100", "150"}
    assert scores["Late"]["placement"] == 1
    assert scores["FatEarly"]["placement"] == 2


def test_report_ranks_and_reports_coverage(tmp_path, arena_env):
    rows_a, rows_b = [], []
    for turn in (100, 150):
        rows_a.append(row(turn, "Strong", population=10, cities=5, techs=10, engineScore=500))
        rows_b.append(row(turn, "Weak", population=1, cities=1, techs=3, engineScore=10))
    save = tmp_path / "SaveFiles"
    write_stats(save / "arena-stats-aaaa1111.jsonl", rows_a + rows_b)
    (save / "arena-final-aaaa1111").write_text("x", encoding="utf-8")
    # 完整口径：必须有日志给出结果，否则这局不算跑完（只看磁盘 = 无 outcome）
    write_log(tmp_path / "logs" / "r0g0.log", game_id="aaaa1111", players="Strong, Weak",
              native="Strong, Weak", winner="Strong", finished_turn=150,
              stats_path=save / "arena-stats-aaaa1111.jsonl")

    data = report_mod.report(rebuild=True, kind="all", method="median")

    assert data["coverage"]["games"] == 1          # 日志局 + 磁盘局同一 gameId → 去重成 1
    assert data["scorable"] == 1
    assert data["requireComplete"] is True
    assert data["droppedIncomplete"] == 0
    assert [r["nation"] for r in data["ranking"]] == ["Strong", "Weak"]
    assert data["ranking"][0]["meanPlacement"] < data["ranking"][1]["meanPlacement"]
    assert data["rankingAxis"] == "meanPlacement"
    assert data["priorWeight"] == 0.0


def test_report_ranks_by_mean_placement_not_blowout_score(tmp_path, arena_env):
    save = tmp_path / "SaveFiles"
    g1, g2 = [], []
    for turn in (100, 150):
        g1 += [
            row(turn, "Blowout", population=100, cities=20),
            row(turn, "Stable", population=10, cities=5),
            row(turn, "Fodder", population=1, cities=1),
        ]
        g2 += [
            row(turn, "Blowout", population=1, cities=1),
            row(turn, "Stable", population=10, cities=5),
            row(turn, "Fodder", population=2, cities=2),
        ]
    write_stats(save / "arena-stats-11111111.jsonl", g1)
    write_stats(save / "arena-stats-22222222.jsonl", g2)
    write_log(tmp_path / "logs" / "r0g0.log", game_id="11111111", players="Blowout, Stable, Fodder",
              native="Blowout, Stable, Fodder", finished_turn=150,
              stats_path=save / "arena-stats-11111111.jsonl")
    write_log(tmp_path / "logs" / "r0g1.log", game_id="22222222", players="Blowout, Stable, Fodder",
              native="Blowout, Stable, Fodder", finished_turn=150,
              stats_path=save / "arena-stats-22222222.jsonl")

    data = report_mod.report(rebuild=True, kind="all", method="median")
    by_name = {r["nation"]: r for r in data["ranking"]}

    assert by_name["Stable"]["meanPlacement"] < by_name["Blowout"]["meanPlacement"]
    assert by_name["Stable"]["rank"] < by_name["Blowout"]["rank"]


def test_report_secondary_sort_key_is_survival_then_power(tmp_path, arena_env):
    """均名次平手时必须先比存活率（再次才是势），不能直接跳到势。

    夹具刻意让 "Y" 的势**更高**：只比势的话 Y 会排到 X 前面。见 PLAN「排序键：
    meanPlacement → 存活率 → 势」。
    """
    save = tmp_path / "SaveFiles"
    g1, g2 = [], []
    for turn in (100, 150):
        g1 += [row(turn, "A", population=100, cities=10),
               row(turn, "Y", population=50, cities=5),
               row(turn, "X", population=10, cities=1)]
        # X 局内第 1、Y 已灭（cities=0）但仍留高人口 → Y 均势更高、存活率更低
        g2 += [row(turn, "X", population=100, cities=10),
               row(turn, "Y", population=90, cities=0),
               row(turn, "Z", population=1, cities=0)]
    write_stats(save / "arena-stats-5ec0a001.jsonl", g1)
    write_stats(save / "arena-stats-5ec0a002.jsonl", g2)
    write_log(tmp_path / "logs" / "r0g0.log", game_id="5ec0a001", players="A, Y, X",
              native="A, Y, X", finished_turn=150, stats_path=save / "arena-stats-5ec0a001.jsonl")
    write_log(tmp_path / "logs" / "r0g1.log", game_id="5ec0a002", players="X, Y, Z",
              native="X, Y, Z", finished_turn=150, stats_path=save / "arena-stats-5ec0a002.jsonl")

    data = report_mod.report(rebuild=True, kind="all", method="median")
    by_name = {r["nation"]: r for r in data["ranking"]}

    assert by_name["X"]["meanPlacement"] == by_name["Y"]["meanPlacement"] == 2.0
    assert by_name["X"]["survivalRate"] > by_name["Y"]["survivalRate"]
    assert by_name["X"]["powerRel"] < by_name["Y"]["powerRel"]   # 势不是排序依据
    assert by_name["X"]["rank"] < by_name["Y"]["rank"]


def test_game_scores_checkpoint_labels_stay_with_their_turns(tmp_path, arena_env):
    """某文明只在后检查点有数据时，标签不能错位到前检查点上。

    回归：曾用 zip(checkpoints, bases)，bases 跳过 None 后整体前移 →
    实测把 t150 的值标成 "100"。
    """
    rows = [row(100, "Early", population=5, cities=3),
            row(150, "Early", population=5, cities=3),
            row(150, "Late", population=50, cities=9)]
    stats = write_stats(tmp_path / "arena-stats-late0002.jsonl", rows)
    scores = report_mod._game_scores(
        {"stats": str(stats), "gameId": "late0002", "kind": "rotation"})

    assert set(scores["Late"]["checkpoints"]) == {"150"}
    assert set(scores["Early"]["checkpoints"]) == {"100", "150"}
    # 只有 1 个样本 → 均值就是该样本（旧实现会把它标成 "100"）
    assert scores["Late"]["checkpoints"]["150"] == pytest.approx(scores["Late"]["base"])


def test_report_does_not_blend_prior_by_default(tmp_path, arena_env):
    save = tmp_path / "SaveFiles"
    rows = []
    for turn in (100, 150):
        rows += [row(turn, "A", population=5, cities=3), row(turn, "B", population=1, cities=1)]
    write_stats(save / "arena-stats-pri00001.jsonl", rows)
    write_log(tmp_path / "logs" / "r0g0.log", game_id="pri00001", players="A, B",
              native="A, B", finished_turn=150, stats_path=save / "arena-stats-pri00001.jsonl")
    prior = {"Ghost": 99.0}

    data = report_mod.report(rebuild=True, prior=prior, prior_weight=0.0)
    names = {r["nation"] for r in data["ranking"]}
    assert data["priorWeight"] == 0.0
    assert "Ghost" not in names

    blended = report_mod.report(rebuild=True, prior=prior, prior_weight=1.0)
    assert blended["priorWeight"] == 1.0
    assert "Ghost" in {r["nation"] for r in blended["ranking"]}


def test_report_uses_fitted_weights_when_asked(tmp_path, arena_env):
    rows = [row(0, "A", population=5, militaryMight=0), row(0, "B", population=0, militaryMight=5)]
    write_stats(tmp_path / "SaveFiles" / "arena-stats-bbbb2222.jsonl", rows)
    record = {"stats": str(tmp_path / "SaveFiles" / "arena-stats-bbbb2222.jsonl"),
              "gameId": "bbbb2222", "kind": "rotation"}

    # 双方 power：A 人口高（5）、B 军力高（5），median = 2.5；两者都没占领/丢城 → 征服项为 0
    by_population = report_mod._game_scores(record, weights={"population": 1.0})["A"]["score"]
    by_military = report_mod._game_scores(record, weights={"militaryMight": 1.0})["A"]["score"]

    assert by_population > by_military      # 权重决定谁领先
    assert by_military == pytest.approx(0.0, abs=1e-3)          # A 的军力为 0 → 国力为 0
    assert by_population == pytest.approx(5 / 2.5, abs=1e-3)


# ---------------------------------------------------------------- fit_weights

def test_spearman_detects_perfect_and_reversed_order():
    fit = importlib.import_module("arena.fit_weights")
    assert fit.spearman([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    assert fit.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)


def test_fit_logistic_learns_the_driving_factor():
    numpy = pytest.importorskip("numpy")
    fit = importlib.import_module("arena.fit_weights")
    rng = numpy.random.default_rng(0)
    signal = rng.normal(size=200)
    noise = rng.normal(size=(200, 2))
    features = numpy.column_stack([signal, noise])
    labels = (signal > 0).astype(int)

    weights = fit.fit_logistic(features.tolist(), labels.tolist(), l2=0.1, steps=2000)

    assert weights[0] > 0.2                                  # 信号因子方向正确
    assert abs(weights[1]) < abs(weights[0])                 # 噪声因子更小
    assert abs(weights[2]) < abs(weights[0])


def test_build_dataset_labels_the_city_leader_not_engine_score(tmp_path, arena_env):
    save = tmp_path / "SaveFiles"
    write_stats(save / "arena-stats-cccc3333.jsonl",
                [row(150, "Winner", cities=8, population=20, engineScore=10),
                 row(150, "Loser", cities=1, population=1, engineScore=900)])
    gamelog.save_index([gamelog._decorate({
        "gameId": "cccc3333", "kind": "rotation", "tag": "r0g0",
        "stats": str(save / "arena-stats-cccc3333.jsonl"), "log": None,
        "players": ["Winner", "Loser"], "roles": {}, "map": None, "difficulty": None,
        "maxTurns": None, "mods": None, "seed": None, "final": None,
        "finishedTurn": 150, "turnCount": None, "winner": None,
        "outcome": "no victory (turn limit)",
        "source": "log",
    })], index_path=tmp_path / "index.jsonl")

    fit = importlib.import_module("arena.fit_weights")
    X, y, groups = fit.build_dataset()

    assert len(X) == 2 and len(set(groups)) == 1
    assert sum(y) == 1
    assert len(X[0]) == len(scoring.FIT_FEATURES)      # 因子列 + 先验列
    assert len(scoring.FIT_FEATURES) == len(scoring.FIT_FACTORS) + 1
    assert "engineScore" not in scoring.FIT_FACTORS
    # Winner 在前（stats 顺序），y[0] 应是城多的那个
    assert y[0] == 1 and y[1] == 0


def test_build_dataset_prior_column_is_scaled_by_weight(tmp_path, arena_env):
    """旧榜分数 × prior_weight 进设计矩阵最后一列；不 z-score（0.1 就该是 0.1）。"""
    save = tmp_path / "SaveFiles"
    write_stats(save / "arena-stats-0d0d0001.jsonl",
                [row(150, "Strong", cities=8, population=20), row(150, "Weak", cities=1, population=1)])
    gamelog.save_index([gamelog._decorate({
        "gameId": "0d0d0001", "kind": "rotation", "tag": "r0g0",
        "stats": str(save / "arena-stats-0d0d0001.jsonl"), "log": None,
        "players": ["Strong", "Weak"], "roles": {}, "map": None, "difficulty": None,
        "maxTurns": None, "mods": None, "seed": None, "final": None,
        "finishedTurn": 150, "turnCount": None, "winner": None,
        "outcome": "no victory (turn limit)", "source": "log",
    })], index_path=tmp_path / "index.jsonl")

    fit = importlib.import_module("arena.fit_weights")
    # Strong 旧榜 3.0 / Weak 1.0 → 乘 0.1
    X, y, groups = fit.build_dataset(prior={"Strong": 3.0, "Weak": 1.0}, prior_weight=0.1)
    prior_col = sorted(x[-1] for x in X)
    assert prior_col == pytest.approx([0.1, 0.3])      # stats 顺序：Strong(3.0) 在 Weak(1.0) 前
    assert len(X[0]) == len(scoring.FIT_FEATURES)

    # 未上榜的文明得 0；prior_weight=0 时整列归零（等于退回旧行为）
    X0, _, _ = fit.build_dataset(prior={"Strong": 3.0}, prior_weight=0.0)
    assert {x[-1] for x in X0} == {0.0}


def test_load_prior_reads_report_and_nation_strength_shapes(tmp_path):
    """旧榜两种产物形状都要能读：report 的 ranking / nation_strength 的 nations。"""
    rep = tmp_path / "ranking.json"
    rep.write_text(json.dumps({"ranking": [{"nation": "A", "score": 2.5}]}), encoding="utf-8")
    assert report_mod.load_prior(rep) == {"A": 2.5}

    ns = tmp_path / "nation-strength-current.json"
    ns.write_text(json.dumps({"nations": [{"nation": "B", "combined": 1.75}]}), encoding="utf-8")
    assert report_mod.load_prior(ns) == {"B": 1.75}

    legacy = tmp_path / "conclusions.json"
    legacy.write_text(json.dumps([{"nation": "C", "finalScore": 9.0}]), encoding="utf-8")
    assert report_mod.load_prior(legacy) == {"C": 9.0}

    assert report_mod.load_prior(tmp_path / "missing.json") == {}


# ---------------------------------------------------------------- nation_strength

def test_combined_formula_legacy_adds_duel_when_asked():
    """旧口径：combined = mean(混战, mk) + 0.1*duel。仅 --include-duel 走这条。"""
    rows = nation_strength.combine({"A": {"score": 2.0}}, {"A": {"score": 4.0}}, {"A": 5.0},
                                   cap=99.0, include_duel=True)
    assert rows[0]["base"] == pytest.approx(3.0)
    assert rows[0]["combined"] == pytest.approx(3.5)
    assert rows[0]["duelInflation"] == pytest.approx(0.5)


def test_combined_default_ignores_duel():
    rows = nation_strength.combine({"A": {"score": 2.0, "meanPlacement": 3.0}}, {}, {"A": 25.86})
    assert rows[0]["duelInflation"] == 0.0
    assert rows[0]["combined"] == pytest.approx(2.0)
    assert rows[0]["meanPlacement"] == 3.0


def test_combined_sorts_by_mean_placement_before_combined():
    """主榜主轴是均名次：combined 高的国家不能靠分压过均名次更稳的国家。"""
    melee = {
        "Steady": {"score": 1.0, "meanPlacement": 1.0},
        "Blowout": {"score": 5.0, "meanPlacement": 4.0},
    }
    rows = nation_strength.combine(melee, {}, {})
    assert [r["nation"] for r in rows] == ["Steady", "Blowout"]


def test_render_markdown_keeps_combine_order_and_flags_no_duel():
    """榜单文档：行序听 combine 的（均名次），不重排；表头必须写清 includeDuel。

    回归：ranking-total.md 曾是手写产物，长期停留在 ``mean(混战,mk)+0.1*duel`` 旧公式上。
    """
    data = {
        "as_of": "2026-01-01", "formula": "主榜=均名次", "includeDuel": False,
        "normalization": "median", "mkMode": "checkpoints", "weightSet": "hand",
        "sources": {"混战": "arena/index.jsonl"},
        "nations": [
            {"nation": "Steady", "meanPlacement": 1.0, "混战": 2.0, "mk": None,
             "survivalRate": 1.0, "captured": 3, "eliminated": 1, "duel150": None,
             "games": {"混战": 5, "mk": 0}},
            {"nation": "Blowout", "meanPlacement": 4.0, "混战": 9.0, "mk": None,
             "survivalRate": 0.5, "captured": 0, "eliminated": 0, "duel150": 3.0,
             "games": {"混战": 2, "mk": 0}},
        ],
    }
    md = nation_strength.render_markdown(data)

    assert md.index("| 1 | Steady") < md.index("| 2 | Blowout")
    assert "includeDuel=False" in md
    assert "均名次" in md
    assert "| 4.00 |" in md and "| 1.00 |" in md          # 均名次列真的出来了
    assert "| — |" in md                                   # 缺失值不写成 None


def test_combined_caps_duel_outlier():
    """duel150 是重尾比值：25.86 不该直接把榜首抬 2.59 分。"""
    melee = {"MongolEmpire": {"score": 3.7888}}
    duel = {"MongolEmpire": 25.86}
    capped = nation_strength.combine(melee, {}, duel, cap=3.0, include_duel=True)[0]
    uncapped = nation_strength.combine(melee, {}, duel, cap=1e9, include_duel=True)[0]
    assert capped["duel150"] == 3.0
    assert capped["duel150Raw"] == 25.86            # 原始值仍保留供审计
    assert capped["combined"] == pytest.approx(3.7888 + 0.3)
    assert uncapped["combined"] == pytest.approx(3.7888 + 2.586)
    assert capped["combined"] < uncapped["combined"]


def test_combined_zero_as_missing_halves_mk_only_nations():
    """旧口径把"未参加混战"写成 0，于是 mean(0, mk) 把 mk 除 2。"""
    melee = {"Sheng Tang": {"score": 2.0}}
    mk = {"Spain": {"score": 2.56}}
    # combine() 返回值是按 combined 降序排的，必须按国名取，不能取 [0]
    strict = {r["nation"]: r for r in nation_strength.combine(melee, mk, {}, zero_as_missing=False)}
    legacy = {r["nation"]: r for r in nation_strength.combine(melee, mk, {}, zero_as_missing=True)}
    assert strict["Spain"]["混战"] is None
    assert strict["Spain"]["base"] == pytest.approx(2.56)     # 按缺失处理
    assert legacy["Spain"]["base"] == pytest.approx(1.28)     # 旧口径除 2
    assert legacy["Spain"]["baseLegacyZero"] == pytest.approx(1.28)
    assert strict["Sheng Tang"]["base"] == pytest.approx(2.0)


def test_load_duel_reads_frozen_values_and_skips_nulls(tmp_path):
    p = tmp_path / "duel.json"
    p.write_text(json.dumps({"duel150": {"A": 1.5, "B": None, "C": 25.86}}), encoding="utf-8")
    duel = nation_strength.load_duel(p)
    assert duel == {"A": 1.5, "C": 25.86}
    assert nation_strength.load_duel(tmp_path / "missing.json") == {}


def test_mk_scores_matches_game_by_civ_set(tmp_path, arena_env):
    """roster 里没有 gameId，所以靠"文明集合相等"去 SaveFiles 找对局。"""
    save = tmp_path / "SaveFiles"
    rows = []
    for turn in (0, 150):
        rows += [row(turn, "Spain", population=8, cities=3),
                 row(turn, "Korea", population=2, cities=1)]
    write_stats(save / "arena-stats-deadbeef.jsonl", rows)
    rosters = tmp_path / "rosters"
    rosters.mkdir()
    (rosters / "roster-mk9.json").write_text(
        json.dumps([{"nation": "Spain", "x": 0, "y": 0}, {"nation": "Korea", "x": 1, "y": 0}]),
        encoding="utf-8")

    scores = nation_strength.mk_scores(roster_glob="roster-mk*.json", mode="final", roster_dir=rosters)
    assert set(scores) == {"Spain", "Korea"}
    assert scores["Spain"]["games"] == 1
    assert scores["Spain"]["score"] > scores["Korea"]["score"]   # 人口/城多的更强
    assert scores["Spain"]["detail"][0]["gameId"] == "deadbeef"


def test_melee_scores_averages_only_scorable_rotation_games(tmp_path, arena_env):
    save = tmp_path / "SaveFiles"
    stats = write_stats(save / "arena-stats-aaaa0001.jsonl",
                        [row(0, "A", population=10), row(0, "B", population=1)])
    records = [
        {"gameId": "aaaa0001", "tag": "r0g0", "kind": "rotation", "stats": str(stats),
         "outcome": "no victory (turn limit)"},
        {"gameId": "aaaa0002", "tag": "r0g1", "kind": "rotation", "stats": str(save / "gone.jsonl"),
         "outcome": "no victory (turn limit)"},
        {"gameId": "aaaa0003", "tag": "b0", "kind": "batch", "stats": str(stats),
         "outcome": "VICTORY: A"},
    ]
    melee = nation_strength.melee_scores(records=records)
    assert set(melee) == {"A", "B"}          # batch 局与缺 stats 的局都不算
    assert melee["A"]["games"] == 1
    assert melee["A"]["score"] > melee["B"]["score"]
    assert melee["A"]["meanPlacement"] < melee["B"]["meanPlacement"]
    assert melee["A"]["survivalRate"] == 1.0


def test_nation_strength_kinds_can_include_save_games(tmp_path, arena_env):
    """存档局（kind=save）默认不进混战，加了 kinds 才进。"""
    stats = write_stats(tmp_path / "SaveFiles" / "arena-stats-save0001.jsonl",
                        [row(0, "A", population=10), row(0, "B", population=1)])
    records = [{"gameId": "save0001", "tag": "save-save0001", "kind": "save", "stats": str(stats),
                "outcome": "no victory (turn limit)"}]
    assert nation_strength.melee_scores(records=records) == {}
    assert set(nation_strength.melee_scores(records=records, kinds=("rotation", "save"))) == {"A", "B"}


def test_gamelog_is_complete_needs_an_outcome_not_just_stats(tmp_path, arena_env):
    """完成口径（宽松）：有结果才算跑完；只有 stats 不算。"""
    stats = write_stats(tmp_path / "SaveFiles" / "arena-stats-d0ed0001.jsonl", [row(0, "A")])
    base = {"gameId": "d0ed0001", "kind": "rotation", "stats": str(stats)}

    assert gamelog.is_complete(gamelog._decorate({**base})) is False                 # 无 outcome（被砍）
    assert gamelog.is_complete(gamelog._decorate(
        {**base, "outcome": "no victory (turn limit)"})) is True                    # 跑满上限
    assert gamelog.is_complete(gamelog._decorate(
        {**base, "outcome": "VICTORY: A", "finishedTurn": 97})) is True            # 提前胜利也算跑完


def test_report_maps_filter_keeps_one_map_set(tmp_path, arena_env):
    """PLAN「不同地图不得混表」：--maps 只留指定地图的局。"""
    save = tmp_path / "SaveFiles"
    rows = [row(t, "A", population=5, cities=3) for t in (100, 150)] + \
           [row(t, "B", population=1, cities=1) for t in (100, 150)]
    for gid, map_name, tag in (("0a0a0001", "测试arena.json", "sym-r0g0"),
                               ("0a0a0002", "arena-64x64-rotation.json", "sym-r0g1")):
        write_stats(save / f"arena-stats-{gid}.jsonl", rows)
        write_log(tmp_path / "logs" / f"{tag}.log", game_id=gid, players="A, B", native="A, B",
                  finished_turn=150, stats_path=save / f"arena-stats-{gid}.jsonl",
                  map_name=map_name)

    data = report_mod.report(rebuild=True, kind="rotation", maps=["测试arena.json"])
    assert data["maps"] == ["测试arena.json"]
    assert data["scorable"] == 1
    assert [g["gameId"] for g in data["games"]] == ["0a0a0001"]

    both = report_mod.report(rebuild=True, kind="rotation")
    assert both["scorable"] == 2
    assert both["maps"] is None


def test_report_mods_filter_separates_conditions(tmp_path, arena_env):
    """同一张图上 cathay-only 与 cathay+Song 是**两个对照条件**，必须能分集。

    语义与 nation_strength 的 --mods-filter 一致：`all(m in record.mods)`，
    即「同时启用这些模组」；所以单查 cathay 会同时命中两批（超集）。
    """
    stats = write_stats(tmp_path / "SaveFiles" / "arena-stats-0b0b0001.jsonl",
                        [row(150, "A", population=5, cities=3), row(150, "B", population=1, cities=1)])
    base = {"kind": "rotation", "map": "测试arena.json", "stats": str(stats),
            "scorable": True, "hasOutcome": True, "complete": True}
    records = [
        {**base, "gameId": "0b0b0001", "mods": ["unciv unrepentant cathay"]},
        {**base, "gameId": "0b0b0002", "mods": ["unciv unrepentant cathay", "Song Dynasty"]},
    ]
    cathay_only = report_mod.report(records=records, kind="rotation",
                                    mods_filter=["unciv unrepentant cathay"])
    assert cathay_only["scorable"] == 2                      # 超集：cathay 是两批的公约数
    assert cathay_only["modsFilter"] == ["unciv unrepentant cathay"]

    both = report_mod.report(records=records, kind="rotation",
                             maps=["测试arena.json"],
                             mods_filter=["unciv unrepentant cathay", "Song Dynasty"])
    assert both["scorable"] == 1                             # 只留 Song 那一批
    assert [g["gameId"] for g in both["games"]] == ["0b0b0002"]


def test_report_excludes_unfinished_games_unless_allowed(tmp_path, arena_env):
    """未跑完的局默认不进榜；`require_complete=False` 才回旧行为（用户 2026-09-15 定）。"""
    save = tmp_path / "SaveFiles"
    rows = [row(t, "A", population=5, cities=3) for t in (100, 150)] + \
           [row(t, "B", population=1, cities=1) for t in (100, 150)]
    write_stats(save / "arena-stats-600d0001.jsonl", rows)
    write_log(tmp_path / "logs" / "r0g0.log", game_id="600d0001", players="A, B",
              native="A, B", finished_turn=150, stats_path=save / "arena-stats-600d0001.jsonl")
    # 半局：有 stats、日志里没有结果（被预算切断）
    write_stats(save / "arena-stats-c0700001.jsonl", [row(100, "A", population=9, cities=9)])
    write_log(tmp_path / "logs" / "r0g1.log", game_id="c0700001", players="A, B",
              native="A, B", finished_turn=100,
              stats_path=save / "arena-stats-c0700001.jsonl", finished=False)

    strict = report_mod.report(rebuild=True, kind="rotation", method="median")
    assert strict["scorable"] == 1
    assert strict["droppedIncomplete"] == 1
    assert strict["requireComplete"] is True

    loose = report_mod.report(rebuild=True, kind="rotation", method="median",
                              require_complete=False)
    assert loose["scorable"] == 2
    assert loose["droppedIncomplete"] == 0


# ---------------------------------------------------------------- map_fix

def test_map_fix_injects_starts_and_keeps_tiles(tmp_path):
    """只注入起点，地形/资源一字不改（用户手摆的资源必须保住）。"""
    fit = importlib.import_module("arena.map_fix")
    base = tmp_path / "base.json"
    base.write_text(json.dumps({
        "mapParameters": {"name": "t", "mods": ["old"]},
        "tileList": [{"position": {"x": 0, "y": 0}, "baseTerrain": "Plains", "resource": "Tea"},
                     {"position": {"x": 1, "y": 0}, "baseTerrain": "Grassland"}],
    }), encoding="utf-8")
    out = tmp_path / "out.json"
    doc = fit.inject(base, [{"nation": "A", "x": 0, "y": 0}, {"nation": "B", "x": 1, "y": 0}],
                     out, mods=["unciv unrepentant cathay"])

    assert doc["startingLocations"] == [
        {"position": {"x": 0, "y": 0}, "nation": "A", "usage": "Normal"},
        {"position": {"x": 1, "y": 0}, "nation": "B", "usage": "Normal"},
    ]
    assert doc["mapParameters"]["mods"] == ["unciv unrepentant cathay"]
    assert doc["tileList"][0]["resource"] == "Tea"          # 没被重造掉
    assert json.loads(out.read_text(encoding="utf-8"))["tileList"] == doc["tileList"]


def test_map_fix_rejects_start_off_map(tmp_path):
    fit = importlib.import_module("arena.map_fix")
    base = tmp_path / "b.json"
    base.write_text(json.dumps({"tileList": [{"position": {"x": 0, "y": 0}}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        fit.inject(base, [{"nation": "A", "x": 9, "y": 9}], tmp_path / "o.json")


# ---------------------------------------------------------------- savedata

def _fake_save():
    """两主要文明 + 一城邦：主要文明的字段指纹与实测存档一致（有 statsHistory/espionageManager）。"""
    return {
        "turns": 120,
        "gameId": "abcdef01-2345-6789-abcd-ef0123456789",
        "gameParameters": {"mods": ["unciv unrepentant cathay"], "difficulty": "Emperor"},
        "victoryData": {"winningCiv": "Rome", "victoryType": "Scientific", "victoryTurn": 120},
        "civilizations": [
            {"civName": "Rome", "cities": [
                {"population": {"population": 20}, "foundingCiv": "Rome",
                 "cityConstructions": {"builtBuildings": ["Pyramid", "Monument"]}},
                {"population": {"population": 5}, "foundingCiv": "Siam"}],
             "gold": 100, "policies": {"storedCulture": 50, "adoptedPolicies": ["a", "b"]},
             "tech": {"techsResearched": ["t1", "t2", "t3"]},
             "religionManager": {"storedFaith": 9}, "espionageManager": {},
             "statsHistory": {"119": "S123N1C2P25G100T3F9H5W1A0"}},
            {"civName": "Siam", "cities": [{"population": {"population": 3}, "foundingCiv": "Siam",
                                                "cityConstructions": {"builtBuildings": []}}],
             "gold": 5, "policies": {"storedCulture": 0, "adoptedPolicies": []},
             "tech": {"techsResearched": ["t1"]}, "espionageManager": {},
             "statsHistory": {"119": "S10N1C1P3G5T1F0H1W0A0"}},
            {"civName": "La Venta", "cityStatePersonality": "Hostile",
             "cities": [{"population": {"population": 2}, "foundingCiv": "La Venta",
                          "cityConstructions": {"builtBuildings": []}}]},
        ],
        "tileMap": {"mapParameters": {"name": "m"}, "tileList": [
            {"position": {"x": 0, "y": 0}, "militaryUnit": {"owner": "Rome", "name": "Catapult"}},
            {"position": {"x": 1, "y": 0}, "militaryUnit": {"owner": "Siam", "name": "Unknown Unit"}},
        ]},
    }


def test_savedata_major_predicate_uses_structure_not_names():
    sd = importlib.import_module("arena.savedata")
    assert sd.is_major("Siam", {"hasStatsHistory": True}) is True
    assert sd.is_major("La Venta", {"cityStatePersonality": "Hostile"}) is False
    assert sd.is_major("Venice", {"hasStatsHistory": False}) is False     # 名字不可靠
    assert sd.is_major("Arabia", {"hasStatsHistory": True, "defeated": True}) is True  # 被灭国仍算主要文明
    assert sd.is_major("Spectator", {"hasStatsHistory": True}) is False
    assert sd.is_major("Barbarians", {"hasStatsHistory": True}) is False


def test_savedata_standings_reads_units_from_tiles(monkeypatch):
    sd = importlib.import_module("arena.savedata")
    monkeypatch.setattr(sd, "unit_strengths", lambda: {"Catapult": {"strength": 4}})
    st = sd.standings(_fake_save(), wonders={"Pyramid"})

    rome = st["Rome"]
    assert rome["cities"] == 2 and rome["population"] == 25
    assert rome["militaryUnits"] == 1 and rome["militaryMight"] == 4   # 单位挂在格子上，按 owner 归属
    assert rome["wonders"] == 1                                       # 只数 Pyramid
    assert rome["capturedCities"] == 1                                # foundingCiv != 自己
    assert rome["engineScore"] == 123                                 # statsHistory 的 S 项
    assert st["Siam"]["militaryMight"] == 0                          # 单位名不在强度表里 → 0


def test_savedata_rows_exclude_minors_and_rank_majors(monkeypatch):
    sd = importlib.import_module("arena.savedata")
    monkeypatch.setattr(sd, "unit_strengths", lambda: {"Catapult": {"strength": 4}})
    doc = _fake_save()
    st = sd.standings(doc, wonders={"Pyramid"})
    rows = sd.to_stats_rows(doc, st)

    assert {r["civID"] for r in rows} == {"Rome", "Siam"}      # 城邦被剔除
    assert all(r["turn"] == 120 for r in rows)

    ranking = sd.rank([{"statsRows": rows, "standings": st}])
    assert [r[0] for r in ranking] == ["Rome", "Siam"]          # 罗马显著更强
    assert ranking[0][1] > ranking[1][1]


# ---------------------------------------------------------------- map symmetry

def test_mirror_key_flips_screen_x():
    from arena.map_generator import ArenaMap, mirror_of
    am = ArenaMap(64, 64, seed=0)
    assert mirror_of(-14, -8) == (22, -8)
    assert am.mirror_key((-14, -8)) == (22, -8)
    assert am.screen_x((-14, -8)) == pytest.approx(-18.0)
    assert am.screen_x((22, -8)) == pytest.approx(18.0)
    assert am.screen_x(am.mirror_key((-20, 0))) == pytest.approx(-am.screen_x((-20, 0)))


def test_render_is_vertical_midline_symmetric():
    from arena.map_generator import ArenaMap, DEFAULT_ROSTER_2P
    doc = ArenaMap(64, 64, seed=7).render(DEFAULT_ROSTER_2P, wonder_pairs=3)
    assert doc["mapParameters"]["worldWrap"] is False
    assert len(doc["startingLocations"]) == 2
    starts = {(s["nation"], s["position"]["x"], s["position"]["y"])
              for s in doc["startingLocations"]}
    assert ("Sheng Tang", -14, -8) in starts or any(s["nation"] == "Sheng Tang" for s in doc["startingLocations"])
    # right seat is exact mirror of left
    by_nation = {s["nation"]: (s["position"]["x"], s["position"]["y"]) for s in doc["startingLocations"]}
    left = by_nation["Sheng Tang"]
    right = by_nation["MongolEmpire"]
    assert right == (-left[0] - left[1], left[1])


def test_six_slot_roster_keeps_three_mirror_pairs():
    from arena.map_generator import ArenaMap
    slots = json.loads((ARENA / "slots-64x64.json").read_text(encoding="utf-8"))
    nations = ["Sheng Tang", "MongolEmpire", "Qindynasty", "Late Tang", "Babylon", "Ethiopia"]
    roster = [(n, (s["x"], s["y"])) for n, s in zip(nations, slots)]
    doc = ArenaMap(64, 64, seed=99).render(roster, wonder_pairs=2)
    assert len(doc["startingLocations"]) == 6
    am = ArenaMap(64, 64, seed=0)
    am.tiles = {(t["position"]["x"], t["position"]["y"]): t for t in doc["tileList"]}
    assert am.verify_symmetry() == []
    # every start's mirror is also a start
    positions = {(s["position"]["x"], s["position"]["y"]) for s in doc["startingLocations"]}
    for pos in positions:
        assert am.mirror_key(pos) in positions

