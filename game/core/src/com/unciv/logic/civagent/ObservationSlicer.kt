package com.unciv.logic.civagent

import com.unciv.logic.GameInfo
import com.unciv.logic.civilization.Civilization
import com.unciv.logic.civilization.Proximity
import com.unciv.logic.civilization.diplomacy.DiplomaticModifiers
import com.unciv.models.ruleset.nation.PersonalityValue
import com.unciv.logic.trade.TradeOffer
import com.unciv.ui.screens.overviewscreen.FactionBalanceScreen
import kotlinx.serialization.Serializable

/**
 * Observation slice: what the game sends to the Python brain instead of a raw save.
 * Fog-of-war trimmed to one civ's own view.
 *
 * v2 (+W5 map/border summaries): stock+delta pairs, city rows, RC faction balance,
 * known-civ stats, era, proximity/continent/reachability, navy count, border pressure.
 * No unit coordinates (决策 18/19).
 */
@Serializable
data class ObservationSlice(
    val schemaVersion: Int = 2,
    val gameId: String,
    val civID: String,
    val turn: Int,
    val gold: Int,
    val goldPerTurn: Float,
    val happiness: Int,
    val resources: Map<String, Int>,
    /** M-content-1: resource name → luxury/strategic/bonus, from the ruleset. */
    val resourceKinds: Map<String, String> = emptyMap(),
    val knownCivs: List<KnownCivSummary>,
    val personality: String? = null,
    val science: Map<String, Int> = emptyMap(),
    val sciencePerTurn: Float = 0f,
    val cultureStored: Int = 0,
    val culturePerTurn: Float = 0f,
    val cities: List<CityRow> = emptyList(),
    val factionBalance: FactionBalanceInfo? = null,
    val era: String = "",
    val eraNumber: Int = 0,
    /** Total eras defined by the active ruleset. Lets the brain normalize eraNumber
     *  to a 0..8 tier for anachronism guards on mods with a different era count. */
    val eraCount: Int = 0,
    /** True water military units owned by the acting civ (not a fake navy copy). */
    val navyShipCount: Int = 0,
    /** Aggregated visible border pressure - never per-unit coordinates. */
    val borderThreats: List<BorderThreat> = emptyList(),
    /** Incoming trade offers awaiting our answer (civ.tradeRequests). */
    val incomingTradeRequests: List<String> = emptyList(),
    /** New engine notifications since the previous decision (P-memory event stream). */
    val newEvents: List<EngineEvent> = emptyList(),
    /** The player's game language - the brain locks decision chatText to it (#1). */
    val language: String = "English",
    /** What this agent did recently (kept by CivAgent: Unciv drops notifications for AI seats). */
    val recentAgentActions: List<String> = emptyList(),
    /** Nearest explored city of a civ we are at war with, or null. */
    val nearestHostileCity: String? = null,
    /** M-content-0: ruleset identity (name / mods / fingerprint) so the brain can tell vanilla from modded. */
    val ruleset: RulesetRef? = null
)

@Serializable
data class KnownCivSummary(
    val civID: String,
    val relationship: String,
    val atWar: Boolean,
    val gold: Int? = null,
    val population: Int? = null,
    val cities: Int? = null,
    val technologies: Int? = null,
    val force: Int? = null,
    val score: Double? = null,
    val rank: Int? = null,
    val vsSelf: String? = null,
    val era: String? = null,
    /** Their visible stock, so peace/trade terms can reference what they actually hold. */
    val resources: Map<String, Int> = emptyMap(),
    val proximity: String? = null,
    val sameContinent: Boolean? = null,
    /** Same continent + Neighbors/Close — coarse land-threat proxy, not full pathfinding. */
    val landReachable: Boolean? = null,
    val borderThreat: String? = null,
    /** Standing deals with them, engine-rendered: "we give [Gold_Per_Turn x5] / they give [Research_Agreement (25 turns left)]". */
    val agreements: List<String> = emptyList(),
    val hasOpenBorders: Boolean = false,
    /** Accumulated relation/trade statistics (DiplomaticModifiers), biggest first. */
    val modifiers: List<String> = emptyList()
)

