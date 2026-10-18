package com.unciv.ui.screens.worldscreen

import com.badlogic.gdx.scenes.scene2d.ui.Table
import com.unciv.models.translations.tr
import com.unciv.ui.components.extensions.toLabel
import com.unciv.ui.components.extensions.toTextButton
import com.unciv.ui.components.input.onClick
import com.unciv.ui.popups.Popup
import java.util.concurrent.atomic.AtomicBoolean

enum class ConfirmResult { REJECT, ACCEPT, ACCEPT_ALL }

/**
 * W4.1, refined by user feedback: the human only ever confirms batches that concern THEM
 * (intents targeting the player's civ). AI-vs-AI diplomacy applies automatically with a
 * notification instead. Closing without a button = rejection (seat plays the native turn).
 * "Accept all" also stops asking for the rest of the session.
 */
internal class AgentIntentConfirmPopup(
    screen: com.unciv.ui.screens.basescreen.BaseScreen,
    message: String,
    onResult: (ConfirmResult) -> Unit
) : Popup(screen) {

    private val resolved = AtomicBoolean(false)

    init {
        add("CivAgent wants to act".tr().toLabel(fontSize = 24)).growX().row()
        add("The CivAgent seat proposes the following to YOU this turn.\nReject and the seat plays a full native turn instead."
            .tr().toLabel()).left().pad(10f).row()
        val body = message.toLabel()
        body.wrap = true
        // stageToShowOn, not stage: Actor.getStage() is null until open() adds this popup.
        add(body).left().pad(10f).maxWidth(stageToShowOn.width * 0.7f).row()

        val buttons = Table(skin)
        val acceptAll = "Accept all".tr().toTextButton()
        val confirm = "Confirm".tr().toTextButton()
        val reject = "Reject".tr().toTextButton()
        acceptAll.onClick { if (resolved.compareAndSet(false, true)) onResult(ConfirmResult.ACCEPT_ALL); close() }
        confirm.onClick { if (resolved.compareAndSet(false, true)) onResult(ConfirmResult.ACCEPT); close() }
        reject.onClick { if (resolved.compareAndSet(false, true)) onResult(ConfirmResult.REJECT); close() }
        buttons.add(acceptAll).pad(8f)
        buttons.add(confirm).pad(8f)
        buttons.add(reject).pad(8f)
        add(buttons).center().row()

        closeListeners.add { if (resolved.compareAndSet(false, true)) onResult(ConfirmResult.REJECT) }
        open()
    }
}
