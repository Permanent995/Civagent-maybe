package com.unciv.logic.civagent

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

/**
 * Local-only LLM secrets (决策 21).
 *
 * Path: `~/.civagent/secrets.json` — never GameInfo / gameParameters / multiplayer PUT.
 * The game may write this file and push to the local brain; it must not put the key
 * into decision/chat request bodies.
 */
@Serializable
data class AgentSecrets(
    var provider: String = "deepseek",
    var apiKey: String = "",
    var baseUrl: String = "",
    var aiServerUrl: String = "http://127.0.0.1:2335",
    /** Absolute path to Civagent_repo (for in-game brain auto-start). */
    var repoRoot: String = "",
    /** Model id the brain should route to. Empty = the provider's default model. */
    var model: String = "",
    /** Stream replies token-by-token into the dialogue instead of waiting for the whole answer. */
    var streaming: Boolean = false,
    /** 总开关：关闭 = 全部席位回退原生 AI，大脑与网关不启动（Options → Agent 可切换）。 */
    var brainEnabled: Boolean = true,
    // Sampling knobs — null means "not set", so the provider default applies.
    var temperature: Double? = null,
    /** Max output length. */
    var maxTokens: Int? = null,
    var frequencyPenalty: Double? = null,
    var presencePenalty: Double? = null,
    var topP: Double? = null,
) {
    /** Wire shape for POST /v1/config: only the knobs the user actually set. */
    fun samplingJson(): String {
        val parts = mutableListOf<String>()
        temperature?.let { parts.add("\"temperature\":$it") }
        maxTokens?.let { parts.add("\"max_tokens\":$it") }
        frequencyPenalty?.let { parts.add("\"frequency_penalty\":$it") }
        presencePenalty?.let { parts.add("\"presence_penalty\":$it") }
        topP?.let { parts.add("\"top_p\":$it") }
        return "{" + parts.joinToString(",") + "}"
    }
}

object AgentSecretsStore {
    private val json = Json { ignoreUnknownKeys = true; prettyPrint = true; encodeDefaults = true }

    fun secretsFile(): File =
        File(System.getProperty("user.home"), ".civagent${File.separator}secrets.json")

    fun load(): AgentSecrets {
        val file = secretsFile()
        if (!file.isFile) return AgentSecrets()
        return try {
            json.decodeFromString<AgentSecrets>(file.readText(Charsets.UTF_8))
        } catch (_: Exception) {
            AgentSecrets()
        }
    }

    fun save(secrets: AgentSecrets) {
        val file = secretsFile()
        file.parentFile?.mkdirs()
        file.writeText(json.encodeToString(secrets), Charsets.UTF_8)
    }

    /** UI hint only — never log or send the full key. */
    fun keyHint(apiKey: String): String {
        val trimmed = apiKey.trim()
        if (trimmed.isEmpty()) return "(not set)"
        if (trimmed.length <= 4) return "••••"
        return "••••${trimmed.takeLast(4)}"
    }

    fun applyToRuntime(secrets: AgentSecrets) {
        AgentConfig.aiServerUrl = secrets.aiServerUrl.trim().ifEmpty { "http://127.0.0.1:2335" }
            .trimEnd('/')
        AgentConfig.enabled = secrets.brainEnabled
    }
}
