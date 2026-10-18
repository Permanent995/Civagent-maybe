package com.unciv.logic.civagent

import com.unciv.UncivGame
import com.unciv.logic.civilization.Civilization
import com.unciv.ui.popups.ToastPopup
import com.unciv.utils.Concurrency
import com.unciv.utils.Log

/**
 * Tells the player when a *confirmed* CivAgent batch only partly applied.
 *
 * The confirmation popup appears before execution, so without this a refused intent (e.g. a peace
 * the engine would not accept) looked exactly like a success - "it said peace was confirmed, but
 * nothing happened".
 */
object AgentIntentReporter {
    fun reportFailures(civ: Civilization, failures: List<AppliedEntry>) {
        if (failures.isEmpty()) return
        val detail = failures.joinToString("; ") { entry ->
            "${entry.type} -> ${entry.targetCivID ?: "?"}: ${entry.error ?: "?"}"
        }
        Log.debug("CivAgent refused ${failures.size} intent(s) for ${civ.civID}: $detail")
        try {
            // Plain text on purpose: tr() strips [placeholders] when untranslated, which
            // produced garbage like "civNamecivName有 count 项" on screen.
            val message = "CivAgent: ${failures.size} action(s) rejected - $detail"
            Concurrency.runOnGLThread {
                val worldScreen = UncivGame.Current.worldScreen ?: return@runOnGLThread
                ToastPopup(message, worldScreen)
            }
        } catch (_: Exception) {
            // Never let reporting break a turn.
        }
    }
}
