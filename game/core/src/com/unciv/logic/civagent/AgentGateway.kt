package com.unciv.logic.civagent

import com.badlogic.gdx.graphics.Color
import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import com.unciv.logic.GameInfo
import com.unciv.models.ruleset.RulesetCache
import com.unciv.ui.screens.devconsole.CliInput
import com.unciv.ui.screens.devconsole.ConsoleCommandRoot
import com.unciv.ui.screens.devconsole.ConsoleErrorException
import com.unciv.ui.screens.devconsole.DevConsoleResponse
import com.unciv.ui.screens.devconsole.CliInput.Companion.splitToCliInput
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.net.InetAddress
import java.net.InetSocketAddress
import java.util.concurrent.Executors

/** Public reply shape of the gateway (mirrors [DevConsoleResponse] without Gdx colors). */
@Serializable
data class GatewayReply(
    /** "ok" | "hint" | "error" | "rejected" */
    val status: String,
    val message: String?,
    val isOk: Boolean
)

@Serializable
data class ConsoleRequest(val command: String)

@Serializable
data class SeatsRequest(val agentSeats: List<String>)

@Serializable
data class SeatsReply(val agentSeats: List<String>)

@Serializable
data class HealthReply(val status: String, val gateway: String, val gameId: String, val turn: Int)

/**
 * Localhost HTTP gateway onto the developer console command tree (EXECUTION.md W1).
 *
 * - `POST /console/gm`    full command set, for the local developer
 * - `POST /console/agent` same command set behind [AgentWhitelist]; bodies: `{"command": "..."}`
 * - `GET  /v1/slice?civ=` minimal [ObservationSlice] for one civ
 * - `GET|POST /seats`     inspect / register which civs are CivAgent seats
 * - `GET  /healthz`
 *
 * Binds to 127.0.0.1 only - never exposed to the network.
 * Commands execute inline on the HTTP thread in this W1 prototype; W2's TurnHook will route
 * execution onto the game thread and drive decisions from there.
 */
class AgentGateway(private val gameInfo: GameInfo, port: Int = DEFAULT_PORT) {
    private val console = HeadlessConsole(gameInfo)
    private val commandRoot = ConsoleCommandRoot()
    private val json = Json { encodeDefaults = true; ignoreUnknownKeys = true }
    private val agentSeats = linkedSetOf<String>()
    private var requestedPort = port
    private var server: HttpServer? = null

    val actualPort: Int get() = server?.address?.port ?: -1

    fun start() {
        check(server == null) { "AgentGateway already started" }
        val newServer = bind()
        newServer.createContext("/console/gm") { exchange -> handleConsole(exchange, tierGm = true) }
        newServer.createContext("/console/agent") { exchange -> handleConsole(exchange, tierGm = false) }
        newServer.createContext("/v1/slice") { exchange -> handleSlice(exchange) }
        newServer.createContext("/v1/content") { exchange -> handleContent(exchange) }
        newServer.createContext("/v1/ruleset") { exchange -> handleRuleset(exchange) }
        newServer.createContext("/v1/ruleset/delta") { exchange -> handleRulesetDelta(exchange) }
        newServer.createContext("/seats") { exchange -> handleSeats(exchange) }
        newServer.createContext("/healthz") { exchange -> reply(exchange, 200, json.encodeToString(
            HealthReply("ok", "agent", gameInfo.gameId, gameInfo.turns))) }
        // daemon: a headless arena run must not hang the JVM after main() returns
        newServer.executor = Executors.newSingleThreadExecutor { r ->
            Thread(r, "AgentGateway").apply { isDaemon = true }
        }
        newServer.start()
        server = newServer
    }

    /**
     * Binds to the requested port; if the default port is already taken (a running game client,
     * a leftover process from an earlier session, …) fall back to an ephemeral port so the
     * in-process gateway still works. [actualPort] always reports the real, bound port.
     */
    private fun bind(): HttpServer {
        val bindWith = { p: Int -> HttpServer.create(InetSocketAddress(InetAddress.getByName("127.0.0.1"), p), 0) }
        return try {
            bindWith(requestedPort)
        } catch (exception: java.io.IOException) {
            if (requestedPort == DEFAULT_PORT) bindWith(0) else throw exception
        }
    }

    fun stop() {
        server?.stop(0)
        server = null
    }

    fun setAgentSeats(seats: Collection<String>) {
        agentSeats.clear()
        agentSeats.addAll(seats)
    }

    fun getAgentSeats(): Set<String> = agentSeats.toSet()

    /** Execute one console command string. `tierGm` = full access; otherwise [AgentWhitelist] applies. */
    fun executeConsole(command: String, tierGm: Boolean): GatewayReply {
        if (!tierGm) {
            val check = AgentWhitelist.check(command, agentSeats, gameInfo)
            if (!check.allowed) return GatewayReply("rejected", check.reason, isOk = false)
        }
        val response = try {
            commandRoot.handle(console, command.trim().splitToCliInput())
        } catch (exception: Exception) {
            return GatewayReply("error", exception.message ?: exception.toString(), isOk = false)
        }
        val status = when {
            response.isOK -> "ok"
            response.color == Color.RED -> "error"
            else -> "hint"
        }
        return GatewayReply(status, response.message, response.isOK)
    }

