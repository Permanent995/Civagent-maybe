package com.unciv.logic.civagent

import com.unciv.testing.GdxTestRunner
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(GdxTestRunner::class)
class AgentChatPolicyTest {

    private val testGame = com.unciv.testing.TestGame()
    private lateinit var playerCiv: com.unciv.logic.civilization.Civilization
    private lateinit var metAgent: com.unciv.logic.civilization.Civilization
    private lateinit var unmetAgent: com.unciv.logic.civilization.Civilization

    @Before
    fun setUp() {
        testGame.makeHexagonalMap(7)
        playerCiv = testGame.addCiv(isPlayer = true)
        metAgent = testGame.addCiv()
        unmetAgent = testGame.addCiv()
        testGame.addCity(playerCiv, testGame.tileMap[0, 0])
        testGame.addCity(metAgent, testGame.tileMap[5, 0])
        testGame.addCity(unmetAgent, testGame.tileMap[-5, 0])
        // meet only metAgent
        testGame.gameInfo.getCivilization(playerCiv.civID)
            .diplomacyFunctions.makeCivilizationsMeet(metAgent)
    }

    @Test
    fun `unmet civs are invisible and unchattable`() {
        val seats = listOf(metAgent.civID, unmetAgent.civID)
        val chattable = AgentChatPolicy.chatabaleSeats(testGame.gameInfo, seats, playerCiv)
        assertEquals(listOf(metAgent.civID), chattable)
        assertFalse(AgentChatPolicy.canChatWith(testGame.gameInfo, seats, playerCiv, unmetAgent.civID))
        assertTrue(AgentChatPolicy.canChatWith(testGame.gameInfo, seats, playerCiv, metAgent.civID))
    }

    @Test
    fun `self is never a chat target`() {
        val seats = listOf(playerCiv.civID, metAgent.civID)
        val chattable = AgentChatPolicy.chatabaleSeats(testGame.gameInfo, seats, playerCiv)
        assertTrue(playerCiv.civID !in chattable)
        assertFalse(AgentChatPolicy.canChatWith(testGame.gameInfo, seats, playerCiv, playerCiv.civID))
    }

    @Test
    fun `defeated seats drop out`() {
        // destroy the capital -> civ has no cities -> isDefeated() once its units die too
        // overrideSafeties: a capital is not razeable by default (City.canBeDestroyed).
        metAgent.cities.first().destroyCity(overrideSafeties = true)
        // toList(): getCivUnits() is a live-list sequence; destroying while iterating skips units.
        metAgent.units.getCivUnits().toList().forEach { it.destroy() }
        val seats = listOf(metAgent.civID, unmetAgent.civID)
        val chattable = AgentChatPolicy.chatabaleSeats(testGame.gameInfo, seats, playerCiv)
        assertTrue(metAgent.civID !in chattable)
    }
}
