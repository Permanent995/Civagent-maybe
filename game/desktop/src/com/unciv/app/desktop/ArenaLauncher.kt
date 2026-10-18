package com.unciv.app.desktop

import com.badlogic.gdx.files.FileHandle
import com.unciv.Constants
import com.unciv.UncivGame
import com.unciv.logic.GameStarter
import com.unciv.logic.civilization.PlayerType
import com.unciv.logic.files.MapSaver
import com.unciv.models.metadata.GameParameters
import com.unciv.models.metadata.GameSettings
import com.unciv.models.metadata.GameSetupInfo
import com.unciv.models.metadata.Player
import com.unciv.models.ruleset.RulesetCache
import com.unciv.models.ruleset.Speed
import com.unciv.models.skins.SkinCache
import com.unciv.models.tilesets.TileSetCache
import com.unciv.utils.Log

/**
 * 训练场驱动器（无头）：从生成的对称地图开局，CivAgent 席位经 TurnHook 向大脑请求决策，
 * 纯 native 席位走原生 AI，循环 nextTurn 直到胜利/回合上限。不依赖任何 UI。
 *
 * 用法（工作目录必须是 android/assets，同 ConsoleLauncher）：
 *   java -cp <desktop jar> com.unciv.app.desktop.ArenaLauncherKt \
 *     --map arena-51x50.json \
 *     --players "Sheng Tang:civagent,MongolEmpire:civagent" \
 *     --mods "unciv unrepentant cathay" \
 *     --max-turns 250
 *     --stats-interval 24   # 采样/日志间隔；AI 仍每回合原生 automate
 *
 * 产物（全部在工作目录下，路径固定可查）：
 *   SaveFiles/arena-stats-{gameId8}.jsonl  每回合每文明的国力采样（评分骨干）
 *   SaveFiles/arena-final-{gameId8}        终局存档（Base64(gzip) 标准格式）
 *   SaveFiles/Autosave*                    每 10 回合的周期存档
 *
 * 前置：本地大脑已起（agent_api :2335）——TurnHook 调不到大脑时席位静默回退原生，
 * 训练时若看到全部 fallback 即大脑未起。
 */
object ArenaLauncher {

