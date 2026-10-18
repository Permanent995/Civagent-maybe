package com.unciv.logic.civagent

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.InetAddress
import java.net.InetSocketAddress

/**
 * W3 chat client resilience.
 *
 * Regression for the `java.net.ConnectException: Connection refused` crash that fired in
 * [com.unciv.ui.screens.worldscreen.LeaderDialoguePopup.switchSeat] when the brain
 * (ai_server on [AgentConfig.aiServerUrl]) was down: the chat popup must degrade to an
 * empty transcript / a failed send / "not reachable", never throw out of a
 * `Concurrency.run` coroutine (which the crash handler turns into a crash dialog).
 */
class AgentChatClientTest {

    private lateinit var brain: HttpServer
    private val originalUrl: String = AgentConfig.aiServerUrl
    private val lastMessageBody = java.util.concurrent.atomic.AtomicReference("")
    private val lastMessagesQuery = java.util.concurrent.atomic.AtomicReference("")

    @Before
    fun setUp() {
        // A real brain on an ephemeral port for the "up" cases.
        brain = HttpServer.create(InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0)
        brain.createContext("/healthz") { exchange: HttpExchange -> reply(exchange, 200, """{"llm":"ok","provider":"mock"}""") }
        brain.createContext("/messages") { exchange: HttpExchange ->
            // Regression: the client must GET this (a POST 405'd and silently emptied the transcript).
            assertTrue("fetchMessages must use GET", exchange.requestMethod == "GET")
            lastMessagesQuery.set(exchange.requestURI.rawQuery ?: "")
            reply(exchange, 200, """{"messages":[
                {"from":"China","to":"Rome","text":"hello","seq":1},
                {"from":"Rome","to":"China","text":"greetings","seq":1}
            ]}""")
        }
        brain.createContext("/message") { exchange: HttpExchange ->
            lastMessageBody.set(exchange.requestBody.bufferedReader().readText())
            reply(exchange, 200, """{"started":true}""")
        }
        brain.executor = java.util.concurrent.Executors.newSingleThreadExecutor()
        brain.start()
    }

    @After
    fun tearDown() {
        brain.stop(0)
        AgentConfig.aiServerUrl = originalUrl
    }

    /** A port that is currently not bound by anything (bind+close gives a free port). */
    private fun deadPort(): Int = java.net.ServerSocket(0).use { it.localPort }

    @Test
    fun `fetchMessages returns empty instead of crashing when brain unreachable`() {
        AgentConfig.aiServerUrl = "http://127.0.0.1:${deadPort()}"
        val result = AgentChatClient.fetchMessages("game1", "China", "Rome")
        assertTrue(result.isEmpty())
    }

    @Test
    fun `postMessage returns false instead of crashing when brain unreachable`() {
        AgentConfig.aiServerUrl = "http://127.0.0.1:${deadPort()}"
        assertFalse(AgentChatClient.postMessage("game1", "China", "Rome", "hello", "en"))
    }

    @Test
    fun `isReachable returns false when brain unreachable`() {
        AgentConfig.aiServerUrl = "http://127.0.0.1:${deadPort()}"
        assertFalse(AgentChatClient.isReachable())
    }

    @Test
    fun `isReachable true and message send succeeds when brain up`() {
        AgentConfig.aiServerUrl = "http://127.0.0.1:${brain.address.port}"
        assertTrue(AgentChatClient.isReachable())
        assertTrue(AgentChatClient.postMessage("game1", "China", "Rome", "hello", "en"))
    }

    @Test
    fun `postMessage carries eraCount so modded eras normalize`() {
        AgentConfig.aiServerUrl = "http://127.0.0.1:${brain.address.port}"
        assertTrue(
            AgentChatClient.postMessage("game1", "China", "Rome", "hello", "en", "Neolithic era", 0, 10)
        )
        assertTrue(
            "eraCount must be sent, got: ${lastMessageBody.get()}",
            lastMessageBody.get().contains("\"eraCount\":10")
        )
    }

    @Test
    fun `fetchMessages hydrates both sides of the transcript over GET`() {
        AgentConfig.aiServerUrl = "http://127.0.0.1:${brain.address.port}"
        val messages = AgentChatClient.fetchMessages("game1", "China", "Rome")
        assertTrue("both speakers must survive hydration, got $messages", messages.size == 2)
        assertTrue(messages.any { it.from == "China" && it.text == "hello" })
        assertTrue(messages.any { it.from == "Rome" && it.text == "greetings" })
        assertTrue("seq must round-trip so the listener can resume", messages.all { it.seq == 1 })
    }

    @Test
    fun `fetchMessages percent-encodes civ names that contain spaces`() {
        // Mod civ ids are names ("Sheng Tang", "盛唐"); an unencoded space made java.net.URL throw
        // and the swallowed exception emptied the transcript.
        AgentConfig.aiServerUrl = "http://127.0.0.1:${brain.address.port}"
        assertTrue(AgentChatClient.fetchMessages("game1", "MongolEmpire", "Sheng Tang").isNotEmpty())
        val raw = lastMessagesQuery.get()
        assertTrue("no raw spaces allowed in the query, got: $raw", !raw.contains(" "))
        assertTrue(
            "civ name must arrive percent-encoded, got: $raw",
            raw.contains("Sheng+Tang") || raw.contains("Sheng%20Tang")
        )
    }

    private fun reply(exchange: HttpExchange, status: Int, body: String) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        exchange.responseHeaders.add("Content-Type", "application/json")
        exchange.sendResponseHeaders(status, bytes.size.toLong())
        exchange.responseBody.use { it.write(bytes) }
    }
}
