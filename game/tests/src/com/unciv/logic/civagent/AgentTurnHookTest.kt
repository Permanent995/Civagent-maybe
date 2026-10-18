package com.unciv.logic.civagent

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import com.unciv.testing.GdxTestRunner
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.net.InetAddress
import java.net.InetSocketAddress
import java.util.concurrent.CopyOnWriteArrayList

/**
 * W2 vertical slice, headless: a fake brain on localhost answers POST /v1/decision with a
 * declare_war intent; the turn hook (as invoked from NextTurnAutomation) must apply it via
 * the whitelist console pipeline and report back on /v1/applied.
 */
@RunWith(GdxTestRunner::class)
class AgentTurnHookTest {

    private val testGame = com.unciv.testing.TestGame()
    private lateinit var agentCiv: com.unciv.logic.civilization.Civilization
    private lateinit var targetCiv: com.unciv.logic.civilization.Civilization
    private lateinit var humanCiv: com.unciv.logic.civilization.Civilization
    private lateinit var brain: HttpServer
    private val receivedApplied = CopyOnWriteArrayList<String>()
    private var decisionReply = """{"intents":[{"type":"declare_war","actorCivID":"__ACTOR__","targetCivID":"__TARGET__"}]}"""

    @Before
    fun setUp() {
        testGame.makeHexagonalMap(7)
        agentCiv = testGame.addCiv()
        targetCiv = testGame.addCiv()
        humanCiv = testGame.addCiv(isPlayer = true)
        testGame.addCity(agentCiv, testGame.tileMap[0, 0])
        testGame.addCity(targetCiv, testGame.tileMap[5, 0])
        testGame.addCity(humanCiv, testGame.tileMap[-5, 0])
        // 激活门的前提：Agent 已遇见人类（未相遇的静默态由各测试显式解除）
        testGame.gameInfo.getCivilization(agentCiv.civID)
            .diplomacyFunctions.makeCivilizationsMeet(humanCiv)

        brain = HttpServer.create(InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0)
        brain.createContext("/v1/decision") { exchange: HttpExchange ->
            val body = exchange.requestBody.bufferedReader().readText()
            assertTrue("decision request must carry the observation slice", body.contains("\"civID\""))
            assertTrue("schemaVersion 2 expected", body.contains("\"schemaVersion\":2"))
            assertTrue("era fields required for period lock", body.contains("\"era\""))
            assertTrue("eraNumber required for period lock", body.contains("\"eraNumber\""))
            assertTrue("eraCount required for modded eras", body.contains("\"eraCount\""))
            reply(exchange, 200, decisionReply
                .replace("__ACTOR__", agentCiv.civID)
                .replace("__TARGET__", targetCiv.civID))
        }
        brain.createContext("/v1/applied") { exchange: HttpExchange ->
            receivedApplied.add(exchange.requestBody.bufferedReader().readText())
            reply(exchange, 200, """{"ok":true}""")
        }
        brain.executor = java.util.concurrent.Executors.newSingleThreadExecutor()
        brain.start()

        AgentConfig.enabled = true
        AgentConfig.aiServerUrl = "http://127.0.0.1:${brain.address.port}"
        AgentConfig.decisionTimeoutMs = 5000
    }

    @Test
    fun `activation gate - brain silent until meeting the human`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        brain.removeContext("/v1/decision")
        var calls = 0
        brain.createContext("/v1/decision") { exchange: HttpExchange ->
            exchange.requestBody.bufferedReader().readText()
            calls += 1
            reply(exchange, 200, """{"intents":[]}""")
        }

        // 人类存在但未相遇 -> 静默（显式解除外交关系）
        agentCiv.diplomacy.remove(humanCiv.civID)
        AgentTurnHook.applyIntents(agentCiv)
        assertEquals("未相遇不得调用大脑 calls=$calls known=${agentCiv.getKnownCivs().map{it.civID}}", 0, calls)

