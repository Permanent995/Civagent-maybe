package com.unciv.logic.civagent

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

@Serializable
data class ChatMessageRequest(
    val gameId: String,
    val fromCiv: String,
    val toCiv: String,
    val text: String,
    val language: String,
    /** Speaking agent seat's era name (period lock for chat). */
    val era: String = "",
    val eraNumber: Int = 0,
    /** Total eras in the ruleset; lets the brain normalize eraNumber on modded rulesets (M-mod-0 D3). */
    val eraCount: Int = 0,
    /** Player→seat intel claim (决策 17.3); never opens fog. */
    val shareIntelClaim: String? = null,
    val shareIntelAboutCiv: String? = null,
    /** Local brain temp path from POST /upload; not stored in GameInfo. */
    val attachmentPath: String? = null,
    /** Game turn when the claim was made - drives the W5.7 claim-verification window. */
    val turn: Int = 0,
)

@Serializable
data class ChatMessageEntry(
    val time: String? = null,
    val from: String,
    val to: String,
    val text: String,
    /** Brain-side event sequence; lets the popup resume SSE without replaying old replies. */
    val seq: Int = 0,
    val draftIntents: List<BrainIntent> = emptyList()
)

@Serializable
data class ChatMessagesReply(val messages: List<ChatMessageEntry> = emptyList())

@Serializable
data class ChatEventEntry(
    val seq: Int,
    val kind: String = "reply",
    val fromCiv: String,
    val toCiv: String,
    val text: String = "",
    val intents: List<BrainIntent> = emptyList(),
    val status: String = "stable"
)

@Serializable
data class UploadReply(val ok: Boolean = false, val path: String? = null, val error: String? = null)

/** GET /v1/prompt - the exact text the brain sent to the model, plus cost hints. */
@Serializable
data class LastPrompt(
    val kind: String = "",
    val time: String? = null,
    val gameId: String = "",
    val civID: String = "",
    val provider: String = "",
    val model: String = "",
    val streaming: Boolean = false,
    /** Rendered as strings by the brain so the client can show them without number coercion. */
    val sampling: Map<String, String> = emptyMap(),
    val systemTokens: Int = 0,
    val userTokens: Int = 0,
    val promptTokens: Int = 0,
    val system: String = "",
    val user: String = ""
)

/**
 * Client for the brain's chat endpoints (W3): POST /message (fire and forget),
 * GET /messages (transcript hydration), GET /events (SSE, reconnected with ?after=seq).
 * Blocking on purpose - callers wrap it in their own threads.
 */
object AgentChatClient {
    private val json = Json { encodeDefaults = true; ignoreUnknownKeys = true; isLenient = true }
    private const val TIMEOUT_MS = 5_000
    private const val SSE_READ_TIMEOUT_MS = 30_000

    /** Query parameters must be encoded: civ ids are names, and mod civs contain spaces or CJK.
     *  A raw space made java.net.URL throw "URL can't contain control characters" - which the
     *  callers swallow into an empty transcript, so history silently vanished. */
    private fun enc(value: String): String = java.net.URLEncoder.encode(value, "UTF-8")

    fun postMessage(
        gameId: String,
        fromCiv: String,
        toCiv: String,
        text: String,
        language: String,
        era: String = "",
        eraNumber: Int = 0,
        eraCount: Int = 0,
        shareIntelClaim: String? = null,
        shareIntelAboutCiv: String? = null,
        attachmentPath: String? = null,
        turn: Int = 0,
    ): Boolean {
        val body = json.encodeToString(
            ChatMessageRequest(
                gameId, fromCiv, toCiv, text, language, era, eraNumber, eraCount,
                shareIntelClaim, shareIntelAboutCiv, attachmentPath, turn,
            )
        )
        return try {
            AgentHttp.postJson("${AgentConfig.aiServerUrl}/message", body, TIMEOUT_MS).isNotBlank()
        } catch (_: Exception) {
            false
        }
    }

    /** Upload a local image to the brain temp store. Returns server path or null. */
    fun uploadFile(fileName: String, bytes: ByteArray): String? {
        val b64 = java.util.Base64.getEncoder().encodeToString(bytes)
        val body = """{"fileName":${json.encodeToString(fileName)},"contentBase64":${json.encodeToString(b64)}}"""
        return try {
            val reply = AgentHttp.postJson("${AgentConfig.aiServerUrl}/upload", body, TIMEOUT_MS * 3)
            json.decodeFromString<UploadReply>(reply).path
        } catch (_: Exception) {
            null
        }
    }

    fun fetchMessages(gameId: String, civA: String, civB: String, after: Int = 0): List<ChatMessageEntry> {
        val url = "${AgentConfig.aiServerUrl}/messages?gameId=${enc(gameId)}&civ1=${enc(civA)}&civ2=${enc(civB)}&after=$after"
        return try {
            // GET, not POST: the brain's /messages route is read-only (POST used to 405 and
            // silently truncate the transcript to the replies the SSE replay re-sent).
            val body = AgentHttp.getJson(url, TIMEOUT_MS)
            json.decodeFromString<ChatMessagesReply>(body).messages
        } catch (_: Exception) {
            emptyList() // network OR parse failure - graceful, no crash
        }
    }

    /** The last prompt the brain assembled (null when it has not run yet). */
    fun fetchLastPrompt(): LastPrompt? = try {
        val body = AgentHttp.getJsonAllowError("${AgentConfig.aiServerUrl}/v1/prompt", TIMEOUT_MS)
        json.decodeFromString<LastPrompt>(body)
    } catch (_: Exception) {
        null
    }

    /** True when the brain answers at all (any HTTP status means it is up). */
    fun isReachable(): Boolean = try {
        AgentHttp.openGet("${AgentConfig.aiServerUrl}/healthz", TIMEOUT_MS).responseCode in 200..599
    } catch (_: Exception) {
        false
    }

    /**
     * Blocking SSE loop; calls [onEvent] for every event newer than [after]. Runs until
     * [shouldContinue] returns false. Reconnects with ?after=<lastSeq> so no event is lost.
     */
    fun listenEvents(
        gameId: String,
        civID: String,
        after: Int = 0,
        onEvent: (ChatEventEntry) -> Unit,
        shouldContinue: () -> Boolean
    ) {
        var lastSeq = after
        while (shouldContinue()) {
            try {
                val connection = AgentHttp.openGet(
                    "${AgentConfig.aiServerUrl}/events?gameId=${enc(gameId)}&civID=${enc(civID)}&after=$lastSeq",
                    SSE_READ_TIMEOUT_MS
                )
                connection.inputStream.bufferedReader().useLines { lines ->
                    for (line in lines) {
                        if (!shouldContinue()) return
                        if (!line.startsWith("data: ")) continue
                        val event = try {
                            json.decodeFromString<ChatEventEntry>(line.removePrefix("data: ").trim())
                        } catch (_: Exception) {
                            continue
                        }
                        // A streaming `partial` deliberately shares its final reply's seq, so
                        // advancing here would make a reconnect skip the finished reply.
                        if (event.kind != "partial") lastSeq = event.seq
                        onEvent(event)
                    }
                }
            } catch (_: Exception) {
                // stream dropped - wait and reconnect, still catching up from lastSeq
            }
            if (shouldContinue()) Thread.sleep(1_000)
        }
    }
}
