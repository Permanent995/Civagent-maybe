package com.unciv.logic.civagent

import com.unciv.logic.GameInfo
import com.unciv.models.ruleset.Building
import com.unciv.models.ruleset.IRulesetObject
import com.unciv.models.ruleset.Ruleset
import com.unciv.models.ruleset.tech.Technology
import com.unciv.models.ruleset.tile.TileResource
import com.unciv.models.ruleset.unit.BaseUnit
import kotlinx.serialization.Serializable
import java.security.MessageDigest

/**
 * M-content-0: projects the *loaded* ruleset into compact, LLM-consumable "content cards".
 *
 * Mods are arbitrary and change between games, so the brain never learns their content - the
 * game describes it on demand. Everything here is derived from engine fields/predicates plus the
 * engine's own Civilopedia renderer; unknown content degrades to text, never crashes.
 *
 * Direction matters: `GameInfo.ruleset` already *is* merged Unciv JSON (base + mods + `replaces`),
 * so this is a read model. We never write ruleset JSON back.
 */
@Serializable
data class ContentCard(
    val kind: String,
    val id: String,
    val name: String,
    /** Capability tags derived from engine predicates, e.g. military / water / city-founder. */
    val caps: List<String> = emptyList(),
    val era: String? = null,
    val stats: Map<String, Int> = emptyMap(),
    val requires: Map<String, String> = emptyMap(),
    val prerequisites: List<String> = emptyList(),
    val replaces: String? = null,
    val uniqueTo: String? = null,
    val uniques: List<String> = emptyList(),
    /** Engine-rendered natural language (Civilopedia lines), in the game's language. */
    val desc: String = ""
)

@Serializable
data class ContentLookup(
    val found: Boolean,
    val kind: String,
    val id: String,
    val card: ContentCard? = null,
    val error: String? = null
)

/** Ruleset identity for the slice: lets the brain pick a strategy profile without shipping the tree. */
@Serializable
data class RulesetRef(
    val name: String,
    val isBaseRuleset: Boolean,
    val mods: List<String>,
    val fingerprint: String
)

/** M-content-1: what the active ruleset adds/removes relative to an unmodded baseline. */
@Serializable
data class RulesetDelta(
    val baseline: String,
    val added: Map<String, List<String>> = emptyMap(),
    val removed: Map<String, List<String>> = emptyMap()
)

object ContentCards {
    val KINDS = listOf(
        "unit", "building", "resource", "tech", "improvement", "promotion", "nation", "policy", "era"
    )

    fun rulesetRef(gameInfo: GameInfo): RulesetRef {
        val ruleset = gameInfo.ruleset
        val mods = gameInfo.gameParameters.mods.toList()
        return RulesetRef(
            name = ruleset.name,
            isBaseRuleset = ruleset.modOptions.isBaseRuleset,
            mods = mods,
            fingerprint = fingerprint(ruleset.name, mods, ruleset.modOptions.isBaseRuleset)
        )
    }

    /** Stable short id for a ruleset identity - key for strategy profiles / cached digests. */
    fun fingerprint(name: String, mods: List<String>, isBaseRuleset: Boolean): String {
        val raw = "$name|$isBaseRuleset|${mods.sorted().joinToString(",")}"
        val bytes = MessageDigest.getInstance("SHA-256").digest(raw.toByteArray(Charsets.UTF_8))
        return bytes.take(8).joinToString("") { "%02x".format(it) }
    }

    /** All ids of one kind, for delta computation (M-content-1). */
    fun listIds(ruleset: Ruleset, kind: String): List<String> = when (kind.lowercase()) {
        "unit" -> ruleset.units.keys.toList()
        "building" -> ruleset.buildings.keys.toList()
        "resource" -> ruleset.tileResources.keys.toList()
        "tech", "technology" -> ruleset.technologies.keys.toList()
        "improvement" -> ruleset.tileImprovements.keys.toList()
        "promotion" -> ruleset.unitPromotions.keys.toList()
        "nation" -> ruleset.nations.keys.toList()
        "policy" -> ruleset.policies.keys.toList()
        "era" -> ruleset.eras.keys.toList()
        else -> emptyList()
    }.sorted()

    /** name → luxury/strategic/bonus, straight from the ruleset (used by trade decisions). */
    fun resourceKinds(ruleset: Ruleset, names: Collection<String>): Map<String, String> =
        names.mapNotNull { name -> ruleset.tileResources[name]?.let { name to it.resourceType.name.lowercase() } }.toMap()

