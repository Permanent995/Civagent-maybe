package com.unciv.ui.screens.overviewscreen

import com.unciv.logic.city.City
import com.unciv.testing.GdxTestRunner
import com.unciv.testing.TestGame
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(GdxTestRunner::class)

class FactionBalanceScreenTests {

    private lateinit var testGame: TestGame

    @Before
    fun setUp() {
        testGame = TestGame()
        testGame.makeHexagonalMap(4)
    }

    @Test
    fun `capital has valid faction scores`() {
        val civ = testGame.addCiv(isPlayer = true)
        val tile = testGame.tileMap[0, 0]!!
        val city = testGame.addCity(civ, tile)

        val (faction, score) = FactionBalanceScreen.determineFactionAndScore(city, civ)
        // Capital should lean peace (Palace + population)
        assertEquals(FactionBalanceScreen.Faction.PEACE, faction)
        assertTrue("Score should be positive", score > 0)
    }

    @Test
    fun `city without buildings is peace faction`() {
        val civ = testGame.addCiv()
        val tile = testGame.tileMap[0, 0]!!
        val city = testGame.addCity(civ, tile)

        val (faction, _) = FactionBalanceScreen.determineFactionAndScore(city, civ)
        assertEquals(FactionBalanceScreen.Faction.PEACE, faction)
    }

    @Test
    fun `border city gets war bonus`() {
        val civ1 = testGame.addCiv(isPlayer = true)
        val civ2 = testGame.addCiv()

        val tile1 = testGame.tileMap[0, 0]!!
        val city1 = testGame.addCity(civ1, tile1)

        // Place civ2's city adjacent to make it a border city
        val tile2 = testGame.tileMap[1, 0]!!
        testGame.addCity(civ2, tile2)

        val (faction, _) = FactionBalanceScreen.determineFactionAndScore(city1, civ1)
        assertEquals(FactionBalanceScreen.Faction.WAR, faction)
    }

    @Test
    fun `city with wonder gets peace bonus`() {
        val civ = testGame.addCiv()
        val tile = testGame.tileMap[0, 0]!!
        val city = testGame.addCity(civ, tile)
        val wonder = testGame.createWonder()
        city.cityConstructions.addBuilding(wonder)

        val (faction, _) = FactionBalanceScreen.determineFactionAndScore(city, civ)
        assertEquals(FactionBalanceScreen.Faction.PEACE, faction)
    }

    @Test
    fun `population increases peace contribution`() {
        val civ = testGame.addCiv()
        val tile = testGame.tileMap[0, 0]!!
        val city = testGame.addCity(civ, tile, initialPopulation = 5)

        val (faction, score) = FactionBalanceScreen.determineFactionAndScore(city, civ)
        assertEquals(FactionBalanceScreen.Faction.PEACE, faction)
        assertTrue("Score should be at least 5 from population", score >= 5)
    }

    @Test
    fun `faction score is always non-negative`() {
        val civs = (0..4).map { testGame.addCiv() }
        for ((i, civ) in civs.withIndex()) {
            val tile = testGame.tileMap[i, 0]!!
            val city = testGame.addCity(civ, tile)
            val (_, score) = FactionBalanceScreen.determineFactionAndScore(city, civ)
            assertTrue("Score should be >= 0 for city $i", score >= 0)
        }
    }

    @Test
    fun `border city with military and wonder is still war`() {
        val civ1 = testGame.addCiv(isPlayer = true)
        val civ2 = testGame.addCiv()

        val tile1 = testGame.tileMap[0, 0]!!
        val city1 = testGame.addCity(civ1, tile1)

        // Make it a border city
        val tile2 = testGame.tileMap[1, 0]!!
        testGame.addCity(civ2, tile2)

        // Also add a wonder - but border cities should still be WAR (border+8 > wonder+6)
        val wonder = testGame.createWonder()
        city1.cityConstructions.addBuilding(wonder)

        val (faction, _) = FactionBalanceScreen.determineFactionAndScore(city1, civ1)
        assertEquals(FactionBalanceScreen.Faction.WAR, faction)
    }
}