@Serializable
data class BorderThreat(
    val civID: String,
    /** none | low | medium | high */
    val level: String,
    /** Bucketed visible enemy military near our cities: 0 / 1-2 / 3-6 / 7+ */
    val visibleEnemyBucket: String
)

@Serializable
data class EngineEvent(
    val turn: Int,
    /** Engine notification category: War / Trade / Diplomacy / General / ... */
    val category: String,
    /** Untranslated engine notification text - the brain's language lock handles the player language. */
    val text: String
)

@Serializable
data class CityRow(
    val name: String,
    val population: Int,
    val foodStored: Int,
    val foodPerTurn: Float,
    val productionPerTurn: Float,
    val constructionProgress: Int,
    val currentConstruction: String,
    /** Engine-authoritative gold price to buy the current construction NOW (混合分层的动态层). */
    val constructionBuyCost: Int? = null,
    /** Engine-authoritative turns until the current construction completes. */
    val constructionTurnsLeft: Int? = null
)

@Serializable
data class FactionBalanceInfo(
    val warTotal: Int,
    val peaceTotal: Int,
    val cities: List<CityFaction>
)

@Serializable
data class CityFaction(val name: String, val faction: String, val score: Int)

object ObservationSlicer {
    private const val BORDER_SCAN_DISTANCE = 4

    /**
     * P4 性格锁: the nation's personality coefficients rendered as a short text line
     * (e.g. "declarewar 8, loyal 3"). Only dimensions off the neutral 5 are listed so
     * the prompt stays small; a neutral/default personality renders as "neutral".
     */
    fun renderPersonality(civ: Civilization): String {
        val personality = civ.getPersonality()
        if (personality.isNeutralPersonality) return "neutral"
        val deviations = PersonalityValue.entries
            .filter { personality.scaledFocus(it) != 1f }
            .joinToString(", ") { "${it.name.lowercase()} ${(personality.scaledFocus(it) * 5).toInt()}" }
        return deviations.ifBlank { "neutral" }
    }

    fun slice(gameInfo: GameInfo, civ: Civilization): ObservationSlice {
        val hideOthers = gameInfo.gameParameters.hideOtherCivilizationStats
        val myScore = civ.calculateTotalScore()
        val borderByCiv = borderThreatMap(civ)
        val knownCivs = civ.getKnownCivs()
            .filter { !it.isDefeated() }
            .map { other -> knownCivSummary(gameInfo, civ, myScore, other, hideOthers, borderByCiv[other.civID]) }
            .sortedBy { it.civID }
            .toList()
        val resources = civ.getCivResourcesByName()
        return ObservationSlice(
            gameId = gameInfo.gameId,
            civID = civ.civID,
            turn = gameInfo.turns,
            gold = civ.gold,
            goldPerTurn = civ.stats.statsForNextTurn.gold,
            happiness = civ.getHappiness(),
            resources = resources,
            resourceKinds = ContentCards.resourceKinds(gameInfo.ruleset, resources.keys),
            knownCivs = knownCivs,
            personality = renderPersonality(civ),
            science = civ.tech.techsInProgress,
            sciencePerTurn = civ.stats.statsForNextTurn.science,
            cultureStored = civ.policies.storedCulture,
            culturePerTurn = civ.stats.statsForNextTurn.culture,
            cities = civ.cities.map { cityRow(it) },
            factionBalance = if (gameInfo.isFactionBalanceEnabled()) factionBalance(civ) else null,
            era = civ.getEra().name,
            eraNumber = civ.getEraNumber(),
            eraCount = gameInfo.ruleset.eras.size,
            navyShipCount = navyShipCount(civ),
            borderThreats = borderByCiv.map { (id, threat) ->
                BorderThreat(id, threat.first, threat.second)
            }.sortedBy { it.civID },
            nearestHostileCity = nearestHostileCity(civ),
            incomingTradeRequests = incomingTradeRequests(civ),
            recentAgentActions = AgentMod.recentAgentActions(gameInfo.gameId, civ.civID),
            ruleset = ContentCards.rulesetRef(gameInfo)
        )
    }

