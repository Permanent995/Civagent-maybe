package com.unciv.logic.civagent

import com.unciv.logic.GameInfo

/**
 * Permission wall for the `agent` tier of the gateway (PLAN.md 决策 6.4).
 *
 * The gateway exposes the full developer console command tree; this whitelist is what keeps
 * the agent tier from becoming GM. Scope: `diplomacy list` (read), and `diplomacy declare-war` /
 * `make-peace` / `denounce` (acting civ must be a registered CivAgent seat, never a human seat).
 * Everything else - addstat, units, cities, tiles - is refused.
 */
object AgentWhitelist {
    data class Check(val allowed: Boolean, val reason: String? = null)

    /** Whitespace split that keeps double-quoted civ names ("Sheng Tang") as one token. */
    fun tokenize(command: String): List<String> =
        Regex("\"([^\"]*)\"|([^\\s]+)").findAll(command.trim())
            .map { it.groupValues[1].ifEmpty { it.groupValues[2] } }
            .filter { it.isNotEmpty() }
            .toList()

    fun check(command: String, agentSeats: Set<String>, gameInfo: GameInfo): Check {
        val tokens = tokenize(command)
        if (tokens.isEmpty()) return Check(false, "empty command")
        if (tokens[0] != "diplomacy")
            return Check(false, "command '${tokens[0]}' is not in the agent whitelist (W1 scope: diplomacy only)")
        if (tokens.size < 2)
            return Check(false, "diplomacy needs a subcommand")
        return when (tokens[1]) {
            "list" -> Check(true, null) // read-only
            "declare-war", "make-peace", "denounce" -> {
                if (tokens.size < 3) return Check(false, "${tokens[1]} needs an acting civ name")
                val actorID = tokens[2]
                if (actorID in agentSeats) return Check(true, null)
                val actor = gameInfo.civilizations.firstOrNull { it.civID == actorID }
                if (actor?.isHuman() == true)
                    Check(false, "refusing: '$actorID' is a human seat - an agent may never act for a human civ")
                else
                    Check(false, "refusing: '$actorID' is not a registered CivAgent seat")
            }
            else -> Check(false, "diplomacy '${tokens[1]}' is not whitelisted (only list, declare-war, make-peace, denounce)")
        }
    }
}
