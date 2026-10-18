package com.unciv.logic.civagent

import com.unciv.testing.GdxTestRunner
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * M-content-0: the ruleset projector. Mods are arbitrary, so the game describes its loaded
 * ruleset on demand instead of the brain ever learning it. Everything is derived from engine
 * fields/predicates + Civilopedia rendering; unknown content must be a clean miss, never a crash.
 */
@RunWith(GdxTestRunner::class)
class ContentCardsTest {

    private val testGame = com.unciv.testing.TestGame()
    private val ruleset get() = testGame.gameInfo.ruleset

    @Test
    fun `unit card exposes capability tags and stats`() {
        val card = ContentCards.project(ruleset, "unit", "Warrior")!!
        assertEquals("unit", card.kind)
        assertEquals("Warrior", card.id)
        assertTrue("military tag expected", card.caps.contains("military"))
        assertTrue("strength must be positive", (card.stats["strength"] ?: 0) > 0)
    }

    @Test
    fun `settler is tagged as city founder`() {
        val card = ContentCards.project(ruleset, "unit", "Settler")!!
        assertTrue("city-founder tag expected", card.caps.contains("city-founder"))
        assertTrue("settler is civilian", card.caps.contains("civilian"))
    }

    @Test
    fun `resource card carries the ruleset kind`() {
        val card = ContentCards.project(ruleset, "resource", "Iron")!!
        assertTrue("strategic kind expected, got ${card.caps}", card.caps.contains("kind:strategic"))
        assertEquals("Mine", card.requires["improvement"])
        assertEquals("Iron Working", card.requires["revealedBy"])
    }

    @Test
    fun `building card distinguishes plain buildings`() {
        val card = ContentCards.project(ruleset, "building", "Monument")!!
        assertTrue("plain building tag expected", card.caps.contains("building"))
        assertFalse("Monument is not a wonder", card.caps.contains("wonder"))
    }

    @Test
    fun `unknown content is a clean miss, not a crash`() {
        val miss = ContentCards.lookup(ruleset, "unit", "Definitely Not A Unit")
        assertFalse(miss.found)
        assertNotNull(miss.error)
        assertNull(ContentCards.project(ruleset, "unit", "Definitely Not A Unit"))
        assertNull(ContentCards.project(ruleset, "nonsense-kind", "Warrior"))
    }

    @Test
    fun `ruleset ref has a stable fingerprint`() {
        val ref = ContentCards.rulesetRef(testGame.gameInfo)
        assertTrue(ref.name.isNotBlank())
        assertEquals("8 bytes hex", 16, ref.fingerprint.length)
        assertEquals("fingerprint must be stable", ref.fingerprint, ContentCards.rulesetRef(testGame.gameInfo).fingerprint)
    }

    @Test
    fun `gateway serves content cards over http`() {
        val gateway = AgentGateway(testGame.gameInfo, port = 0)
        gateway.start()
        try {
            val base = "http://127.0.0.1:${gateway.actualPort}"

            val hit = httpGet("$base/v1/content?kind=unit&id=Warrior")
            assertEquals(200, hit.first)
            assertTrue("card must carry caps: ${hit.second}", hit.second.contains("\"military\""))
            assertTrue("card must carry desc or uniques", hit.second.contains("\"kind\":\"unit\""))

            val miss = httpGet("$base/v1/content?kind=unit&id=Nope")
            assertEquals(404, miss.first)
            assertTrue(miss.second.contains("\"found\":false"))

            val rulesetRef = httpGet("$base/v1/ruleset")
            assertEquals(200, rulesetRef.first)
            assertTrue(rulesetRef.second.contains("fingerprint"))

            val delta = httpGet("$base/v1/ruleset/delta")
            assertEquals(200, delta.first)
            assertTrue("delta must name its baseline: ${delta.second}", delta.second.contains("baseline"))
        } finally {
            gateway.stop()
        }
    }

    @Test
    fun `resource kinds come from the ruleset`() {
        val kinds = ContentCards.resourceKinds(ruleset, listOf("Iron", "Wheat", "Definitely Not A Resource"))
        assertEquals("strategic", kinds["Iron"])
        assertEquals("bonus", kinds["Wheat"])
        assertFalse("unknown resources are dropped, not invented", kinds.containsKey("Definitely Not A Resource"))
    }

    @Test
    fun `delta vs an empty baseline reports additions`() {
        val delta = ContentCards.delta(ruleset, com.unciv.models.ruleset.Ruleset())
        assertTrue((delta.added["unit"]?.size ?: 0) > 0)
        assertTrue((delta.added["tech"]?.size ?: 0) > 0)
    }

    @Test
    fun `delta vs the same ruleset is empty`() {
        val delta = ContentCards.delta(ruleset, ruleset.clone())
        assertTrue(delta.added.isEmpty())
        assertTrue(delta.removed.isEmpty())
    }

    private fun httpGet(url: String): Pair<Int, String> {
        val connection = java.net.URL(url).openConnection() as java.net.HttpURLConnection
        connection.requestMethod = "GET"
        val code = connection.responseCode
        val stream = if (code in 200..299) connection.inputStream else connection.errorStream
        val body = stream.bufferedReader().readText()
        connection.disconnect()
        return code to body
    }
}
