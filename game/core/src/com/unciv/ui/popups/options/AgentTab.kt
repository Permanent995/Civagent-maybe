package com.unciv.ui.popups.options

import com.badlogic.gdx.graphics.Color
import com.badlogic.gdx.scenes.scene2d.Actor
import com.badlogic.gdx.scenes.scene2d.ui.CheckBox
import com.badlogic.gdx.scenes.scene2d.ui.SelectBox
import com.badlogic.gdx.scenes.scene2d.ui.Table
import com.badlogic.gdx.scenes.scene2d.utils.ChangeListener
import com.badlogic.gdx.utils.Array as GdxArray
import com.unciv.logic.civagent.AgentBrainLauncher
import com.unciv.logic.civagent.AgentConfig
import com.unciv.logic.civagent.AgentHttp
import com.unciv.logic.civagent.AgentSecrets
import com.unciv.logic.civagent.AgentSecretsStore
import com.unciv.ui.screens.worldscreen.AgentPromptViewerPopup
import com.unciv.ui.components.extensions.addSeparator
import com.unciv.ui.components.extensions.toLabel
import com.unciv.ui.components.extensions.toTextButton
import com.unciv.ui.components.input.onClick
import com.unciv.ui.components.widgets.UncivTextField
import com.unciv.utils.Concurrency
import com.unciv.utils.Log

/**
 * Main-menu Options → Agent: configure local brain URL + vendor key.
 * Keys go to `~/.civagent/secrets.json` and POST /v1/config (localhost only) — never gameParameters.
 */
