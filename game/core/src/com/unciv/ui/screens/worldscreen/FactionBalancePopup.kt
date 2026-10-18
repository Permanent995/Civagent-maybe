package com.unciv.ui.screens.worldscreen

import com.badlogic.gdx.graphics.Color
import com.badlogic.gdx.scenes.scene2d.ui.Table
import com.unciv.logic.civilization.Civilization
import com.unciv.models.translations.tr
import com.unciv.ui.components.extensions.addSeparator
import com.unciv.ui.components.extensions.toLabel
import com.unciv.ui.components.extensions.toTextButton
import com.unciv.ui.components.input.onActivation
import com.unciv.ui.images.ImageGetter
import com.unciv.ui.popups.Popup
import com.unciv.ui.popups.ToastPopup
import com.unciv.ui.screens.basescreen.BaseScreen

/** EU4-style faction balance adjustment popup.
 *  Allow spending gold or culture to shift the War/Peace balance. */
class FactionBalancePopup(
    val worldScreen: WorldScreen,
    private val viewingCiv: Civilization
) : Popup(worldScreen.stage) {

    private val balance = viewingCiv.factionBalance

    init {
        val innerTable = Table()

        // Title
        innerTable.add("派系力量天平".toLabel(Color.GOLD, 32)).padBottom(15f).row()

        // Labels below bar
        val barLabels = Table()
        barLabels.defaults().pad(8f)
        barLabels.add("\uD83D\uDD4A 主和 (-100)".toLabel(Color.ROYAL, 16))
        barLabels.add("⚖ 中立 (0)".toLabel(Color.GRAY, 16))
        barLabels.add("⚔ 主战 (+100)".toLabel(Color.FIREBRICK, 16))
        innerTable.add(barLabels).padBottom(5f).row()

        // Current value indicator
        val warPeaceLabel = when {
            balance >= 50 -> "⚔ 主战倾向".toLabel(Color.FIREBRICK, 24)
            balance <= -50 -> "\uD83D\uDD4A 主和倾向".toLabel(Color.ROYAL, 24)
            else -> "⚖ 中立".toLabel(Color.GRAY, 24)
        }
        innerTable.add(warPeaceLabel).padBottom(5f).row()
        innerTable.add("当前天平: $balance".toLabel(Color.WHITE, 20)).padBottom(10f).row()
        innerTable.addSeparator()

        // Current effects
        val goldMod = viewingCiv.getFactionGoldModifier()
        val strengthMod = viewingCiv.getFactionStrengthModifier()

        if (goldMod != 0f || strengthMod != 0f) {
            innerTable.add("当前效果:".toLabel(Color.GOLD, 20)).left().padBottom(5f).row()
            if (goldMod != 0f) {
                val sign = if (goldMod > 0) "+" else ""
                val color = if (goldMod > 0) Color.GREEN else Color.FIREBRICK
                innerTable.add("  金币产出: ${sign}${(goldMod * 100).toInt()}%".toLabel(color, 18)).left().row()
            }
            if (strengthMod != 0f) {
                val sign = if (strengthMod > 0) "+" else ""
                val color = if (strengthMod > 0) Color.GREEN else Color.FIREBRICK
                innerTable.add("  军事力量: ${sign}${(strengthMod * 100).toInt()}%".toLabel(color, 18)).left().row()
            }
            innerTable.addSeparator()
        }

        // Adjustment buttons
        innerTable.add("倾斜天平:".toLabel(Color.GOLD, 22)).left().padBottom(10f).padTop(5f).row()

        val buttonTable = Table()
        buttonTable.defaults().pad(8f)

        // War buttons
        val war10Btn = "⚔ +10".toTextButton()
        war10Btn.onActivation { adjustBalanceBy(10) }
        buttonTable.add(war10Btn)

        val war20Btn = "⚔ +20".toTextButton()
        war20Btn.onActivation { adjustBalanceBy(20) }
        buttonTable.add(war20Btn)

        // Peace buttons
        val peace10Btn = "\uD83D\uDD4A -10".toTextButton()
        peace10Btn.onActivation { adjustBalanceBy(-10) }
        buttonTable.add(peace10Btn)

        val peace20Btn = "\uD83D\uDD4A -20".toTextButton()
        peace20Btn.onActivation { adjustBalanceBy(-20) }
        buttonTable.add(peace20Btn)

        innerTable.add(buttonTable).padBottom(10f).row()
        innerTable.addSeparator()

        // Cost info
        innerTable.add("消耗:".toLabel(Color.GRAY, 16)).left().row()
        innerTable.add("  ±10 = ${Civilization.FACTION_COST_GOLD}金币 或 ${Civilization.FACTION_COST_CULTURE}文化".toLabel(Color.GRAY, 14)).left().row()
        innerTable.add("  ±20 = 翻倍".toLabel(Color.GRAY, 14)).left().row()

        innerTable.pack()
        add(innerTable)
        row()

        // Close button
        val closeButton = "关闭".toTextButton()
        closeButton.onActivation { close() }
        add(closeButton)
    }

    private fun adjustBalanceBy(delta: Int) {
        val multiplier = if (delta == 10 || delta == -10) 1 else 2
        val costGold = Civilization.FACTION_COST_GOLD * multiplier
        val costCulture = Civilization.FACTION_COST_CULTURE * multiplier

        // Try gold first, then culture
        if (viewingCiv.hasGold(costGold)) {
            viewingCiv.spendGold(costGold)
            viewingCiv.adjustFactionBalance(delta)
        } else if (viewingCiv.policies.storedCulture >= costCulture) {
            viewingCiv.policies.storedCulture -= costCulture
            viewingCiv.adjustFactionBalance(delta)
        } else {
            ToastPopup(
                "资源不足！需要 ${costGold}金币 或 ${costCulture}文化".tr(),
                worldScreen.stage
            ).open()
            return
        }

        worldScreen.shouldUpdate = true
        close()
    }
}
