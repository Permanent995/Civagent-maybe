package com.unciv.logic.civagent

import com.unciv.logic.civilization.Civilization
import com.unciv.testing.GdxTestRunner
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * P3: fog-of-war guarantees of the observation slice (EXECUTION.md 验收).
 * Every test here is a privacy/fairness property the brain depends on.
 */
@RunWith(GdxTestRunner::class)
class ObservationSlicerTest {

    private val testGame = com.unciv.testing.TestGame()
    private lateinit var self: Civilization
    private lateinit var met: Civilization
    private lateinit var unmet: Civilization

    @Before
    fun setUp() {
        testGame.makeHexagonalMap(7)
        self = testGame.addCiv(isPlayer = true)
        met = testGame.addCiv()
        unmet = testGame.addCiv()
        testGame.addCity(self, testGame.tileMap[0, 0])
        testGame.addCity(met, testGame.tileMap[5, 0])
        testGame.addCity(unmet, testGame.tileMap[-5, 0])
        testGame.gameInfo.getCivilization(self.civID)
            .diplomacyFunctions.makeCivilizationsMeet(met)
    }

    @Test
    fun `unmet civs never appear in the slice`() {
        val slice = ObservationSlicer.slice(testGame.gameInfo, self)
        assertTrue(slice.knownCivs.any { it.civID == met.civID })
        assertFalse("unmet civ must be invisible", slice.knownCivs.any { it.civID == unmet.civID })
    }

    @Test
    fun `hideOtherCivilizationStats strips all stat fields but keeps relations`() {
        testGame.gameInfo.gameParameters.hideOtherCivilizationStats = true
        try {
            val slice = ObservationSlicer.slice(testGame.gameInfo, self)
            val metSummary = slice.knownCivs.first { it.civID == met.civID }
            assertNull(metSummary.gold)
            assertNull(metSummary.population)
            assertNull(metSummary.cities)
            assertNull(metSummary.technologies)
            assertNull(metSummary.force)
            assertNull(metSummary.score)
            assertNull(metSummary.rank)
            assertNull(metSummary.vsSelf)
            // relations and distance are NOT stats - they stay (fog-safe by design)
            assertTrue(metSummary.relationship.isNotEmpty())
        } finally {
            testGame.gameInfo.gameParameters.hideOtherCivilizationStats = false
        }
    }

    @Test
    fun `stats fields present when hiding is off`() {
        val slice = ObservationSlicer.slice(testGame.gameInfo, self)
        val metSummary = slice.knownCivs.first { it.civID == met.civID }
        assertEquals(met.gold, metSummary.gold)
        assertTrue(metSummary.population != null && metSummary.population!! > 0)
        assertTrue(metSummary.score != null)
    }

    @Test
    fun `stock and delta pairs are populated`() {
        val slice = ObservationSlicer.slice(testGame.gameInfo, self)
        // stock+delta pairs (决策 22) must both be present, never silently zero
        assertTrue(slice.gold >= 0)
        assertTrue(slice.sciencePerTurn != 0f || slice.goldPerTurn != 0f || slice.culturePerTurn != 0f)
        for (city in slice.cities) {
            assertTrue(city.foodStored >= 0)
            assertTrue(city.constructionProgress >= 0)
        }
    }

    @Test
    fun `own civ numbers match the engine`() {
        self.addGold(123)
        val slice = ObservationSlicer.slice(testGame.gameInfo, self)
        assertEquals(123, slice.gold)
        assertEquals(self.cities.size, slice.cities.size)
    }

    @Test
    fun `p4 nation personality coefficients land in the slice`() {
        val personality = com.unciv.models.ruleset.nation.Personality().apply {
            declareWar = 8f
            loyal = 3f
        }
        testGame.gameInfo.ruleset.personalities["testpersonality"] = personality
        met.nation.personality = "testpersonality"
        val slice = ObservationSlicer.slice(testGame.gameInfo, met)
        assertTrue("declarewar 8" in slice.personality!!)
        assertTrue("loyal 3" in slice.personality!!)
        assertTrue("off-neutral dims only:", "gold" !in slice.personality!!)

        // human civ -> neutral personality by engine rule
        val selfSlice = ObservationSlicer.slice(testGame.gameInfo, self)
        assertEquals("neutral", selfSlice.personality)
    }

    @Test
    fun `slice carries only acting civ identity - no other civ rows as subjects`() {
        val slice = ObservationSlicer.slice(testGame.gameInfo, self)
        assertEquals(self.civID, slice.civID)
        // knownCivs may mention met civs, but cities/factionBalance are ours alone
        assertTrue(slice.cities.all { self.cities.any { c -> c.name == it.name } })
    }
}