    /** What the active ruleset adds/removes relative to an unmodded baseline. */
    fun delta(current: Ruleset, baseline: Ruleset): RulesetDelta {
        val added = LinkedHashMap<String, List<String>>()
        val removed = LinkedHashMap<String, List<String>>()
        for (kind in KINDS) {
            val cur = listIds(current, kind).toSet()
            val base = listIds(baseline, kind).toSet()
            (cur - base).sorted().takeIf { it.isNotEmpty() }?.let { added[kind] = it }
            (base - cur).sorted().takeIf { it.isNotEmpty() }?.let { removed[kind] = it }
        }
        return RulesetDelta(baseline.name, added, removed)
    }

    fun lookup(ruleset: Ruleset, kind: String, id: String): ContentLookup {
        val card = project(ruleset, kind, id)
        return if (card == null) ContentLookup(false, kind, id, error = "unknown $kind '$id'")
        else ContentLookup(true, kind, id, card = card)
    }

    fun project(ruleset: Ruleset, kind: String, id: String): ContentCard? = when (kind.lowercase()) {
        "unit" -> ruleset.units[id]?.let { unitCard(it, ruleset) }
        "building" -> ruleset.buildings[id]?.let { buildingCard(it, ruleset) }
        "resource" -> ruleset.tileResources[id]?.let { resourceCard(it, ruleset) }
        "tech", "technology" -> ruleset.technologies[id]?.let { techCard(it, ruleset) }
        "improvement" -> ruleset.tileImprovements[id]?.let {
            baseCard("improvement", it, ruleset).copy(
                requires = it.techRequired?.let { tech -> mapOf("tech" to tech) } ?: emptyMap(),
                replaces = it.replaces, uniqueTo = it.uniqueTo
            )
        }
        "promotion" -> ruleset.unitPromotions[id]?.let { baseCard("promotion", it, ruleset) }
        "nation" -> ruleset.nations[id]?.let { baseCard("nation", it, ruleset) }
        "policy" -> ruleset.policies[id]?.let { baseCard("policy", it, ruleset) }
        "era" -> ruleset.eras[id]?.let { baseCard("era", it, ruleset).copy(era = it.name) }
        else -> null
    }

    private fun baseCard(kind: String, obj: IRulesetObject, ruleset: Ruleset): ContentCard {
        val desc = obj.getCivilopediaTextLines(ruleset).joinToString("\n") { it.text }.trim()
        return ContentCard(
            kind = kind,
            id = obj.name,
            name = obj.name,
            uniques = obj.uniqueObjects.map { it.getDisplayText() },
            desc = desc
        )
    }

    private fun eraOfTech(ruleset: Ruleset, tech: String?): String? =
        tech?.let { ruleset.technologies[it]?.column?.era }

    private fun unitCard(unit: BaseUnit, ruleset: Ruleset): ContentCard {
        val caps = buildList {
            add(if (unit.isMilitary) "military" else "civilian")
            if (unit.isRanged()) add("ranged")
            if (unit.isWaterUnit) add("water")
            if (unit.isCityFounder()) add("city-founder")
            if (unit.unitType.isNotBlank()) add("type:${unit.unitType}")
        }
        val requires = buildMap {
            unit.requiredTech?.let { put("tech", it) }
            unit.requiredResource?.let { put("resource", it) }
        }
        return baseCard("unit", unit, ruleset).copy(
            caps = caps,
            era = eraOfTech(ruleset, unit.requiredTech),
            stats = mapOf(
                "strength" to unit.strength,
                "rangedStrength" to unit.rangedStrength,
                "movement" to unit.movement,
                "cost" to unit.cost
            ),
            requires = requires,
            replaces = unit.replaces,
            uniqueTo = unit.uniqueTo
        )
    }

    private fun buildingCard(building: Building, ruleset: Ruleset): ContentCard {
        val caps = listOf(
            when {
                building.isWonder -> "wonder"
                building.isNationalWonder -> "national-wonder"
                else -> "building"
            }
        )
        val requires = buildMap {
            building.requiredTech?.let { put("tech", it) }
            building.requiredBuilding?.let { put("building", it) }
            building.requiredResource?.let { put("resource", it) }
        }
        return baseCard("building", building, ruleset).copy(
            caps = caps,
            era = eraOfTech(ruleset, building.requiredTech),
            stats = mapOf("cost" to building.cost),
            requires = requires,
            replaces = building.replaces,
            uniqueTo = building.uniqueTo
        )
    }

    private fun resourceCard(resource: TileResource, ruleset: Ruleset): ContentCard {
        val requires = buildMap {
            resource.improvement?.let { put("improvement", it) }
            resource.revealedBy?.let { put("revealedBy", it) }
        }
        return baseCard("resource", resource, ruleset).copy(
            caps = listOf("kind:${resource.resourceType.name.lowercase()}"),
            requires = requires
        )
    }

    private fun techCard(tech: Technology, ruleset: Ruleset): ContentCard =
        baseCard("tech", tech, ruleset).copy(
            era = tech.column?.era,
            stats = mapOf("cost" to tech.cost),
            prerequisites = tech.prerequisites.sorted()
        )
}
