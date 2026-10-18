package com.unciv.logic.civilization

import com.unciv.logic.civagent.ObservationSlicer
import com.unciv.testing.GdxTestRunner
import com.unciv.testing.TestGame
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/** 派系平衡跟间谍一样是开局开关：关掉后修正、回归、倾斜、观察切片全部不生效。 */
@RunWith(GdxTestRunner::class)
class FactionBalanceToggleTest {
    private val testGame = TestGame()
    private val civ = testGame.addCiv()

    @Before
    fun setup() {
        testGame.makeHexagonalMap(3)
        testGame.addCity(civ, testGame.tileMap[0, 0])
    }

    @Test
    fun `enabled balance still shifts gold strength and regresses`() {
        testGame.gameInfo.gameParameters.factionBalanceEnabled = true
        civ.factionBalance = 50
        assertEquals(-0.20f, civ.getFactionGoldModifier(), 0.001f)
        assertEquals(0.15f, civ.getFactionStrengthModifier(), 0.001f)
        civ.regressFactionBalance()
        assertEquals(49, civ.factionBalance)
        assertTrue(ObservationSlicer.slice(testGame.gameInfo, civ).factionBalance != null)
    }

    @Test
    fun `disabled balance does not modify regress or appear in the slice`() {
        testGame.gameInfo.gameParameters.factionBalanceEnabled = false
        civ.factionBalance = 80
        assertEquals(0f, civ.getFactionGoldModifier(), 0.001f)
        assertEquals(0f, civ.getFactionStrengthModifier(), 0.001f)
        civ.regressFactionBalance()
        civ.adjustFactionBalance(-40)
        assertEquals(80, civ.factionBalance)
        assertNull(ObservationSlicer.slice(testGame.gameInfo, civ).factionBalance)
    }

    @Test
    fun `clone keeps the faction balance switch`() {
        val parameters = testGame.gameInfo.gameParameters
        parameters.factionBalanceEnabled = false
        assertEquals(false, parameters.clone().factionBalanceEnabled)
    }
}