    private fun knownCivSummary(
        gameInfo: GameInfo,
        civ: Civilization,
        myScore: Double,
        other: Civilization,
        hideOthers: Boolean,
        border: Pair<String, String>?
    ): KnownCivSummary {
        val diplo = civ.getDiplomacyManager(other)
        val proximity = civ.getProximity(other)
        val sameContinent = sameContinent(civ, other)
        val landReachable = sameContinent && proximity in setOf(Proximity.Neighbors, Proximity.Close)
        val base = KnownCivSummary(
            civID = other.civID,
            relationship = diplo?.relationshipLevel()?.name ?: "unknown",
            atWar = diplo != null && civ.isAtWarWith(other),
            proximity = proximity.name,
            sameContinent = sameContinent,
            landReachable = landReachable,
            borderThreat = border?.first
        )
        if (hideOthers) return base
        val score = other.calculateTotalScore()
        return base.copy(
            gold = other.gold,
            population = other.cities.sumOf { it.population.population },
            cities = other.cities.size,
            technologies = other.tech.techsResearched.size,
            force = militaryMight(other),
            score = score,
            rank = rankByScore(gameInfo, other),
            vsSelf = ratioVsSelf(score, myScore),
            era = other.getEra().name,
            resources = other.getCivResourcesByName(),
            agreements = agreementSummaries(civ, other),
            hasOpenBorders = civ.getDiplomacyManager(other)?.hasOpenBorders == true,
            modifiers = modifierSummaries(civ, other)
        )
    }

    // ---- W5+: the engine's own view of inter-civ trade/deal state (决策 22: still summaries, no
    // coordinates). Note: civ.notifications is unusable here - Civilization.addNotification returns
    // early for PlayerType.AI, so an agent seat has no notification feed at all.

    private fun offerText(offer: TradeOffer): String {
        val amount = if (offer.amount > 1) " x${offer.amount}" else ""
        val left = if (offer.duration > 0) " (${offer.duration} turns left)" else ""
        return "${offer.name}$amount$left"
    }

    /** Deals already signed with [other] and still running. */
    private fun agreementSummaries(civ: Civilization, other: Civilization): List<String> =
        civ.getDiplomacyManager(other)?.trades.orEmpty().map { trade ->
            val ours = trade.ourOffers.joinToString(", ") { offerText(it) }
            val theirs = trade.theirOffers.joinToString(", ") { offerText(it) }
            "we give [$ours] / they give [$theirs]"
        }

    /** Accumulated trade/relation statistics (DiplomaticModifiers), biggest magnitude first. */
    private fun modifierSummaries(civ: Civilization, other: Civilization, limit: Int = 6): List<String> =
        civ.getDiplomacyManager(other)?.diplomaticModifiers.orEmpty().entries
            .sortedByDescending { kotlin.math.abs(it.value) }
            .take(limit)
            .map { (name, value) ->
                val text = runCatching { DiplomaticModifiers.valueOf(name).text }.getOrNull() ?: name
                "$text (${value.toInt()})"
            }

    /** Trade offers other civs have sent us and not yet answered. */
    private fun incomingTradeRequests(civ: Civilization, limit: Int = 5): List<String> =
        civ.tradeRequests.takeLast(limit).map { request ->
            // TradeRequest: theirOffers = what they give us, ourOffers = what they want back.
            val gives = request.trade.theirOffers.joinToString(", ") { offerText(it) }
            val wants = request.trade.ourOffers.joinToString(", ") { offerText(it) }
            "from ${request.requestingCiv}: gives [$gives] / wants [$wants]"
        }

    private fun sameContinent(a: Civilization, b: Civilization): Boolean {
        val capitalA = a.getCapital()?.getCenterTile() ?: return false
        val capitalB = b.getCapital()?.getCenterTile() ?: return false
        return capitalA.getContinent() == capitalB.getContinent()
    }

    private fun navyShipCount(civ: Civilization): Int =
        civ.units.getCivUnits().count { it.isMilitary() && it.baseUnit.isWaterUnit }

