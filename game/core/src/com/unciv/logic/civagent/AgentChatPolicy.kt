package com.unciv.logic.civagent

import com.unciv.logic.GameInfo
import com.unciv.logic.civilization.Civilization

/**
 * Chat visibility rule (PLAN.md 决策 5.4 边界): before you have MET a civ you cannot talk
 * to it - and you must not even see that it exists. A seat is chatable only when it is
 * registered as a CivAgent seat AND the viewing civ has actually met it (a diplomacy
 * entry exists), and it is still alive.
 */
object AgentChatPolicy {
    fun chatabaleSeats(gameInfo: GameInfo, registeredSeats: Collection<String>, viewingCiv: Civilization): List<String> {
        val metIDs = viewingCiv.getKnownCivs().map { it.civID }.toSet()
        return registeredSeats
            .filter { seat -> seat != viewingCiv.civID && seat in metIDs }
            .filter { seat -> gameInfo.civilizations.any { it.civID == seat && !it.isDefeated() } }
            .sorted()
    }

    /** May messages from [otherCivID] be shown to [viewingCiv]? (event filtering) */
    fun canChatWith(gameInfo: GameInfo, registeredSeats: Collection<String>, viewingCiv: Civilization, otherCivID: String): Boolean =
        otherCivID != viewingCiv.civID &&
            otherCivID in registeredSeats &&
            viewingCiv.getKnownCivs().any { it.civID == otherCivID } &&
            gameInfo.civilizations.any { it.civID == otherCivID && !it.isDefeated() }
}