internal class AgentTab(
    optionsPopup: OptionsPopup
) : OptionsPopupTab(optionsPopup) {

    private val providers = listOf(
        "deepseek", "openai", "openai_compatible", "anthropic", "gemini", "ollama"
    )

    private val providerBox = SelectBox<String>(skin)
    private val modelBox = SelectBox<String>(skin)
    private val streamingCheck = CheckBox(" Stream replies as they arrive", skin)
    private val approvalCheck = CheckBox(" Ask before actions that target me", skin)
    private val brainCheck = CheckBox(" Enable CivAgent brain", skin)
    private val keyField = UncivTextField("API key")
    private val baseUrlField = UncivTextField("Base URL (optional / compatible gateway)")
    private val brainUrlField = UncivTextField("Brain URL")
    private val repoRootField = UncivTextField("Civagent_repo path (for auto-start)")
    private val statusLabel = "".toLabel()
    private val hintLabel = "".toLabel(Color.GRAY)

    /** Known model ids per provider; "" means "let the provider pick its default". */
    private val modelsByProvider = mapOf(
        "deepseek" to listOf("", "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"),
        "openai" to listOf("", "gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3-mini"),
        "openai_compatible" to listOf(""),
        "anthropic" to listOf("", "claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"),
        "gemini" to listOf("", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"),
        "ollama" to listOf("", "llama3.1", "qwen2.5"),
    )

    private val temperatureField = UncivTextField("e.g. 0.7")
    private val maxTokensField = UncivTextField("e.g. 2048")
    private val freqPenaltyField = UncivTextField("e.g. 0.3")
    private val presencePenaltyField = UncivTextField("e.g. 0")
    private val topPField = UncivTextField("e.g. 0.95")

    /** Empty key field means "keep existing saved key". */
    private var hadSavedKey = false

    private fun refreshModelChoices(selected: String) {
        val items = modelsByProvider[providerBox.selected] ?: listOf("")
        val array = GdxArray<String>()
        items.forEach { array.add(it) }
        if (selected.isNotBlank() && selected !in items) array.add(selected) // keep a custom id
        modelBox.items = array
        modelBox.selected = selected
    }

    override fun lateInitialize() {
        val loaded = AgentSecretsStore.load()
        hadSavedKey = loaded.apiKey.isNotBlank()

        addHeader("CivAgent")
        add("Keys stay on this PC (~/.civagent/secrets.json).\nThey are never written into saves or multiplayer uploads.".toLabel())
            .colspan(2).left().padBottom(10f).row()

        providerBox.items = GdxArray<String>().also { arr -> providers.forEach { arr.add(it) } }
        providerBox.selected = loaded.provider.takeIf { it in providers } ?: "deepseek"
        add("Provider".toLabel()).left()
        add(providerBox).growX().row()
        keyField.isPasswordMode = true
        keyField.text = ""
        add("API key".toLabel()).left()
        add(keyField).growX().row()
        hintLabel.setText(
            if (hadSavedKey) "Saved key: ${AgentSecretsStore.keyHint(loaded.apiKey)} — leave blank to keep"
            else "No key saved yet"
        )
        add(hintLabel).colspan(2).left().padBottom(5f).row()

        baseUrlField.text = loaded.baseUrl
        add("Base URL".toLabel()).left()
        add(baseUrlField).growX().row()

        refreshModelChoices(loaded.model)
        providerBox.addListener(object : ChangeListener() {
            override fun changed(event: ChangeEvent?, actor: Actor?) {
                refreshModelChoices(modelBox.selected ?: "")
            }
        })
        add("Model".toLabel()).left()
        add(modelBox).growX().row()
        add("Empty = the provider's default model. This is the model the brain routes to.".toLabel(Color.GRAY))
            .colspan(2).left().padBottom(5f).row()

        streamingCheck.isChecked = loaded.streaming
        add("Streaming".toLabel()).left()
        add(streamingCheck).left().row()
        add("On: replies appear as they are written, instead of all at once.".toLabel(Color.GRAY))
            .colspan(2).left().padBottom(5f).row()

        temperatureField.text = loaded.temperature?.toString() ?: ""
        maxTokensField.text = loaded.maxTokens?.toString() ?: ""
        freqPenaltyField.text = loaded.frequencyPenalty?.toString() ?: ""
        presencePenaltyField.text = loaded.presencePenalty?.toString() ?: ""
        topPField.text = loaded.topP?.toString() ?: ""
        add("Temperature".toLabel()).left()
        add(temperatureField).growX().row()
        add("Max output tokens".toLabel()).left()
        add(maxTokensField).growX().row()
        add("Frequency penalty".toLabel()).left()
        add(freqPenaltyField).growX().row()
        add("Presence penalty".toLabel()).left()
        add(presencePenaltyField).growX().row()
        add("Top P".toLabel()).left()
        add(topPField).growX().row()
        add("Sampling: blank = the provider's default. Applies to chat and decisions.".toLabel(Color.GRAY))
            .colspan(2).left().padBottom(5f).row()

        // 总开关（持久化到 secrets.json）：关闭 = 全部席位回退原生 AI，网关/大脑/数据传输全停
        brainCheck.isChecked = AgentConfig.enabled
        brainCheck.addListener(object : ChangeListener() {
            override fun changed(event: ChangeEvent?, actor: Actor?) {
                AgentSecretsStore.save(buildSecretsForSave().copy(brainEnabled = brainCheck.isChecked)
                    .let { it })
                AgentSecretsStore.applyToRuntime(AgentSecretsStore.load())
            }
        })
        add("Brain".toLabel()).left()
        add(brainCheck).left().row()
        add("Off = all CivAgent seats fall back to the native AI; no gateway, no brain, no data transfer.".toLabel(Color.GRAY))
            .colspan(2).left().padBottom(5f).row()

        // Approval gate (session-scoped). Unchecking = the popup's "Accept all".
        // Restarts always reset to asking - this checkbox re-enables asking mid-session.
        approvalCheck.isChecked = !AgentConfig.autoAcceptIntents
        approvalCheck.addListener(object : ChangeListener() {
            override fun changed(event: ChangeEvent?, actor: Actor?) {
                AgentConfig.autoAcceptIntents = !approvalCheck.isChecked
            }
        })
        add("Approvals".toLabel()).left()
        add(approvalCheck).left().row()
        add("On: CivAgent asks before any action that targets your civ.\nOff: actions apply automatically with a notification; re-enable here anytime.".toLabel(Color.GRAY))
            .colspan(2).left().padBottom(5f).row()

        brainUrlField.text = loaded.aiServerUrl.ifBlank { AgentConfig.aiServerUrl }
        add("Brain URL".toLabel()).left()
        add(brainUrlField).growX().row()

        repoRootField.text = loaded.repoRoot.ifBlank {
            AgentBrainLauncher.resolveRepoRoot().orEmpty()
        }
        add("Repo path".toLabel()).left()
        add(repoRootField).growX().row()

        addSeparator()

        val buttons = Table(skin)
        buttons.add("Save".toTextButton().onClick { saveSecrets() }).pad(5f)
        buttons.add("Test connection".toTextButton().onClick { testConnection() }).pad(5f)
        buttons.add("Start brain".toTextButton().onClick { startBrain() }).pad(5f)
        buttons.add("Clear key".toTextButton().onClick { clearKey() }).pad(5f)
        buttons.add("View last prompt".toTextButton().onClick {
            AgentPromptViewerPopup(optionsPopup.stageToShowOn).open(force = true)
        }).pad(5f)
        add(buttons).colspan(2).row()
        add(statusLabel).colspan(2).left().padTop(8f).row()

        super.lateInitialize()
    }

    private fun buildSecretsForSave(clearKey: Boolean = false): AgentSecrets {
        val previous = AgentSecretsStore.load()
        val typed = keyField.text.trim()
        val apiKey = when {
            clearKey -> ""
            typed.isNotEmpty() -> typed
            else -> previous.apiKey
        }
        return AgentSecrets(
            provider = providerBox.selected ?: "deepseek",
            apiKey = apiKey,
            baseUrl = baseUrlField.text.trim(),
            aiServerUrl = brainUrlField.text.trim().ifEmpty { "http://127.0.0.1:2335" },
            repoRoot = repoRootField.text.trim(),
            model = modelBox.selected ?: "",
            streaming = streamingCheck.isChecked,
            brainEnabled = brainCheck.isChecked,
            temperature = temperatureField.text.trim().toDoubleOrNull(),
            maxTokens = maxTokensField.text.trim().toIntOrNull(),
            frequencyPenalty = freqPenaltyField.text.trim().toDoubleOrNull(),
            presencePenalty = presencePenaltyField.text.trim().toDoubleOrNull(),
            topP = topPField.text.trim().toDoubleOrNull(),
        )
    }

    private fun startBrain() {
        // Persist path first so the launcher can find the repo.
        val secrets = buildSecretsForSave()
        AgentSecretsStore.save(secrets)
        AgentSecretsStore.applyToRuntime(secrets)
        statusLabel.setText("Starting local brain…")
        Concurrency.run("AgentBrainStartBtn") {
            val status = AgentBrainLauncher.ensureRunning("optionsButton")
            Concurrency.runOnGLThread {
                statusLabel.setText(
                    when (status) {
                        AgentBrainLauncher.Status.AlreadyUp -> "Brain already running."
                        AgentBrainLauncher.Status.Started -> "Brain started."
                        AgentBrainLauncher.Status.Starting -> "Brain start already in progress…"
                        AgentBrainLauncher.Status.NoRepo -> "Repo not found. Set Repo path to your Civagent_repo folder."
                        AgentBrainLauncher.Status.SpawnFailed -> "Could not spawn python. Is python on PATH?"
                        AgentBrainLauncher.Status.TimedOut -> "Spawned but healthz timed out — check deployment/data/host logs."
                        AgentBrainLauncher.Status.Disabled -> "CivAgent disabled."
                    }
                )
            }
        }
    }

    private fun saveSecrets() {
        val secrets = buildSecretsForSave()
        AgentSecretsStore.save(secrets)
        AgentSecretsStore.applyToRuntime(secrets)
        hadSavedKey = secrets.apiKey.isNotBlank()
        hintLabel.setText(
            if (hadSavedKey) "Saved key: ${AgentSecretsStore.keyHint(secrets.apiKey)} — leave blank to keep"
            else "No key saved yet"
        )
        keyField.text = ""
        statusLabel.setText("Saved locally. Pushing to brain…")
        Concurrency.run("AgentSecretsPush") {
            val result = pushToBrain(secrets)
            Concurrency.runOnGLThread {
                statusLabel.setText(result)
            }
        }
    }

    private fun clearKey() {
        keyField.text = ""
        val secrets = buildSecretsForSave(clearKey = true)
        AgentSecretsStore.save(secrets)
        AgentSecretsStore.applyToRuntime(secrets)
        hadSavedKey = false
        hintLabel.setText("No key saved yet")
        statusLabel.setText("Key cleared locally.")
        Concurrency.run("AgentSecretsClear") {
            val result = pushToBrain(secrets)
            Concurrency.runOnGLThread { statusLabel.setText("Key cleared. $result") }
        }
    }

    private fun testConnection() {
        val url = brainUrlField.text.trim().ifEmpty { AgentConfig.aiServerUrl }.trimEnd('/')
        statusLabel.setText("Probing $url/healthz …")
        Concurrency.run("AgentHealthz") {
            val msg = try {
                val body = AgentHttp.getJson("$url/healthz", timeoutMs = 3_000)
                if (body.contains("\"llm\":\"ok\"")) "Brain OK — LLM configured."
                else if (body.contains("\"llm\":\"missing\"")) "Brain up, but LLM key missing (save a key)."
                else "Brain replied: ${body.take(120)}"
            } catch (ex: Exception) {
                "Brain unreachable ($url). Start Host / ai_server first. (${ex.javaClass.simpleName})"
            }
            Concurrency.runOnGLThread { statusLabel.setText(msg) }
        }
    }

    private fun pushToBrain(secrets: AgentSecrets): String {
        val base = secrets.aiServerUrl.trimEnd('/')
        val body = buildString {
            append('{')
            append("\"provider\":").append(jsonString(secrets.provider)).append(',')
            append("\"apiKey\":").append(jsonString(secrets.apiKey)).append(',')
            append("\"baseUrl\":").append(jsonString(secrets.baseUrl)).append(',')
            append("\"repoRoot\":").append(jsonString(secrets.repoRoot)).append(',')
            append("\"model\":").append(jsonString(secrets.model)).append(',')
            append("\"streaming\":").append(if (secrets.streaming) "true" else "false").append(',')
            append("\"sampling\":").append(secrets.samplingJson())
            append('}')
        }
        return try {
            val reply = AgentHttp.postJson("$base/v1/config", body, timeoutMs = 5_000)
            if (reply.contains("\"ok\":true") || reply.contains("\"ok\": true"))
                "Saved + brain reloaded (${AgentSecretsStore.keyHint(secrets.apiKey)})."
            else if (reply.contains("localhost") || reply.contains("forbidden"))
                "Saved locally; brain refused reload (localhost only)."
            else
                "Saved locally; brain reply: ${reply.take(160)}"
        } catch (ex: Exception) {
            Log.debug("Agent config push failed: ${ex.message}")
            "Saved locally; brain offline — will load ~/.civagent/secrets.json on next start."
        }
    }

    private fun jsonString(value: String): String =
        "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
}
