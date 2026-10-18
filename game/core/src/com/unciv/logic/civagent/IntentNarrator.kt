package com.unciv.logic.civagent

import com.unciv.logic.civilization.Civilization
import com.unciv.models.translations.fillPlaceholders
import com.unciv.models.translations.tr

/**
 * Renders agent intents as a plain-language list (W4.1) - what the confirmation popup shows.
 * Never raw JSON, and every line goes through `.tr()` so the player reads it in the game language.
 */
object IntentNarrator {
    fun describe(civ: Civilization, intents: List<BrainIntent>): String =
        intents.joinToString("\n") { "• " + describeOne(civ, it) }

    fun describeOne(civ: Civilization, intent: BrainIntent): String {
        val target = intent.targetCivID ?: "?"
        // Unciv idiom: values go INSIDE the square brackets first, then one .tr() both
        // localizes and substitutes. fillPlaceholders-after-tr strips the brackets when
        // no translation entry exists, so it must never be used on the template first.
        return when (intent.type) {
            "declare_war" -> "⚔ [${civ.civID}] declares war on [$target]!".tr()
            "make_peace", "seek_peace" -> "☮ [${civ.civID}] makes peace with [$target].".tr()
            "denounce" -> "[${civ.civID}] publicly denounces [$target]!".tr()
            "propose_trade", "buy_luxury" -> {
                val trade = intent.trade
                val ours = trade?.ourOffers?.joinToString(", ") { offerText(it) }?.takeIf { it.isNotEmpty() }
                    ?: "nothing".tr()
                val theirs = trade?.theirOffers?.joinToString(", ") { offerText(it) }?.takeIf { it.isNotEmpty() }
                    ?: "nothing".tr()
                "⚖ [${civ.civID}] offers [$ours] for [$theirs] (to [$target])".tr()
            }
            "open_border", "research_agreement", "mutual_defense", "form_ally" ->
                "🤝 [${civ.civID}] proposes [${agreementLabel(intent.type)}] to [$target]".tr()
            "share_intel" -> {
                val claim = intent.intel?.claim?.take(60) ?: "(empty claim)".tr()
                "📜 [${civ.civID}] shares intel with [$target]: [$claim]".tr()
            }
            "chat" -> {
                // Chat prose is flavor - truncate so the CONFIRM POPUP shows the actions,
                // not a wall of foreign-language text (#1).
                val spoken = intent.chatText?.let { if (it.length > 220) it.take(220) + "…" else it }
                "💬 [${civ.civID}] says: [${spoken ?: "(silence)".tr()}]".tr()
            }
            else -> "${intent.type}" + (intent.targetCivID?.let { " -> $it" } ?: "")
        }
    }

    /** Player-facing name of an agreement type (kept localizable). */
    private fun agreementLabel(type: String): String = when (type) {
        "open_border" -> "Open Borders".tr()
        "research_agreement" -> "Research Agreement".tr()
        "mutual_defense" -> "Defensive Pact".tr()
        else -> "Declaration of Friendship".tr()
    }

    private fun offerText(offer: BrainTradeOffer): String {
        val name = (offer.ref ?: offer.type).tr()
        return if (offer.amount > 0) "${offer.amount.toInt()} [$name]".tr() else name
    }

    /** Sort key for W4.4 order: trades first, then war/peace, then everything else. */
    fun order(intent: BrainIntent): Int = when (intent.type) {
        "propose_trade", "buy_luxury", "open_border", "research_agreement", "mutual_defense", "form_ally" -> 0
        "declare_war", "make_peace", "seek_peace", "denounce" -> 1
        else -> 2
    }
}
