package com.unciv.logic.civagent

import com.unciv.logic.GameInfo
import com.unciv.logic.civilization.Civilization
import com.unciv.logic.city.City
import com.unciv.logic.map.mapunit.MapUnit
import com.unciv.logic.map.tile.Tile
import com.unciv.ui.screens.devconsole.CliInput
import com.unciv.ui.screens.devconsole.ConsoleContext
import com.unciv.ui.screens.devconsole.ConsoleErrorException

/**
 * UI-free [ConsoleContext] for the CivAgent gateway: reuses the exact command tree of the
 * in-game developer console, but has no screen selection. Commands that would need a
 * selection (tile / city / unit context) are refused with an actionable error.
 */
internal class HeadlessConsole(override val gameInfo: GameInfo) : ConsoleContext {
    override fun getCivByNameOrNull(name: CliInput): Civilization? =
        gameInfo.civilizations.firstOrNull { name.equals(it.civID) }

    override fun getSelectedCiv(): Civilization =
        throw ConsoleErrorException("Headless gateway has no selected civ - pass an explicit civ name")

    override fun getSelectedTile(): Tile =
        throw ConsoleErrorException("Headless gateway has no tile selection - not supported in W1")

    override fun getSelectedCity(): City =
        throw ConsoleErrorException("Headless gateway has no tile selection - not supported in W1")

    override fun getSelectedUnit(): MapUnit =
        throw ConsoleErrorException("Headless gateway has no tile selection - not supported in W1")

    override fun getCity(cityName: CliInput): City =
        gameInfo.getCities().firstOrNull { cityName.equals(it.name) }
            ?: throw ConsoleErrorException("Unknown city: $cityName")
}
