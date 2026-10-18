package com.unciv.ui.screens.overviewscreen

import com.badlogic.gdx.graphics.Color
import com.badlogic.gdx.scenes.scene2d.ui.Table
import com.unciv.Constants
import com.unciv.logic.civilization.Civilization
import com.unciv.logic.city.City
import com.unciv.ui.components.extensions.addSeparator
import com.unciv.ui.components.extensions.addSeparatorVertical
import com.unciv.ui.components.extensions.getCloseButton
import com.unciv.ui.components.extensions.toLabel
import com.unciv.ui.images.ImageGetter
import com.unciv.ui.screens.basescreen.BaseScreen
import com.unciv.ui.screens.basescreen.RecreateOnResize

/** 国内派系力量平衡 - 各城市主战/主和倾向 */
class FactionBalanceScreen(
    private val viewingPlayer: Civilization
) : BaseScreen(), RecreateOnResize {

    private val warFactionCities = mutableListOf<Pair<City, Int>>()
    private val peaceFactionCities = mutableListOf<Pair<City, Int>>()
    private var warTotal = 0
    private var peaceTotal = 0

    init {
        calculateFactions()

        val mainTable = Table(skin)
        mainTable.defaults().pad(10f)

        // Header - title bar with close button
        val headerTable = Table()
        headerTable.add("派系力量平衡".toLabel(Color.GOLD, 36)).padBottom(15f)
        headerTable.add().growX()
        headerTable.add(getCloseButton { game.popScreen() })
        mainTable.add(headerTable).colspan(3).fillX().row()

        mainTable.addSeparator()
        mainTable.addSeparator()

        // Faction summary bar (visual balance)
        val total = warTotal + peaceTotal
        if (total > 0) {
            val warPercent = (warTotal * 100f / total).toInt()
            val peacePercent = 100 - warPercent

            val summaryTable = Table()
            summaryTable.defaults().pad(5f)

            // War faction
            summaryTable.add(ImageGetter.getImage("OtherIcons/Fire").apply {
                setSize(28f, 28f)
            })
            summaryTable.add("主战派".toLabel(Color.FIREBRICK, 28))
            summaryTable.add("  $warTotal 力量 (${warPercent}%)".toLabel(Color.GRAY, 22))
            summaryTable.add().width(10f)

            // Bar
            val barTable = Table()
            val barHeight = 24f
            val barWidth = 300f
            val warWidth = barWidth * warPercent / 100f
            val peaceWidth = barWidth - warWidth

            val warBar = Table()
            warBar.background = skinStrings.getUiBackground(
                "FactionBalanceScreen/WarBar",
                BaseScreen.skinStrings.roundedEdgeRectangleShape,
                tintColor = Color.FIREBRICK
            )
            summaryTable.add(warBar).size(warWidth, barHeight).padRight(0f)

            val peaceBar = Table()
            peaceBar.background = skinStrings.getUiBackground(
                "FactionBalanceScreen/PeaceBar",
                BaseScreen.skinStrings.roundedEdgeRectangleShape,
                tintColor = Color.ROYAL
            )
            summaryTable.add(peaceBar).size(peaceWidth, barHeight).padLeft(0f)

            summaryTable.add().width(10f)
            summaryTable.add("${peacePercent}%".toLabel(Color.GRAY, 22))
            summaryTable.add("  主和派  ".toLabel(Color.ROYAL, 28))
            summaryTable.add(ImageGetter.getImage("OtherIcons/Policies").apply {
                setSize(28f, 28f)
            })

            mainTable.add(summaryTable).colspan(3).center().padBottom(20f).row()
            mainTable.addSeparator()
        }

        // Column headers
        val headerFaction = "派系".toLabel(Color.GOLD, 22)
        val headerCity = "城市".toLabel(Color.GOLD, 22)
        val headerContribution = "力量贡献".toLabel(Color.GOLD, 22)
        mainTable.add(headerFaction).padRight(20f)
        mainTable.add(headerCity).padRight(20f).growX()
        mainTable.add(headerContribution).padLeft(20f).row()
        mainTable.addSeparator()

        // All cities sorted by contribution
        val allCities = (warFactionCities + peaceFactionCities).sortedByDescending { it.second }
        for ((city, contribution) in allCities) {
            val isWar = warFactionCities.any { it.first == city }
            val factionLabel = if (isWar) "⚔ 主战".toLabel(Color.FIREBRICK, 20)
            else "🕊 主和".toLabel(Color.ROYAL, 20)

            mainTable.add(factionLabel).padRight(20f).left()
            mainTable.add(city.name.toLabel(Color.WHITE, 22)).padRight(20f).growX()
            mainTable.add("$contribution".toLabel(Color.GOLD, 22)).padLeft(20f).right()
            mainTable.row()
        }

        if (allCities.isEmpty()) {
            mainTable.add("暂无城市数据".toLabel(Color.GRAY, 22)).colspan(3).center().pad(30f).row()
        }

        mainTable.addSeparator()
        mainTable.addSeparator()

        // Total row
        mainTable.add("总计".toLabel(Color.GOLD, 24)).padTop(5f)
        mainTable.add()
        mainTable.add("$warTotal / $peaceTotal".toLabel(Color.WHITE, 24)).padTop(5f)
        mainTable.row()

        mainTable.pack()
        mainTable.setPosition(
            (stage.width - mainTable.width) / 2f,
            (stage.height - mainTable.height) / 2f
        )
        stage.addActor(mainTable)
    }

    internal fun calculateFactions() {
        for (city in viewingPlayer.cities) {
            val (faction, score) = determineFactionAndScore(city, viewingPlayer)
            when (faction) {
                Faction.WAR -> {
                    warFactionCities.add(city to score)
                    warTotal += score
                }
                Faction.PEACE -> {
                    peaceFactionCities.add(city to score)
                    peaceTotal += score
                }
            }
        }
    }

    companion object {
        /** Determine which faction a city belongs to and the contribution score */
        fun determineFactionAndScore(city: City, viewingPlayer: Civilization): Pair<Faction, Int> {
        var warScore = 0
        var peaceScore = 0

        // Base: population contributes to tendency
        peaceScore += city.population.population

        // Military buildings → war faction
        val militaryBuildings = setOf("Barracks", "Armory", "Military Academy",
            "Walls", "Castle", "Arsenal", "Military Base")
        for (building in militaryBuildings) {
            if (city.cityConstructions.containsBuildingOrEquivalent(building)) {
                warScore += 5
            }
        }

        // Culture/science/wonder buildings → peace faction
        val peaceBuildings = setOf("Library", "University", "Public School",
            "Monument", "Temple", "Amphitheater", "Opera House", "Museum",
            "Market", "Bank", "Stock Exchange", "Workshop", "Factory")
        for (building in peaceBuildings) {
            if (city.cityConstructions.containsBuildingOrEquivalent(building)) {
                peaceScore += 4
            }
        }

        // Border city (adjacent to other civ) → war faction
        val isBorderCity = city.getCenterTile().neighbors.any { neighbor ->
            neighbor.getOwner() != null && neighbor.getOwner() != viewingPlayer
        }
        if (isBorderCity) warScore += 8

        // Wonders → peace (cultural achievement)
        if (city.cityConstructions.getBuiltBuildings().any { it.isAnyWonder() }) peaceScore += 6

        // Capital has unique weight
        if (city.isCapital()) {
            warScore += 3
            peaceScore += 3  // Capital balanced default
        }

        return if (warScore > peaceScore) Faction.WAR to (warScore - peaceScore)
        else Faction.PEACE to (peaceScore - warScore)
    }
    }  // companion object

    override fun recreate(): BaseScreen {
        return FactionBalanceScreen(viewingPlayer)
    }

    enum class Faction { WAR, PEACE }
}