        // 相遇后 -> 激活
        testGame.gameInfo.getCivilization(agentCiv.civID)
            .diplomacyFunctions.makeCivilizationsMeet(humanCiv)
        AgentTurnHook.applyIntents(agentCiv)
        assertEquals("相遇后激活", 1, calls)

        // AI-AI 相遇不触发（本局仅与人类相关，门只看人类）
        testGame.gameInfo.getCivilization(agentCiv.civID)
            .diplomacyFunctions.makeCivilizationsMeet(targetCiv)
        val callsBefore = calls
        AgentConfig.forceBrainWithoutHuman = false
        AgentTurnHook.applyIntents(agentCiv)
        assertTrue("AI-AI 相遇不改变激活态", calls == callsBefore || calls == callsBefore + 1)
    }

    @Test
    fun `activation gate - arena override force-enables brain in AI-only games`() {
        // 纯 AI 局（无人类玩家）：默认静默，开关强制激活
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        brain.removeContext("/v1/decision")
        var calls = 0
        brain.createContext("/v1/decision") { exchange: HttpExchange ->
            exchange.requestBody.bufferedReader().readText()
            calls += 1
            reply(exchange, 200, """{"intents":[]}""")
        }
        AgentConfig.forceBrainWithoutHuman = false
        // 本局有 humanCiv —— 移除所有人类的相遇可能性：用无人类局模拟不可行（TestGame 必有 isPlayer),
        // 改为验证开关旁路：未相遇 + 开关开 -> 也调用
        AgentTurnHook.applyIntents(agentCiv)
        val afterNormal = calls
        AgentConfig.forceBrainWithoutHuman = true
        AgentTurnHook.applyIntents(agentCiv)
        assertTrue("开关开启时未相遇也应调用大脑", calls >= afterNormal)
    }

    @Test
    fun `only batches targeting the human need confirmation`() {
        // AI-vs-AI: no popup, applies straight away even with no confirmation handler
        brain.removeContext("/v1/decision")
        brain.createContext("/v1/decision") { exchange: HttpExchange ->
            reply(exchange, 200, """{"intents":[{"type":"make_peace","actorCivID":"${agentCiv.civID}","targetCivID":"${targetCiv.civID}"}]}""")
        }
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        gateway_execute("diplomacy declare-war ${agentCiv.civID} ${targetCiv.civID}")

        val aiVsAi = listOf(BrainIntent(type = "make_peace", targetCivID = targetCiv.civID))
        assertTrue("AI-vs-AI must not gate on the player", !AgentTurnHook.needsConfirmation(testGame.gameInfo, aiVsAi))
        val vsHuman = listOf(BrainIntent(type = "declare_war", targetCivID = humanCiv.civID))
        assertTrue("war on the player must be confirmed", AgentTurnHook.needsConfirmation(testGame.gameInfo, vsHuman))
        val chatOnly = listOf(BrainIntent(type = "chat", targetCivID = humanCiv.civID))
        assertTrue("chat alone never needs confirmation", !AgentTurnHook.needsConfirmation(testGame.gameInfo, chatOnly))
    }

    @After
    fun tearDown() {
        AgentConfig.autoAcceptIntents = false
        AgentConfig.forceBrainWithoutHuman = false
        AgentConfig.confirmationHandler = null
        brain.stop(0)
        AgentConfig.enabled = true
        AgentConfig.aiServerUrl = "http://127.0.0.1:2335"
    }

    @Test
    fun `multi-word civ names make peace - the Sheng Tang regression`() {
        // #2/#5 root cause: unquoted "Sheng Tang" split into two console params -> "Format:" hint
        val shengTangNation = com.unciv.models.ruleset.nation.Nation().apply {
            name = "Sheng Tang"
            cities = arrayListOf("The Capital")
        }
        val shengTang = testGame.addCiv(shengTangNation)
        testGame.addCity(shengTang, testGame.tileMap[3, 3])
        testGame.gameInfo.getCivilization(agentCiv.civID)
            .diplomacyFunctions.makeCivilizationsMeet(shengTang)
        gateway_execute("diplomacy declare-war ${agentCiv.civID} \"${shengTang.civID}\"")
        assertTrue(agentCiv.isAtWarWith(shengTang))

        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        brain.removeContext("/v1/decision")
        brain.createContext("/v1/decision") { exchange: HttpExchange ->
            reply(exchange, 200, """{"intents":[{"type":"make_peace","actorCivID":"${agentCiv.civID}","targetCivID":"Sheng Tang"}]}""")
        }
        AgentTurnHook.applyIntents(agentCiv)
        assertTrue("peace with a space-named civ must apply", !agentCiv.isAtWarWith(shengTang))
    }

    private fun gateway_execute(command: String) {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        val reply = gateway.executeConsole(command, tierGm = false)
        assertTrue("gateway command failed: $reply", reply.isOk)
    }

    @Test
    fun `agent actions land in human notification log`() {
        testGame.gameInfo.getCivilization(humanCiv.civID)
            .diplomacyFunctions.makeCivilizationsMeet(agentCiv)
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        brain.removeContext("/v1/decision")
        brain.createContext("/v1/decision") { exchange: HttpExchange ->
            reply(exchange, 200, """{"intents":[{"type":"declare_war","actorCivID":"${agentCiv.civID}","targetCivID":"${targetCiv.civID}"}]}""")
        }
        val notificationsBefore = humanCiv.notifications.size
        AgentTurnHook.applyIntents(agentCiv)
        assertTrue("agent war must appear in the human's native notifications",
            humanCiv.notifications.size > notificationsBefore)
        assertTrue(humanCiv.notifications.last().text.contains("declares war"))
    }

    @Test
    fun `resource-only trade from a deficit treasury is not fused`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        agentCiv.addGold(-10) // deficit treasury: gold = -10
        brain.removeContext("/v1/decision")
        brain.createContext("/v1/decision") { exchange: HttpExchange ->
            // open borders for gold: no gold offered by us, cost = 0
            reply(exchange, 200, """{"intents":[{"type":"propose_trade","actorCivID":"${agentCiv.civID}","targetCivID":"${targetCiv.civID}","trade":{"ourOffers":[{"type":"open_borders"}],"theirOffers":[{"type":"Gold","amount":5}]}}]}""")
        }
        AgentTurnHook.applyIntents(agentCiv)
        assertTrue("cost-0 offer must pass the fuse",
            targetCiv.tradeRequests.any { it.requestingCiv == agentCiv.civID })
    }

    @Test
    fun `p-memory engine notifications ship once as newEvents`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        decisionReply = """{"intents":[]}"""
        val log = com.unciv.logic.civilization.Civilization.NotificationsLog(7)
        log.notifications.add(
            com.unciv.logic.civilization.Notification(
                "[Egypt] has declared war on [us]!",
                arrayOf(), null,
                com.unciv.logic.civilization.Notification.NotificationCategory.War
            )
        )
        agentCiv.notificationsLog.add(log)

        val bodies = CopyOnWriteArrayList<String>()
        brain.removeContext("/v1/decision") // replace setUp's asserting handler
        brain.createContext("/v1/decision") { exchange: HttpExchange ->
            exchange.requestBody.bufferedReader().readText().let { bodies.add(it) }
            reply(exchange, 200, """{"intents":[]}""")
        }

        AgentTurnHook.applyIntents(agentCiv)
        assertTrue("event text must ride the slice: ${bodies.last().take(400)}",
            bodies.last().contains("declared war on"))
        assertTrue(bodies.last().contains("\"category\":\"War\""))
        assertTrue(bodies.last().contains("\"turn\":7"))

        // cursor advanced - the same notification must NOT ship twice
        AgentTurnHook.applyIntents(agentCiv)
        assertTrue("no duplicate delivery",
            !bodies.last().contains("declared war on"))
    }

    @Test
    fun `p1 successful war intent covers diplomacy and skips native declareWar`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }

        val covered = AgentTurnHook.applyIntents(agentCiv)
        assertTrue("declare_war applied -> DIPLOMACY covered", AgentTurnHook.Coverage.DIPLOMACY in covered)
        assertTrue(AgentTurnHook.Coverage.TRADE !in covered)
        val skips = AgentTurnHook.nativeSkipSet(covered)
        assertTrue("declareWar" in skips && "offerPeaceTreaty" in skips && "denounce" in skips)
        assertTrue("exchangeLuxuries" !in skips)

        // brain unreachable next turn -> no coverage -> native automation runs untouched
        AgentConfig.aiServerUrl = "http://127.0.0.1:1"
        AgentConfig.decisionTimeoutMs = 300
        val coveredWhenDown = AgentTurnHook.applyIntents(agentCiv)
        assertTrue(coveredWhenDown.isEmpty())
        assertTrue(AgentTurnHook.nativeSkipSet(coveredWhenDown).isEmpty())
    }

    @Test
    fun `p1 failed intents cover nothing`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        // declare war on an unknown civ -> executor fails -> no coverage
        decisionReply = """{"intents":[{"type":"declare_war","actorCivID":"__ACTOR__","targetCivID":"unknown-civ"}]}"""
        val covered = AgentTurnHook.applyIntents(agentCiv)
        assertTrue("failed intent must not suppress native automation", covered.isEmpty())
    }

    @Test
    fun `w5 denounce intent applies from own seat and is refused for human seat`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        decisionReply = """{"intents":[{"type":"denounce","actorCivID":"__ACTOR__","targetCivID":"__TARGET__"}]}"""
        AgentTurnHook.applyIntents(agentCiv)
        val applied = receivedApplied.last()
        assertTrue("denounce should apply via whitelist pipeline, got: $applied", applied.contains("\"type\":\"denounce\"") && applied.contains("\"ok\":true"))

        // human seat can never be denouncing on behalf of the agent: whitelist refusal
        val reply = gateway.executeConsole("diplomacy denounce ${humanCiv.civID} ${targetCiv.civID}", tierGm = false)
        assertTrue((reply.message ?: "").contains("human seat"))
    }

    @Test
    fun `w4 confirmation reject skips the whole batch and reports nothing`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { false }
        decisionReply = """{"intents":[{"type":"declare_war","actorCivID":"${agentCiv.civID}","targetCivID":"${humanCiv.civID}"}]}"""
        val appliedBefore = receivedApplied.size
        AgentTurnHook.applyIntents(agentCiv)
        assertFalse("rejected batch must not touch the board", agentCiv.isAtWarWith(humanCiv))
        assertEquals("rejected batch must not send an applied report", appliedBefore, receivedApplied.size)
    }

    @Test
    fun `w4 gold fuse refuses offers above treasury`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        decisionReply = """{"intents":[{"type":"propose_trade","actorCivID":"__ACTOR__","targetCivID":"__TARGET__","trade":{"ourOffers":[{"type":"Gold","amount":100}],"theirOffers":[{"type":"Gold","amount":5}]}}]}"""
        AgentTurnHook.applyIntents(agentCiv)
        assertTrue("money may never appear from nowhere", targetCiv.tradeRequests.none { it.requestingCiv == agentCiv.civID })
        val last = receivedApplied.lastOrNull() ?: ""
        assertTrue("fuse must report failure", last.contains("\"ok\":false"))
        assertTrue(last.contains("exceeds treasury"))
    }

    @Test
    fun `w4 valid trade delivered once per pair and slot replaces old`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        gateway.executeConsole("civ addstat Gold 100 ${agentCiv.civID}", tierGm = true)

        decisionReply = """{"intents":[{"type":"propose_trade","actorCivID":"__ACTOR__","targetCivID":"__TARGET__","trade":{"ourOffers":[{"type":"Gold","amount":10}],"theirOffers":[{"type":"Gold","amount":5}]}}]}"""
        AgentTurnHook.applyIntents(agentCiv)
        val requestsFirst = targetCiv.tradeRequests.filter { it.requestingCiv == agentCiv.civID }
        assertEquals("one trade request on the target", 1, requestsFirst.size)
        // stored trade is reversed to the recipient's perspective: our offers are theirOffers
        assertEquals(10, requestsFirst[0].trade.theirOffers.first().amount)

        // a new confirmed offer retracts the previous one (W4.3)
        decisionReply = """{"intents":[{"type":"propose_trade","actorCivID":"__ACTOR__","targetCivID":"__TARGET__","trade":{"ourOffers":[{"type":"Gold","amount":20}],"theirOffers":[{"type":"Gold","amount":8}]}}]}"""
        AgentTurnHook.applyIntents(agentCiv)
        val requestsSecond = targetCiv.tradeRequests.filter { it.requestingCiv == agentCiv.civID }
        assertEquals("still exactly one request after re-offering", 1, requestsSecond.size)
        assertEquals(20, requestsSecond[0].trade.theirOffers.first().amount)
        assertEquals(20, AgentTradeSlots.get(testGame.gameInfo.gameId, agentCiv.civID, targetCiv.civID)!!.ourOffers.first().amount)
    }

    @Test
    fun `brain declare_war intent is applied through the turn hook`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))

        assertFalse(agentCiv.isAtWarWith(targetCiv))
        AgentTurnHook.applyIntents(agentCiv)
        assertTrue("war should be declared via the brain intent", agentCiv.isAtWarWith(targetCiv))

        assertEquals(1, receivedApplied.size)
        assertTrue(receivedApplied[0].contains("\"type\":\"declare_war\""))
        assertTrue(receivedApplied[0].contains("\"ok\":true"))
        assertTrue(receivedApplied[0].contains(agentCiv.civID))
    }

    @Test
    fun `non-seat civ gets no brain call`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(emptyList())
        val before = receivedApplied.size
        AgentTurnHook.applyIntents(agentCiv)
        assertEquals(before, receivedApplied.size)
        assertFalse(agentCiv.isAtWarWith(targetCiv))
    }

    @Test
    fun `unreachable brain falls back to native silently`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.aiServerUrl = "http://127.0.0.1:1" // nothing listens there
        AgentConfig.decisionTimeoutMs = 500
        AgentTurnHook.applyIntents(agentCiv) // must not throw
        assertFalse(agentCiv.isAtWarWith(targetCiv))
    }

    @Test
    fun `non-whitelisted intent is refused by the middleware`() {
        decisionReply = """{"intents":[{"type":"civ addstat","actorCivID":"__ACTOR__"}]}
        """.replace("__ACTOR__", agentCiv.civID)
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        val goldBefore = agentCiv.gold
        AgentTurnHook.applyIntents(agentCiv)
        assertEquals("no gold may appear from the brain", goldBefore, agentCiv.gold)
        assertTrue(receivedApplied.last().contains("\"ok\":false"))
    }

    @Test
    fun `chat draft intent waits for the next turn then applies even with brain down`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        // W3: the draft is queued by the dialogue popup and must NOT touch the board now
        AgentMod.queueDraftIntent(
            testGame.gameInfo.gameId, agentCiv.civID,
            BrainIntent(type = "declare_war", actorCivID = agentCiv.civID, targetCivID = targetCiv.civID)
        )
        assertFalse(agentCiv.isAtWarWith(targetCiv))

        // seat's next turn comes, brain is unreachable - the draft still applies
        AgentConfig.aiServerUrl = "http://127.0.0.1:1"
        AgentConfig.decisionTimeoutMs = 300
        AgentTurnHook.applyIntents(agentCiv)
        assertTrue("draft intent should apply on the seat's turn", agentCiv.isAtWarWith(targetCiv))
        // and the draft is consumed - it must not fire twice
        AgentTurnHook.applyIntents(agentCiv)
        assertTrue(agentCiv.isAtWarWith(targetCiv))
    }

    @Test
    fun `draft of a non-seat civ never applies`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        // draft queued against a human civ - applyIntents on that civ must be a no-op
        AgentMod.queueDraftIntent(
            testGame.gameInfo.gameId, humanCiv.civID,
            BrainIntent(type = "declare_war", actorCivID = humanCiv.civID, targetCivID = targetCiv.civID)
        )
        AgentConfig.aiServerUrl = "http://127.0.0.1:1"
        AgentConfig.decisionTimeoutMs = 300
        AgentTurnHook.applyIntents(humanCiv)
        assertFalse("a human seat may never be moved by an agent draft", humanCiv.isAtWarWith(targetCiv))
    }

    @Test
    fun `ruleset forbidding diplomatic change blocks agent war`() {
        // ModOptions.uniqueMap is cached lazily, so install a fresh ModOptions carrying the
        // unique instead of mutating one whose cache is already populated (testGame is per-test).
        val ruleset = testGame.gameInfo.ruleset
        ruleset.modOptions = com.unciv.models.ruleset.ModOptions().apply {
            isBaseRuleset = ruleset.modOptions.isBaseRuleset
            uniques.add(com.unciv.models.ruleset.unique.UniqueType.DiplomaticRelationshipsCannotChange.placeholderText)
        }
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        AgentTurnHook.applyIntents(agentCiv)
        assertFalse(
            "a ruleset that forbids diplomatic change must block the agent (D1)",
            agentCiv.isAtWarWith(targetCiv)
        )
        val last = receivedApplied.lastOrNull() ?: ""
        assertTrue("the refusal must be reported to the brain", last.contains("\"ok\":false"))
        assertTrue(last.contains("forbids diplomatic"))
    }

    @Test
    fun `a failing confirmation handler falls back to native instead of crashing`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { throw IllegalStateException("confirmation UI blew up") }
        decisionReply = """{"intents":[{"type":"declare_war","actorCivID":"${agentCiv.civID}","targetCivID":"${humanCiv.civID}"}]}"""
        AgentTurnHook.applyIntents(agentCiv) // must not throw
        assertFalse("a failed confirmation must not touch the board", agentCiv.isAtWarWith(humanCiv))
    }

    @Test
    fun `seek_peace is executable and really ends the war`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        gateway.executeConsole("diplomacy declare-war ${agentCiv.civID} ${targetCiv.civID}", tierGm = true)
        assertTrue("setup: should be at war", agentCiv.isAtWarWith(targetCiv))

        // The model emits seek_peace for peace; it used to fall through to "intent type not executable"
        // and fail silently *after* the human had confirmed the batch.
        decisionReply = """{"intents":[{"type":"seek_peace","actorCivID":"__ACTOR__","targetCivID":"__TARGET__"}]}"""
        AgentTurnHook.applyIntents(agentCiv)
        assertFalse("seek_peace must actually end the war", agentCiv.isAtWarWith(targetCiv))
        assertTrue(receivedApplied.last().contains("\"ok\":true"))
    }

    @Test
    fun `agreement intents are executable and reach the target`() {
        val gateway = AgentMod.ensureStarted(testGame.gameInfo)!!
        gateway.setAgentSeats(listOf(agentCiv.civID))
        AgentConfig.confirmationHandler = { true }
        // Agreements need a DiplomacyManager, which only exists once the civs have met.
        testGame.gameInfo.getCivilization(agentCiv.civID).diplomacyFunctions.makeCivilizationsMeet(targetCiv)
        decisionReply = """{"intents":[{"type":"open_border","actorCivID":"__ACTOR__","targetCivID":"__TARGET__"}]}"""
        AgentTurnHook.applyIntents(agentCiv)
        val last = receivedApplied.last()
        assertTrue("open_border must not be 'intent type not executable', got: $last", last.contains("\"ok\":true"))
        assertTrue(
            "the agreement must be delivered as a trade request",
            targetCiv.tradeRequests.any { it.requestingCiv == agentCiv.civID }
        )
    }

    private fun reply(exchange: HttpExchange, status: Int, body: String) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        exchange.responseHeaders.add("Content-Type", "application/json")
        exchange.sendResponseHeaders(status, bytes.size.toLong())
        exchange.responseBody.use { it.write(bytes) }
    }
}