    /**
     * Count visible enemy military near our cities. Returns civID -> (level, bucket).
     * Coordinates never leave this function.
     */
    private fun borderThreatMap(civ: Civilization): Map<String, Pair<String, String>> {
        val counts = HashMap<String, Int>()
        // forEachTileInDistance, not getTilesInDistance: the latter is deprecated because this is
        // O(cities x tiles) per seat-turn and the sequence version allocates.
        for (city in civ.cities) {
            city.getCenterTile().forEachTileInDistance(BORDER_SCAN_DISTANCE) { tile ->
                if (!tile.isVisible(civ)) return@forEachTileInDistance
                for (unit in tile.getUnits()) {
                    if (!unit.isMilitary() || unit.civ == civ || unit.civ.isDefeated()) return@forEachTileInDistance
                    if (!civ.knows(unit.civ)) return@forEachTileInDistance
                    counts[unit.civ.civID] = (counts[unit.civ.civID] ?: 0) + 1
                }
            }
        }
        return counts.mapValues { (_, n) ->
            val level = when {
                n <= 0 -> "none"
                n <= 2 -> "low"
                n <= 6 -> "medium"
                else -> "high"
            }
            val bucket = when {
                n <= 0 -> "0"
                n <= 2 -> "1-2"
                n <= 6 -> "3-6"
                else -> "7+"
            }
            level to bucket
        }
    }

    private fun nearestHostileCity(civ: Civilization): String? {
        val ourCapital = civ.getCapital()?.getCenterTile() ?: return null
        var bestName: String? = null
        var bestDist = Int.MAX_VALUE
        for (enemy in civ.getKnownCivs()) {
            if (!civ.isAtWarWith(enemy) || enemy.isDefeated()) continue
            for (city in enemy.cities) {
                val tile = city.getCenterTile()
                if (!tile.isExplored(civ)) continue
                val dist = tile.aerialDistanceTo(ourCapital)
                if (dist < bestDist) {
                    bestDist = dist
                    bestName = city.name
                }
            }
        }
        return bestName
    }

    private fun militaryMight(civ: Civilization): Int =
        civ.units.getCivUnits().sumOf { maxOf(it.baseUnit.strength, it.baseUnit.rangedStrength) }

    private fun rankByScore(gameInfo: GameInfo, civ: Civilization): Int {
        val ranked = gameInfo.civilizations
            .filter { it.isMajorCiv() && !it.isDefeated() }
            .sortedByDescending { it.calculateTotalScore() }
        return ranked.indexOfFirst { it.civID == civ.civID } + 1
    }

    private fun ratioVsSelf(theirs: Double, mine: Double): String =
        if (mine <= 0.0) "n/a" else "%.1fx".format(theirs / mine)

    private fun cityRow(city: com.unciv.logic.city.City): CityRow {
        val construction = city.cityConstructions.currentConstructionName()
        val buyCost = (city.cityConstructions.getCurrentConstruction()
            as? com.unciv.models.ruleset.INonPerpetualConstruction)
            ?.getStatBuyCost(city, com.unciv.models.stats.Stat.Gold)
        val turnsLeft = if (construction.isEmpty()) null
        else city.cityConstructions.turnsToConstruction(construction)
        return CityRow(
            name = city.name,
            population = city.population.population,
            foodStored = city.population.foodStored,
            foodPerTurn = city.cityStats.currentCityStats.food,
            productionPerTurn = city.cityStats.currentCityStats.production,
            constructionProgress = if (construction.isEmpty()) 0 else city.cityConstructions.getWorkDone(construction),
            currentConstruction = construction,
            constructionBuyCost = buyCost,
            constructionTurnsLeft = turnsLeft
        )
    }

    private fun factionBalance(civ: Civilization): FactionBalanceInfo {
        var warTotal = 0
        var peaceTotal = 0
        val cities = mutableListOf<CityFaction>()
        for (city in civ.cities) {
            val (faction, score) = FactionBalanceScreen.determineFactionAndScore(city, civ)
            cities.add(CityFaction(city.name, faction.name, score))
            when (faction) {
                FactionBalanceScreen.Faction.WAR -> warTotal += score
                FactionBalanceScreen.Faction.PEACE -> peaceTotal += score
            }
        }
        return FactionBalanceInfo(warTotal = warTotal, peaceTotal = peaceTotal, cities = cities)
    }
}
