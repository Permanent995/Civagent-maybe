package com.unciv.ui.screens.worldscreen

import com.badlogic.gdx.graphics.Color
import com.badlogic.gdx.scenes.scene2d.Stage
import com.badlogic.gdx.scenes.scene2d.ui.Table
import com.unciv.logic.civagent.AgentChatClient
import com.unciv.logic.civagent.LastPrompt
import com.unciv.ui.components.extensions.toLabel
import com.unciv.ui.components.extensions.toTextButton
import com.unciv.ui.components.input.onClick
import com.unciv.ui.popups.Popup
import com.unciv.utils.Concurrency

/**
 * Built-in prompt viewer: shows the exact text the brain sent to the model, plus the token
 * estimate and the sampling settings that were applied. Read-only.
 */
internal class AgentPromptViewerPopup(stage: Stage) : Popup(stage, scrollable = Scrollability.All) {

    init {
        add("Last prompt".toLabel(fontSize = 22)).growX().row()
        val body = "Loading…".toLabel(Color.LIGHT_GRAY)
        body.setWrap(true)
        add(body).left().pad(10f).growX().row()

        val buttons = Table(skin)
        val refresh = "Refresh".toTextButton()
        val close = "Close".toTextButton()
        close.onClick { close() }
        buttons.add(refresh).pad(6f)
        buttons.add(close).pad(6f)
        add(buttons).center().row()
        addCloseButton()

        fun load() {
            Concurrency.run("AgentPromptView") {
                val prompt = AgentChatClient.fetchLastPrompt()
                Concurrency.runOnGLThread {
                    body.setText(render(prompt))
                    pack()
                }
            }
        }
        refresh.onClick { body.setText("Loading…"); load() }
        open(force = true)
        load()
    }

    private fun render(prompt: LastPrompt?): String {
        if (prompt == null) {
            return "No prompt yet.\nThe brain records one after the first chat message or decision."
        }
        val sampling = if (prompt.sampling.isEmpty()) "(provider defaults)"
        else prompt.sampling.entries.joinToString(", ") { "${it.key}=${it.value}" }
        return buildString {
            appendLine("kind: ${prompt.kind}   time: ${prompt.time ?: "?"}")
            appendLine("seat: ${prompt.civID}   game: ${prompt.gameId}")
            appendLine("provider/model: ${prompt.provider} / ${prompt.model}")
            appendLine("streaming: ${prompt.streaming}")
            appendLine("sampling: $sampling")
            appendLine("tokens (estimate): system=${prompt.systemTokens} user=${prompt.userTokens} total=${prompt.promptTokens}")
            appendLine()
            appendLine("----- SYSTEM -----")
            appendLine(prompt.system)
            appendLine()
            appendLine("----- USER -----")
            appendLine(prompt.user)
        }
    }
}