    @JvmStatic
    fun main(args: Array<String>) {
        Log.backend = DesktopLogBackend()
        val options = parseArgs(args)

        val game = UncivGame(true)
        UncivGame.Current = game
        UncivGame.Current.settings = GameSettings().apply {
            showTutorials = false
            // 周期存档间隔：与采样解耦；训练场默认稀一点，少磁盘抖动
            turnsBetweenAutosaves = options.statsInterval.coerceAtLeast(1)
        }
        RulesetCache.loadRulesets(true)
        TileSetCache.loadTileSetConfigs(true)
        SkinCache.loadSkinConfigs(true)

        val mapFile = FileHandle("maps/${options.mapName}")
        require(mapFile.exists()) { "map not found: ${mapFile.path()} - run arena/map_generator.py first" }
        val tileMap = MapSaver.loadMap(mapFile)

        val gameParameters = GameParameters().apply {
            difficulty = options.difficulty
            speed = Speed.DEFAULT
            noBarbarians = true
            numberOfCityStates = 0
            minNumberOfPlayers = 2
            players.clear() // GameParameters defaults to 4 pre-filled players
            for (mod in options.mods) mods.add(mod)
            // GameStarter combines base+mods itself; players reference nations by name
            for ((nationName, role) in options.players) {
                players.add(Player(nationName, if (role == "human") PlayerType.Human else PlayerType.AI))
            }
            // All-AI arenas need a human spectator or GameInfo.setTransients has no currentPlayer
            players.add(Player(Constants.spectator, PlayerType.Human))
            // T0 全 native：关掉 Fill-CivAgent，禁止 ensureStarted 拉起 Python 大脑
            val hasCivAgent = options.players.any { it.second == "civagent" }
            civAgentFillSeats = hasCivAgent
        }
        require(gameParameters.players.size > 1) { "--players required" }

        val setup = GameSetupInfo(gameParameters, tileMap.mapParameters)
        setup.mapFile = mapFile
        println("[arena] starting: map=${options.mapName} players=${gameParameters.players.joinToString { it.chosenCiv }}")
        val gameInfo = GameStarter.startNewGame(setup)
        // 仅 civagent 席注册网关/大脑；T0 全 native 时 gateway=null，不 spawn agent_api
        val civagentSeats = options.players.filter { it.second == "civagent" }.map { it.first }
        val gateway = if (civagentSeats.isNotEmpty()) {
            com.unciv.logic.civagent.AgentMod.ensureStarted(gameInfo)?.also { it.setAgentSeats(civagentSeats) }
        } else {
            null
        }
        if (options.brainAlways) {
            com.unciv.logic.civagent.AgentConfig.forceBrainWithoutHuman = true
            println("[arena] brain force-enabled (激活门旁路，T1/T2 方法论测试)")
        } else {
            println("[arena] brain gated: activates only after meeting the human (纯 AI 局全程静默)")
        }
        println("[arena] gameId=${gameInfo.gameId.take(8)} difficulty=${options.difficulty} " +
            "civagentSeats=$civagentSeats native=${options.players.filter { it.second == "native" }.map { it.first }} " +
            "statsInterval=${options.statsInterval} brain=${if (gateway != null) "on" else "off"}")

        // Per-turn nation-power sampling: the backbone of the reward-feedback evaluation
        // (economy slope / conquest progress / personality KPIs are all computed from this).
        val statsFile = FileHandle("SaveFiles").child("arena-stats-${gameInfo.gameId.take(8)}.jsonl")
        val scoreCheckpoints = setOf(50, 100, 150) // must align with arena/scoring.py CHECKPOINTS
        fun shouldSample(turn: Int): Boolean {
            if (options.statsInterval <= 1) return true
            if (turn == 0 || turn >= options.maxTurns) return true
            if (turn in scoreCheckpoints) return true
            return turn % options.statsInterval == 0
        }
        fun dumpStats() {
            for (civ in gameInfo.civilizations) {
                if (!civ.isMajorCiv() || civ.isSpectator()) continue
                val units = civ.units.getCivUnits().toList()
                val military = units.filter { it.baseUnit.strength > 0 || it.baseUnit.rangedStrength > 0 }
                val might = military.sumOf { maxOf(it.baseUnit.strength, it.baseUnit.rangedStrength) }
                // conquest progress: cities this civ owns that it did NOT found
                val captured = civ.cities.count { it.foundingCivObject?.civID != civ.civID }
                val wonders = civ.cities.sumOf { city ->
                    city.cityConstructions.getBuiltBuildings().count { it.isWonder } }
                val faith = try { civ.religionManager.storedFaith } catch (_: Exception) { 0 }
                val entry = "{" + listOf(
                    "\"turn\":" + gameInfo.turns,
                    "\"civID\":\"" + civ.civID + "\"",
                    "\"gold\":" + civ.gold,
                    "\"goldPerTurn\":" + civ.stats.statsForNextTurn.gold.toInt(),
                    "\"sciencePerTurn\":" + civ.stats.statsForNextTurn.science.toInt(),
                    "\"culturePerTurn\":" + civ.stats.statsForNextTurn.culture.toInt(),
                    "\"cultureStored\":" + civ.policies.storedCulture,
                    "\"faith\":" + faith,
                    "\"techs\":" + civ.tech.techsResearched.size,
                    "\"cities\":" + civ.cities.size,
                    "\"population\":" + civ.cities.sumOf { it.population.population },
                    "\"wonders\":" + wonders,
                    "\"engineScore\":" + civ.calculateTotalScore().toInt(),
                    "\"militaryUnits\":" + military.size,
                    "\"militaryMight\":" + might,
                    "\"capturedCities\":" + captured,
                    // v1 归因简化：凡是“其他主要文明已灭亡”都计入在场者（混战局不追责最后一击）
                    "\"eliminatedNations\":" + gameInfo.civilizations
                        .count { it.isMajorCiv() && it != civ && it.isDefeated() }
                ).joinToString(",") + "}"
                statsFile.writeString(entry + System.lineSeparator(), true, Charsets.UTF_8.name())
            }
        }
        dumpStats()

        var lastPrint = -1
        fun someoneWon() = gameInfo.civilizations.any { it.victoryManager.hasWon() }
        while (!someoneWon() && gameInfo.turns < options.maxTurns) {
            gameInfo.nextTurn() // 每回合仍走引擎原生 NextTurnAutomation；采样可降频
            if (shouldSample(gameInfo.turns)) dumpStats()
            val printEvery = options.statsInterval.coerceAtLeast(1)
            if (gameInfo.turns - lastPrint >= printEvery || gameInfo.turns >= options.maxTurns) {
                lastPrint = gameInfo.turns
                val summary = gameInfo.civilizations
                    .filter { it.isMajorCiv() && !it.isSpectator() }
                    .joinToString(" | ") { civ ->
                        "${civ.civID}: g${civ.gold} c${civ.cities.size} p${civ.cities.sumOf { it.population.population }}"
                    }
                println("[arena] turn ${gameInfo.turns} - $summary")
            }
        }
        // 终局再采一次（可能与末次采样重复同行，评分取 latest 无妨）
        dumpStats()

        gateway?.stop() // release the non-daemon HTTP threads so the JVM can exit

        // Final save at a fixed documented path. Headless has no Gdx Files service, so we
        // write the exact same format UncivFiles.saveGame would (Base64(gzip(GameInfo JSON))).
        try {
            val saveDir = FileHandle("SaveFiles")
            saveDir.mkdirs()
            val saveFile = saveDir.child("arena-final-${gameInfo.gameId.take(8)}")
            saveFile.writeString(
                com.unciv.ui.screens.savescreens.Gzip.zip(com.unciv.json.json().toJson(gameInfo)),
                false, Charsets.UTF_8.name()
            )
            println("[arena] final save: ${saveFile.file().absolutePath}")
        } catch (exception: Exception) {
            println("[arena] final save failed: ${exception.message}")
        }

        val winner = gameInfo.civilizations.firstOrNull { it.victoryManager.hasWon() }
        println("[arena] finished at turn ${gameInfo.turns}: " +
            (winner?.let { "VICTORY: ${it.civID}" } ?: "no victory (turn limit)"))
        println("[arena] trajectories: deployment/data/${gameInfo.gameId}/ in Civagent_repo (brain side)")
        println("[arena] stats: ${statsFile.file().absolutePath}")
    }

    private fun parseArgs(args: Array<String>): Options {
        val options = Options()
        var i = 0
        while (i < args.size) {
            when (args[i]) {
                "--map" -> options.mapName = args[i + 1]
                "--players" -> options.players = args[i + 1].split(',')
                    .mapNotNull { entry ->
                        val parts = entry.split(':')
                        if (parts.size == 2) parts[0].trim() to parts[1].trim() else null
                    }
                "--mods" -> options.mods = args[i + 1].split(',').map { it.trim() }
                "--difficulty" -> options.difficulty = args[i + 1]
                "--brain" -> options.brainAlways = true
                "--max-turns" -> options.maxTurns = args[i + 1].toInt()
                "--stats-interval" -> options.statsInterval = args[i + 1].toInt().coerceAtLeast(1)
            }
            i += 2
        }
        return options
    }

    private class Options {
        var difficulty: String = "Prince"
        var brainAlways: Boolean = false
        var mapName: String = "arena-51x50.json"
        var players: List<Pair<String, String>> = emptyList()
        var mods: List<String> = emptyList()
        var maxTurns: Int = 250
        /** 国力采样/日志打印间隔（回合）。AI 仍每回合原生 automate；1=每回合采。 */
        var statsInterval: Int = 1
    }
}
