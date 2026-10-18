package com.unciv.logic.civagent

import com.unciv.logic.trade.Trade
import com.unciv.testing.GdxTestRunner
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(GdxTestRunner::class)
class NarratorAndSlotsTest {

    private val testGame = com.unciv.testing.TestGame()
    private lateinit var rome: com.unciv.logic.civilization.Civilization

    @Before
    fun setUp() {
        testGame.makeHexagonalMap(5)
        rome = testGame.addCiv()
        testGame.addCity(rome, testGame.tileMap[0, 0])
    }

    @Test
    fun `narrator renders plain language never raw json`() {
        val war = BrainIntent(type = "declare_war", targetCivID = "Egypt")
        val trade = BrainIntent(
            type = "propose_trade", targetCivID = "Egypt",
            trade = BrainTrade(
                ourOffers = listOf(BrainTradeOffer(type = "Gold", amount = 25f)),
                theirOffers = listOf(BrainTradeOffer(type = "Luxury_Resource", ref = "Ivory", amount = 1f))
            )
        )
        val text = IntentNarrator.describe(rome, listOf(war, trade))
        // key facts survive rendering (translation state may vary headless)
        assertTrue("Egypt missing in: $text", "Egypt" in text)
        assertTrue("offer amounts missing in: $text", "25" in text && "Ivory" in text)
        // never raw JSON
        assertTrue("raw json leaked: $text", "{" !in text && "}" !in text)
    }

    @Test
    fun `narrator order puts trades before war before rest`() {
        val trade = BrainIntent(type = "propose_trade")
        val war = BrainIntent(type = "declare_war")
        val chat = BrainIntent(type = "chat")
        val sorted = listOf(war, chat, trade).sortedBy { IntentNarrator.order(it) }
        assertEquals(listOf(trade, war, chat), sorted)
    }

    @Test
    fun `p5 reconcile clears slots whose engine request is gone`() {
        val egypt = testGame.addCiv()
        testGame.addCity(egypt, testGame.tileMap[3, 0])
        testGame.gameInfo.getCivilization(rome.civID)
            .diplomacyFunctions.makeCivilizationsMeet(egypt)
        val trade = Trade()
        AgentTradeSlots.put("g1", rome.civID, egypt.civID, trade)
        egypt.tradeRequests.add(com.unciv.logic.trade.TradeRequest(rome.civID, trade.reverse()))
        AgentTradeSlots.reconcile("g1", rome)
        org.junit.Assert.assertNotNull("pending request keeps the slot", AgentTradeSlots.get("g1", rome.civID, egypt.civID))

        // human accepts/declines -> engine tradeRequests cleared -> slot must follow
        egypt.tradeRequests.clear()
        AgentTradeSlots.reconcile("g1", rome)
        org.junit.Assert.assertNull("stale slot must be cleared", AgentTradeSlots.get("g1", rome.civID, egypt.civID))
    }

    @Test
    fun `trade slots keep one trade per pair and replace on re-put`() {
        val t1 = Trade()
        val t2 = Trade()
        AgentTradeSlots.put("g1", "Rome", "Egypt", t1)
        AgentTradeSlots.put("g1", "Egypt", "Rome", t2) // same pair, reversed argument order
        org.junit.Assert.assertSame(t2, AgentTradeSlots.get("g1", "Rome", "Egypt"))
        org.junit.Assert.assertSame(t2, AgentTradeSlots.get("g1", "Egypt", "Rome"))

        val t3 = Trade()
        AgentTradeSlots.put("g1", "Rome", "Egypt", t3)
        org.junit.Assert.assertSame(t3, AgentTradeSlots.get("g1", "rome", "EGYPT")) // case-insensitive pair
        AgentTradeSlots.put("g2", "Rome", "Egypt", t1) // other game unaffected
        org.junit.Assert.assertSame(t1, AgentTradeSlots.get("g2", "Rome", "Egypt"))
        org.junit.Assert.assertSame(t3, AgentTradeSlots.get("g1", "Rome", "Egypt"))
    }
}
