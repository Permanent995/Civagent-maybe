package com.unciv.ui.screens.devconsole

import com.badlogic.gdx.graphics.Color
import com.unciv.logic.GameInfo
import com.unciv.logic.civilization.Civilization
import com.unciv.logic.civilization.Notification
import com.unciv.logic.city.City
import com.unciv.logic.map.mapunit.MapUnit
import com.unciv.logic.map.tile.Tile

/**
 * UI-free access surface for the console command tree.
 *
 * Implemented by [DevConsolePopup] for the in-game console, and by the headless
 * CivAgent gateway (`com.unciv.logic.civagent.HeadlessConsole`) so external tooling
 * can run the exact same commands without a WorldScreen.
 */
internal interface ConsoleContext {
    val gameInfo: GameInfo

    fun getCivByNameOrNull(name: CliInput): Civilization?

    /** The civ whose screen the console is open on. Headless contexts have no selection. */
    fun getSelectedCiv(): Civilization

    fun getSelectedTile(): Tile
    fun getSelectedCity(): City
    fun getSelectedUnit(): MapUnit
    fun getCity(cityName: CliInput): City

    fun getCivByName(name: CliInput): Civilization =
        getCivByNameOrNull(name)
        ?: throw ConsoleErrorException("Unknown civ: $name")

    fun getCivByNameOrSelected(name: CliInput?): Civilization =
        name?.let { getCivByName(it) } ?: getSelectedCiv()

    /** UI: mark the world screen (un)changed. Headless: no-op. */
    fun requestUpdate(update: Boolean = true) {}

    /** UI: ask the human to confirm before running [action]. Headless: refuses. */
    fun confirmAction(message: String, buttonText: String = "Yes", action: () -> Unit) {
        throw ConsoleErrorException("Confirmation requires a UI - command not executed")
    }

    /** UI: echo a message into the console popup. Headless: no-op. */
    fun showResponse(message: String?, color: Color) {}

    /** UI: put a corrected command into the input field (deprecation hints). Headless: no-op. */
    fun suggestCommand(text: String) {}

    /** UI: show the command history. Headless: no-op. */
    fun showHistory() {}

    /** UI: surface a one-time notification (e.g. `tile find`). Headless: no-op. */
    fun showOneTimeNotification(notification: Notification) {}
}
