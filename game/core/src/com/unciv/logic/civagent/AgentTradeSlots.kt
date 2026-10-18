package com.unciv.logic.civagent

import com.unciv.logic.civilization.Civilization
import com.unciv.logic.trade.Trade

/** One outbound trade slot per civ pair (W4.3): a new confirmed offer retracts the old one. */
object AgentTradeSlots {
    private data class SlotEntry(val fromCiv: String, val toCiv: String, val trade: Trade)

    private val slots = HashMap<String, SlotEntry>()

    // order-insensitive: the slot belongs to the PAIR, whoever initiated
    private fun key(gameId: String, a: String, b: String) =
        "$gameId/" + listOf(a.lowercase(), b.lowercase()).sorted().joinToString("|")

    fun get(gameId: String, fromCiv: String, toCiv: String): Trade? = slots[key(gameId, fromCiv, toCiv)]?.trade

    fun put(gameId: String, fromCiv: String, toCiv: String, trade: Trade) {
        slots[key(gameId, fromCiv, toCiv)] = SlotEntry(fromCiv, toCiv, trade)
    }

    /**
     * P5: sync the slot table with engine truth. The engine's [com.unciv.logic.trade.TradeRequest]
     * disappears when the human accepts, declines, or lets the offer expire - a slot whose
     * request no longer exists is stale and must be cleared so the next confirmed offer
     * starts fresh instead of silently retracting nothing.
     */
    fun reconcile(gameId: String, civ: Civilization) {
        val iterator = slots.entries.iterator()
        while (iterator.hasNext()) {
            val slot = iterator.next().value
            if (!slot.fromCiv.equals(civ.civID, ignoreCase = true)) continue
            val target = civ.gameInfo.civilizations.firstOrNull { it.civID.equals(slot.toCiv, ignoreCase = true) }
            val stillPending = target != null &&
                target.tradeRequests.any { it.requestingCiv.equals(civ.civID, ignoreCase = true) }
            if (!stillPending) iterator.remove()
        }
    }
}