    /** Minimal observation slice for one civ, by civID. */
    fun observationSlice(civID: String): ObservationSlice? =
        gameInfo.civilizations.firstOrNull { it.civID == civID }?.let { ObservationSlicer.slice(gameInfo, it) }

    private fun handleConsole(exchange: HttpExchange, tierGm: Boolean) {
        if (exchange.requestMethod != "POST") return reply(exchange, 405, """{"error":"POST only"}""")
        val body = exchange.requestBody.bufferedReader().readText()
        val request = try {
            json.decodeFromString<ConsoleRequest>(body)
        } catch (exception: Exception) {
            return reply(exchange, 400, """{"error":"bad JSON body: ${exception.message}"}""")
        }
        val reply = executeConsole(request.command, tierGm)
        val httpStatus = when (reply.status) {
            "ok" -> 200
            "hint" -> 200
            "rejected" -> 403
            else -> 400
        }
        reply(exchange, httpStatus, json.encodeToString(reply))
    }

    private fun handleSlice(exchange: HttpExchange) {
        val civID = exchange.requestURI.rawQuery
            ?.split('&')
            ?.firstOrNull { it.startsWith("civ=") }
            ?.removePrefix("civ=")
        if (civID == null) return reply(exchange, 400, """{"error":"missing ?civ=<civID>"}""")
        val slice = observationSlice(civID)
            ?: return reply(exchange, 404, """{"error":"unknown civ: $civID"}""")
        reply(exchange, 200, json.encodeToString(slice))
    }

    private fun queryParams(exchange: HttpExchange): Map<String, String> =
        exchange.requestURI.rawQuery?.split('&')?.mapNotNull {
            val kv = it.split('=', limit = 2)
            if (kv.size != 2) null else kv[0] to java.net.URLDecoder.decode(kv[1], "UTF-8")
        }?.toMap() ?: emptyMap()

    /** M-content-0: read-only projection of the loaded ruleset, so the brain can ask about modded content. */
    private fun handleContent(exchange: HttpExchange) {
        if (exchange.requestMethod != "GET") return reply(exchange, 405, """{"error":"GET only"}""")
        val params = queryParams(exchange)
        val kind = params["kind"]
            ?: return reply(exchange, 400, """{"error":"missing ?kind=<${ContentCards.KINDS.joinToString("|")}>"}""")
        val id = params["id"] ?: return reply(exchange, 400, """{"error":"missing ?id=<name>"}""")
        val result = ContentCards.lookup(gameInfo.ruleset, kind, id)
        reply(exchange, if (result.found) 200 else 404, json.encodeToString(result))
    }

    private fun handleRuleset(exchange: HttpExchange) {
        if (exchange.requestMethod != "GET") return reply(exchange, 405, """{"error":"GET only"}""")
        reply(exchange, 200, json.encodeToString(ContentCards.rulesetRef(gameInfo)))
    }

    /** M-content-1: what this game's ruleset adds/removes vs the unmodded baseline in RulesetCache. */
    private fun handleRulesetDelta(exchange: HttpExchange) {
        if (exchange.requestMethod != "GET") return reply(exchange, 405, """{"error":"GET only"}""")
        val baselineName = gameInfo.gameParameters.baseRuleset
        val baseline = RulesetCache[baselineName]
            ?: return reply(exchange, 409, """{"error":"baseline ruleset not loaded: $baselineName"}""")
        reply(exchange, 200, json.encodeToString(ContentCards.delta(gameInfo.ruleset, baseline)))
    }

    private fun handleSeats(exchange: HttpExchange) {
        when (exchange.requestMethod) {
            "GET" -> reply(exchange, 200, json.encodeToString(SeatsReply(agentSeats.toList())))
            "POST" -> {
                val body = exchange.requestBody.bufferedReader().readText()
                val request = try {
                    json.decodeFromString<SeatsRequest>(body)
                } catch (exception: Exception) {
                    return reply(exchange, 400, """{"error":"bad JSON body: ${exception.message}"}""")
                }
                setAgentSeats(request.agentSeats)
                reply(exchange, 200, json.encodeToString(SeatsReply(agentSeats.toList())))
            }
            else -> reply(exchange, 405, """{"error":"GET or POST only"}""")
        }
    }

    private fun reply(exchange: HttpExchange, httpStatus: Int, body: String) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        exchange.responseHeaders.add("Content-Type", "application/json; charset=utf-8")
        exchange.sendResponseHeaders(httpStatus, if (bytes.isEmpty()) -1 else bytes.size.toLong())
        exchange.responseBody.use { it.write(bytes) }
    }

    companion object {
        const val DEFAULT_PORT = 8081
    }
}
