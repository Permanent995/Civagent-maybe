package com.unciv.logic.civagent

import com.unciv.testing.GdxTestRunner
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.net.HttpURLConnection
import java.net.URL

/**
 * W1 acceptance (EXECUTION.md M1), headless:
 * - `diplomacy list` over the gateway returns a response
 * - `agent` tier refuses `civ addstat` (would be GM cheating)
 * - `agent` tier refuses declare-war on behalf of a human seat
 * - `gm` tier allows `civ addstat`
 * - observation slice exposes the acting civ's own numbers
 */
@RunWith(GdxTestRunner::class)
class AgentGatewayTest {

    private val testGame = com.unciv.testing.TestGame()
    private lateinit var gateway: AgentGateway
    private lateinit var agentCiv: com.unciv.logic.civilization.Civilization
    private lateinit var otherAgentCiv: com.unciv.logic.civilization.Civilization
    private lateinit var humanCiv: com.unciv.logic.civilization.Civilization

    @Before
    fun setUp() {
        testGame.makeHexagonalMap(7)
        agentCiv = testGame.addCiv()
        otherAgentCiv = testGame.addCiv()
        humanCiv = testGame.addCiv(isPlayer = true)
        // civs without cities count as defeated - console diplomacy refuses them
        testGame.addCity(agentCiv, testGame.tileMap[0, 0])
        testGame.addCity(otherAgentCiv, testGame.tileMap[5, 0])
        testGame.addCity(humanCiv, testGame.tileMap[-5, 0])
        gateway = AgentGateway(testGame.gameInfo, port = 0)
        gateway.setAgentSeats(listOf(agentCiv.civID, otherAgentCiv.civID))
        gateway.start()
    }

    @After
    fun tearDown() {
        gateway.stop()
    }

    @Test
    fun `healthz responds`() {
        val (status, body) = http("GET", "/healthz")
        assertEquals(200, status)
        assertTrue(body.contains("\"status\":\"ok\""))
    }

    @Test
    fun `diplomacy list over agent tier returns a response`() {
        // meet first via gm so the list has content
        val meet = postConsole("/console/gm", "diplomacy meet ${agentCiv.civID} ${otherAgentCiv.civID}")
        assertTrue("gm meet should pass, got: $meet", meet.second.contains("\"status\":\"ok\""))

        val (status, body) = postConsole("/console/agent", "diplomacy list ${agentCiv.civID} ${otherAgentCiv.civID}")
        assertEquals(200, status)
        assertTrue("expected Relation in list output, got: $body", body.contains("Relation"))
    }

    @Test
    fun `agent tier refuses civ addstat`() {
        val (status, body) = postConsole("/console/agent", "civ addstat Gold 99999 ${agentCiv.civID}")
        assertEquals(403, status)
        assertTrue(body.contains("\"status\":\"rejected\""))
        assertEquals("gold must be untouched", 0, agentCiv.gold)
    }

    @Test
    fun `agent tier refuses declare-war on behalf of a human seat`() {
        val (status, body) = postConsole(
            "/console/agent",
            "diplomacy declare-war ${humanCiv.civID} ${agentCiv.civID}"
        )
        assertEquals(403, status)
        assertTrue(body.contains("human seat"))
    }

    @Test
    fun `agent tier refuses non-diplomacy command families`() {
        val (status, body) = postConsole("/console/agent", "unit add Warrior ${agentCiv.civID}")
        assertEquals(403, status)
        assertTrue(body.contains("whitelist"))
    }

    @Test
    fun `agent tier may declare war from its own seat`() {
        val (status, body) = postConsole(
            "/console/agent",
            "diplomacy declare-war ${agentCiv.civID} ${otherAgentCiv.civID}"
        )
        assertEquals(200, status)
        assertTrue("expected ok, got: $body", body.contains("\"status\":\"ok\""))
        assertTrue(agentCiv.isAtWarWith(otherAgentCiv))
    }

    @Test
    fun `gm tier allows civ addstat`() {
        val goldBefore = agentCiv.gold
        val (status, body) = postConsole("/console/gm", "civ addstat Gold 99999 ${agentCiv.civID}")
        assertEquals(200, status)
        assertTrue("expected ok, got: $body", body.contains("\"status\":\"ok\""))
        assertEquals(goldBefore + 99999, agentCiv.gold)
    }

    @Test
    fun `slice exposes own civ numbers and known civs`() {
        postConsole("/console/gm", "diplomacy meet ${agentCiv.civID} ${otherAgentCiv.civID}")
        postConsole("/console/gm", "diplomacy meet ${agentCiv.civID} ${humanCiv.civID}")
        val (status, body) = http("GET", "/v1/slice?civ=${agentCiv.civID}")
        assertEquals(200, status)
        assertTrue(body.contains("\"civID\":\"${agentCiv.civID}\""))
        assertNotNull(gateway.observationSlice(agentCiv.civID))
        val slice = gateway.observationSlice(agentCiv.civID)!!
        assertEquals(agentCiv.gold, slice.gold)
        assertTrue(slice.knownCivs.any { it.civID == otherAgentCiv.civID || it.civID == humanCiv.civID })
        assertFalse(slice.resources.keys.contains("nonexistent-resource"))
    }

    @Test
    fun `seats can be listed and updated over http`() {
        val (getStatus, getBody) = http("GET", "/seats")
        assertEquals(200, getStatus)
        assertTrue(getBody.contains(agentCiv.civID))

        val (postStatus, _) = post("POST", "/seats", """{"agentSeats":["${otherAgentCiv.civID}"]}""")
        assertEquals(200, postStatus)
        assertEquals(setOf(otherAgentCiv.civID), gateway.getAgentSeats())
    }

    // helpers

    private fun postConsole(path: String, command: String): Pair<Int, String> =
        post("POST", path, """{"command":"$command"}""")

    private fun http(method: String, path: String): Pair<Int, String> {
        val connection = URL("http://127.0.0.1:${gateway.actualPort}$path").openConnection() as HttpURLConnection
        connection.requestMethod = method
        return read(connection)
    }

    private fun post(method: String, path: String, body: String): Pair<Int, String> {
        val connection = URL("http://127.0.0.1:${gateway.actualPort}$path").openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.doOutput = true
        connection.setFixedLengthStreamingMode(body.toByteArray().size)
        connection.outputStream.use { it.write(body.toByteArray()) }
        return read(connection)
    }

    private fun read(connection: HttpURLConnection): Pair<Int, String> {
        val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
        return connection.responseCode to (stream?.bufferedReader()?.readText() ?: "")
    }
}
