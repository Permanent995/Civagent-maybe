package com.unciv.ui.screens.worldscreen

import com.badlogic.gdx.Gdx
import com.badlogic.gdx.Input
import com.badlogic.gdx.graphics.Color
import com.badlogic.gdx.math.Vector2
import com.badlogic.gdx.scenes.scene2d.Actor
import com.badlogic.gdx.scenes.scene2d.InputEvent
import com.badlogic.gdx.scenes.scene2d.Touchable
import com.badlogic.gdx.scenes.scene2d.ui.Cell
import com.badlogic.gdx.scenes.scene2d.ui.Image
import com.badlogic.gdx.scenes.scene2d.ui.Label
import com.badlogic.gdx.scenes.scene2d.ui.ScrollPane
import com.badlogic.gdx.scenes.scene2d.ui.SelectBox
import com.badlogic.gdx.scenes.scene2d.ui.Table
import com.badlogic.gdx.scenes.scene2d.utils.DragListener
import com.badlogic.gdx.utils.Array as GdxArray
import com.unciv.logic.civagent.AgentChatClient
import com.unciv.logic.civagent.AgentChatPolicy
import com.unciv.logic.civagent.AgentBrainLauncher
import com.unciv.logic.civagent.AgentConfig
import com.unciv.logic.civagent.AgentMod
import com.unciv.logic.civagent.ChatEventEntry
import com.unciv.logic.civagent.ChatMessageEntry
import com.unciv.logic.civilization.Civilization
import com.unciv.logic.files.FileChooser
import com.unciv.models.translations.tr
import com.unciv.ui.components.extensions.addSeparator
import com.unciv.ui.components.extensions.getCloseButton
import com.unciv.ui.components.extensions.setSize
import com.unciv.ui.components.extensions.toLabel
import com.unciv.ui.components.extensions.toTextButton
import com.unciv.ui.components.input.keyShortcuts
import com.unciv.ui.components.input.onClick
import com.unciv.ui.components.widgets.UncivTextField
import com.unciv.ui.images.ImageGetter
import com.unciv.ui.popups.Popup
import com.unciv.ui.popups.ToastPopup
import com.unciv.utils.Concurrency

/**
 * 领袖对话 (W3): talk to CivAgent seats without touching the board.
 * Nation portraits, drag title to move, corner handle to resize; strings go through .tr().
 */
internal class LeaderDialoguePopup(
    val worldScreen: WorldScreen
) : Popup(worldScreen.stage, Scrollability.None, maxSizePercentage = 0.92f) {

    private val gameInfo = worldScreen.gameInfo
    private val myCiv = worldScreen.selectedCiv
    private val language = worldScreen.game.settings.language

    @Volatile private var closed = false
    @Volatile private var brainOffline = false

    private val seats: List<String> = try {
        val registered = AgentMod.ensureStarted(gameInfo)?.getAgentSeats().orEmpty()
        AgentChatPolicy.chatabaleSeats(gameInfo, registered, myCiv)
    } catch (_: Exception) {
        emptyList()
    }

    private var selectedSeat: String = seats.firstOrNull().orEmpty()
    private val seatButtons = HashMap<String, Table>()
    private val greetingLabel = "".toLabel()
    private val statusLabel = "".toLabel()
    private val headerNameLabel = "".toLabel(fontSize = 22)
    private val messageTable = Table(skin)
    private val messageScroll = ScrollPane(messageTable, skin).apply {
        setScrollingDisabled(true, false)
        setOverscroll(false, false)
    }
    /** The cell that owns the transcript size - resizing the widget alone is undone by pack(). */
    private lateinit var messageCell: Cell<ScrollPane>
    private val inputField = UncivTextField("Type a message…")
    private lateinit var attachmentLabel: Label
    private var pendingAttachmentPath: String? = null
    private var pendingAttachmentName: String? = null
    private val lines = HashMap<String, MutableList<ChatMessageEntry>>()
    /** Streaming seat → text so far; rendered as one live bubble, replaced as tokens arrive. */
    private val liveStreaming = HashMap<String, String>()
    private val lastEventSeq = HashMap<String, Int>()

    private var panelWidth = (stageToShowOn.width * 0.55f).coerceIn(420f, 900f)
    private var panelHeight = (stageToShowOn.height * 0.62f).coerceIn(360f, 720f)

    init {
        clickBehindToClose = true

        val titleBar = Table(skin)
        titleBar.add("Leader Dialogue".toLabel(fontSize = 24)).pad(8f).expandX().left()
        titleBar.add(getCloseButton { close() }).pad(4f)
        titleBar.touchable = Touchable.enabled
        addDragHandle(titleBar)
        add(titleBar).growX().row()

        if (seats.isEmpty()) {
            add(
                "No one to talk to yet.\nEither no CivAgent seats are filled (set them up at game start),\nor you have not met any CivAgent civ so far - explore and meet them first."
                    .toLabel()
            ).pad(20f).row()
        } else {
            val seatRow = Table(skin)
            for (seat in seats) {
                val civ = gameInfo.civilizations.firstOrNull { it.civID == seat } ?: continue
                val btn = seatButton(civ)
                seatButtons[seat] = btn
                seatRow.add(btn).pad(4f)
            }
            add(ScrollPane(seatRow, skin).apply { setScrollingDisabled(false, true) })
                .growX().pad(6f).row()

            // One portrait is enough: the seat row above already shows the nation icon.
            val header = Table(skin)
            header.add(headerNameLabel).pad(6f).left().expandX()
            add(header).growX().row()
            add(greetingLabel).left().padLeft(10f).padRight(10f).row()
            add(statusLabel).left().padLeft(10f).row()

            messageCell = add(messageScroll).size(panelWidth - 40f, panelHeight - 220f).pad(8f)
            row()

            attachmentLabel = "".toLabel(Color.SKY)
            attachmentLabel.isVisible = false
            add(attachmentLabel).left().padLeft(10f).row()

            val inputWrapper = Table(skin)
            val plusBtn = ImageGetter.getImage("OtherIcons/New").apply { setSize(28f) }
            plusBtn.onClick { openPlusMenu() }
            inputWrapper.add(plusBtn).pad(5f)
            inputWrapper.add(inputField).growX().pad(5f)
            val sendBtn = "Send".toLabel()
            sendBtn.onClick { sendMessage() }
            inputWrapper.add(sendBtn).pad(5f)
            // Enter sends, like the dev console - the Send button was the only way before.
            inputField.keyShortcuts.add(Input.Keys.ENTER, ::sendMessage)
            add(inputWrapper).growX().pad(8f).row()

            val resizeHint = "Drag the title to move · drag the corner to resize".toLabel(Color.GRAY)
            val bottom = Table(skin)
            bottom.add(resizeHint).expandX().left().pad(4f)
            val grip = ImageGetter.getImage("OtherIcons/DownArrow").apply {
                color = Color.LIGHT_GRAY
                setSize(22f)
                setOrigin(11f, 11f)
                rotation = 45f
            }
            addResizeHandle(grip)
            bottom.add(grip).pad(6f).right()
            add(bottom).growX().row()

            selectSeat(selectedSeat)

            Concurrency.run("AgentChatProbe") {
                val ok = AgentChatClient.isReachable()
                Concurrency.runOnGLThread { setBrainOffline(!ok) }
            }
            // Hydrate every seat's transcript, then subscribe from the newest seq. Starting at
            // after=0 used to replay all past replies: duplicate bubbles plus re-queued drafts.
            Concurrency.run("AgentChatHydrate") {
                var after = 0
                for (seat in seats) {
                    val history = AgentChatClient.fetchMessages(gameInfo.gameId, myCiv.civID, seat)
                    for (entry in history) if (entry.seq > after) after = entry.seq
                    if (history.isEmpty()) continue
                    Concurrency.runOnGLThread {
                        lines[seat] = history.toMutableList()
                        if (seat == selectedSeat) rebuildMessages()
                    }
                }
                Concurrency.run("AgentChatListener") {
                    AgentChatClient.listenEvents(
                        gameInfo.gameId, myCiv.civID, after = after,
                        onEvent = { event -> Concurrency.runOnGLThread { onChatEvent(event) } },
                        shouldContinue = { !closed }
                    )
                }
            }
        }
        open(force = true)
        applyPanelSize()
    }

    /** The person you are talking to (李世民), not the state (盛唐). */
    private fun leaderNameOf(civ: Civilization): String =
        civ.nation.leaderName.takeIf { it.isNotBlank() }?.tr() ?: civ.civName.tr()

    private fun seatButton(civ: Civilization): Table {
        val table = Table(skin)
        val portrait = ImageGetter.getNationPortrait(civ.nation, 48f)
        table.add(portrait).pad(2f).row()
        table.add(civ.civName.toLabel(fontSize = 14)).padBottom(2f)
        table.pad(4f)
        table.touchable = Touchable.enabled
        table.onClick { selectSeat(civ.civID) }
        highlightSeatButton(table, civ.civID == selectedSeat)
        return table
    }

    private fun highlightSeatButton(table: Table, selected: Boolean) {
        table.color = if (selected) Color.GOLD else Color.WHITE
    }

    private fun selectSeat(seat: String) {
        if (seat.isEmpty()) return
        selectedSeat = seat
        for ((id, btn) in seatButtons) highlightSeatButton(btn, id == seat)
        val civ = gameInfo.civilizations.firstOrNull { it.civID == seat } ?: return
        headerNameLabel.setText(leaderNameOf(civ))
        greetingLabel.setText(
            (civ.nation.neutralHello.takeIf { it.isNotBlank() } ?: "(The leader awaits your words.)").tr()
        )
        rebuildMessages() // history is hydrated for every seat when the popup opens
    }

    private fun addDragHandle(actor: Actor) {
        actor.addListener(object : DragListener() {
            private val last = Vector2()
            override fun dragStart(event: InputEvent?, x: Float, y: Float, pointer: Int) {
                last.set(x, y)
                actor.localToStageCoordinates(last)
            }
            override fun drag(event: InputEvent?, x: Float, y: Float, pointer: Int) {
                val stagePos = Vector2(x, y)
                actor.localToStageCoordinates(stagePos)
                val dx = stagePos.x - last.x
                val dy = stagePos.y - last.y
                last.set(stagePos)
                innerTable.x = (innerTable.x + dx).coerceIn(0f, stageToShowOn.width - innerTable.width)
                innerTable.y = (innerTable.y + dy).coerceIn(0f, stageToShowOn.height - innerTable.height)
            }
        })
    }

    private fun addResizeHandle(grip: Image) {
        grip.touchable = Touchable.enabled
        grip.addListener(object : DragListener() {
            override fun drag(event: InputEvent?, x: Float, y: Float, pointer: Int) {
                // x/y are local to the grip; the panel's top-left stays put while it grows.
                val stagePos = grip.localToStageCoordinates(Vector2(x, y))
                panelWidth = (stagePos.x - innerTable.x).coerceIn(360f, stageToShowOn.width * 0.92f)
                panelHeight = (innerTable.y + innerTable.height - stagePos.y)
                    .coerceIn(300f, stageToShowOn.height * 0.92f)
                applyPanelSize()
            }

            override fun dragStop(event: InputEvent?, x: Float, y: Float, pointer: Int) {
                rebuildMessages() // re-wrap the transcript at the final width
            }
        })
    }

    private fun applyPanelSize() {
        if (!::messageCell.isInitialized) return
        // The cell, not the widget: Table.pack() would otherwise snap the ScrollPane back.
        messageCell.size(panelWidth - 40f, (panelHeight - 240f).coerceAtLeast(140f))
        innerTable.invalidateHierarchy()
        innerTable.pack()
        // Keep the panel fully on screen after the resize.
        innerTable.x = innerTable.x.coerceIn(0f, (stageToShowOn.width - innerTable.width).coerceAtLeast(0f))
        innerTable.y = innerTable.y.coerceIn(0f, (stageToShowOn.height - innerTable.height).coerceAtLeast(0f))
    }

    /** Usable text width inside the transcript: panel minus padding, avatar and scrollbar. */
    private fun messageWrapWidth(): Float = (panelWidth - 110f).coerceAtLeast(140f)

    private fun setBrainOffline(offline: Boolean) {
        if (offline == brainOffline) return
        brainOffline = offline
        statusLabel.setText(
            if (offline)
                "CivAgent brain unreachable - AI seats won't reply.".tr() +
                    "\n(${AgentConfig.aiServerUrl})"
            else ""
        )
    }

    private fun openPlusMenu() {
        val menu = Popup(stageToShowOn)
        menu.add("Attachments".toLabel(fontSize = 20)).pad(8f).row()
        menu.add("Share intelligence".toTextButton().onClick {
            menu.close()
            openShareIntelForm()
        }).pad(6f).row()
        menu.add("Upload image".toTextButton().onClick {
            menu.close()
            pickImageAttachment()
        }).pad(6f).row()
        // The brain runs from source, so code/config changes need a restart - handy from here.
        menu.add("Restart brain".toTextButton().onClick {
            menu.close()
            statusLabel.setText("Restarting the brain\u2026")
            AgentBrainLauncher.restart { status ->
                Concurrency.runOnGLThread { statusLabel.setText(AgentBrainLauncher.statusText(status)) }
            }
        }).pad(6f).row()
        // See exactly what the model was sent (system + user + sampling + token estimate).
        menu.add("View last prompt".toTextButton().onClick {
            menu.close()
            AgentPromptViewerPopup(worldScreen.stage).open(force = true)
        }).pad(6f).row()
        if (pendingAttachmentPath != null) {
            menu.add("Clear attachment".toTextButton().onClick {
                clearAttachment()
                menu.close()
            }).pad(6f).row()
        }
        menu.addCloseButton()
        menu.open(force = true)
    }

    private fun openShareIntelForm() {
        val seat = selectedSeat
        if (seat.isEmpty()) return
        val known = myCiv.getKnownCivs()
            .filter { it.isMajorCiv() && !it.isDefeated() && it.civID != myCiv.civID }
            .sortedBy { it.civName }

        val form = Popup(stageToShowOn)
        form.add("Share intelligence".toLabel(fontSize = 22)).pad(8f).row()
        form.add("Must include a text claim. Does not reveal the map.".toLabel(Color.GRAY)).pad(4f).row()

        val aboutBox = SelectBox<String>(skin)
        val aboutIds = GdxArray<String>()
        val nameBox = SelectBox<String>(skin)
        val names = GdxArray<String>()
        for (civ in known) {
            aboutIds.add(civ.civID)
            names.add(civ.civName.tr())
        }
        nameBox.items = names
        aboutBox.items = aboutIds
        if (aboutIds.size > 0) {
            aboutBox.selected = aboutIds.first()
            nameBox.selected = names.first()
        }
        nameBox.addListener(object : com.badlogic.gdx.scenes.scene2d.utils.ChangeListener() {
            override fun changed(event: ChangeEvent?, actor: Actor?) {
                val idx = nameBox.selectedIndex
                if (idx in 0 until aboutIds.size) aboutBox.selected = aboutIds[idx]
            }
        })
        form.add("About civilization".toLabel()).left().pad(4f)
        form.add(nameBox).growX().pad(4f).row()

        val claimField = UncivTextField("e.g. Rome is massing troops on our northern border")
        form.add("Claim".toLabel()).left().pad(4f)
        form.add(claimField).growX().minWidth(360f).pad(4f).row()

        if (pendingAttachmentName != null) {
            form.add(("Attached: " + pendingAttachmentName!!).toLabel(Color.SKY)).pad(4f).row()
        }

        form.add("Send intelligence".toTextButton().onClick {
            val claim = claimField.text.trim()
            val about = aboutBox.selected
            if (claim.isEmpty()) {
                ToastPopup("A text claim is required for intelligence exchange.", worldScreen)
                return@onClick
            }
            if (about.isNullOrEmpty()) {
                ToastPopup("Pick which civilization the claim is about.", worldScreen)
                return@onClick
            }
            form.close()
            sendShareIntel(about, claim)
        }).pad(8f).row()
        form.addCloseButton()
        form.open(force = true)
    }

    private fun pickImageAttachment() {
        val dialog = FileChooser.createLoadDialog(
            stageToShowOn,
            "Choose an image",
            Gdx.files.absolute(System.getProperty("user.home"))
        ) { success, file ->
            if (!success || file == null || !file.exists()) return@createLoadDialog
            Concurrency.run("AgentUpload") {
                val bytes = try { file.readBytes() } catch (_: Exception) { null }
                if (bytes == null) {
                    Concurrency.runOnGLThread {
                        ToastPopup("Could not read the image file.", worldScreen)
                    }
                    return@run
                }
                if (bytes.size > 4_000_000) {
                    Concurrency.runOnGLThread {
                        ToastPopup("Image too large (max 4 MB).", worldScreen)
                    }
                    return@run
                }
                val path = AgentChatClient.uploadFile(file.name(), bytes)
                Concurrency.runOnGLThread {
                    if (path == null) {
                        ToastPopup("Upload failed — is the CivAgent brain running?", worldScreen)
                    } else {
                        pendingAttachmentPath = path
                        pendingAttachmentName = file.name()
                        refreshAttachmentLabel()
                        ToastPopup("Image attached. Send a message or share intelligence.", worldScreen)
                    }
                }
            }
        }
        dialog.filter = FileChooser.createExtensionFilter("png", "jpg", "jpeg", "webp")
        dialog.open(force = true)
    }

    private fun clearAttachment() {
        pendingAttachmentPath = null
        pendingAttachmentName = null
        refreshAttachmentLabel()
    }

    private fun refreshAttachmentLabel() {
        if (!::attachmentLabel.isInitialized) return
        val name = pendingAttachmentName
        if (name.isNullOrBlank()) {
            attachmentLabel.setText("")
            attachmentLabel.isVisible = false
        } else {
            attachmentLabel.setText("Attached:".tr() + " $name (" + "tap + to clear".tr() + ")")
            attachmentLabel.isVisible = true
        }
    }

    private fun sendShareIntel(aboutCivId: String, claim: String) {
        val seat = selectedSeat
        if (seat.isEmpty()) return
        val aboutName = gameInfo.civilizations.firstOrNull { it.civID == aboutCivId }?.civName?.tr() ?: aboutCivId
        val display = "【${"Share intelligence".tr()} · $aboutName】$claim" +
            (pendingAttachmentName?.let { " 📎 $it" } ?: "")
        val attach = pendingAttachmentPath
        clearAttachment()
        appendLine(seat, ChatMessageEntry(from = myCiv.civID, to = seat, text = display))
        val seatCiv = gameInfo.getCivilization(seat)
        Concurrency.run("AgentShareIntel") {
            val ok = AgentChatClient.postMessage(
                gameInfo.gameId, myCiv.civID, seat, display, language,
                seatCiv.getEra().name, seatCiv.getEraNumber(), seatCiv.gameInfo.ruleset.eras.size,
                shareIntelClaim = claim,
                shareIntelAboutCiv = aboutCivId,
                attachmentPath = attach,
                turn = gameInfo.turns,
            )
            Concurrency.runOnGLThread { if (!ok) setBrainOffline(true) }
        }
    }

    private fun sendMessage() {
        val seat = selectedSeat
        val text = inputField.text.trim()
        if (seat.isEmpty() || text.isEmpty()) return
        inputField.setText("")
        val attach = pendingAttachmentPath
        val attachName = pendingAttachmentName
        val display = text + (attachName?.let { " 📎 $it" } ?: "")
        clearAttachment()
        appendLine(seat, ChatMessageEntry(from = myCiv.civID, to = seat, text = display))
        val seatCiv = gameInfo.getCivilization(seat)
        Concurrency.run("AgentChatSend") {
            val ok = AgentChatClient.postMessage(
                gameInfo.gameId, myCiv.civID, seat, display, language,
                seatCiv.getEra().name, seatCiv.getEraNumber(), seatCiv.gameInfo.ruleset.eras.size,
                attachmentPath = attach,
                turn = gameInfo.turns,
            )
            Concurrency.runOnGLThread { if (!ok) setBrainOffline(true) }
        }
    }

    private fun onChatEvent(event: ChatEventEntry) {
        if (event.toCiv != myCiv.civID) return
        val registered = try {
            AgentMod.ensureStarted(gameInfo)?.getAgentSeats().orEmpty()
        } catch (_: Exception) {
            emptyList()
        }
        if (!AgentChatPolicy.canChatWith(gameInfo, registered, myCiv, event.fromCiv)) return
        lastEventSeq[event.fromCiv] = event.seq
        setBrainOffline(false)
        if (event.kind == "partial") {
            liveStreaming[event.fromCiv] = event.text
            if (event.fromCiv == selectedSeat) rebuildMessages()
            return
        }
        liveStreaming.remove(event.fromCiv) // the finished reply replaces the live bubble
        if (event.kind == "error") {
            appendLine(event.fromCiv, ChatMessageEntry(from = event.fromCiv, to = event.toCiv, text = event.text))
            return
        }
        for (intent in event.intents) AgentMod.queueDraftIntent(gameInfo.gameId, event.fromCiv, intent)
        appendLine(event.fromCiv, ChatMessageEntry(from = event.fromCiv, to = event.toCiv, text = event.text, draftIntents = event.intents))
    }

    private fun appendLine(seat: String, entry: ChatMessageEntry) {
        lines.getOrPut(seat) { mutableListOf() }.add(entry)
        if (seat == selectedSeat) rebuildMessages()
    }

    private fun rebuildMessages() {
        val seat = selectedSeat
        messageTable.clear()
        for (entry in lines[seat].orEmpty()) {
            val fromCiv = gameInfo.civilizations.firstOrNull { it.civID == entry.from }
            val row = Table(skin)
            val speaker = if (fromCiv != null) "${leaderNameOf(fromCiv)}: " else "${entry.from}: "
            val head = Table(skin)
            if (fromCiv != null) head.add(ImageGetter.getNationPortrait(fromCiv.nation, 28f)).padRight(6f)
            head.add(speaker.toLabel()).left()
            row.add(head).left().growX().row()
            // Wrapped to the panel width: unwrapped labels used to be clipped / spill outside the UI.
            val body = entry.text.toLabel().apply { setWrap(true) }
            row.add(body).left().growX().width(messageWrapWidth()).padTop(2f).row()
            messageTable.add(row).left().growX().pad(3f).padBottom(4f).row()
            for (intent in entry.draftIntents) {
                val draft = "Draft (next turn):".tr() + " ${intent.type}" +
                    (intent.targetCivID?.let { " → ${it.tr()}" } ?: "")
                messageTable.add(draft.toLabel(Color.GOLD)).left().padLeft(36f).pad(1f).row()
            }
            if (entry.from == myCiv.civID) messageTable.addSeparator()
        }
        // Streaming: one live bubble for the selected seat while the reply is still being written.
        val live = liveStreaming[seat]
        if (!live.isNullOrEmpty()) {
            val liveCiv = gameInfo.civilizations.firstOrNull { it.civID == seat }
            val row = Table(skin)
            val head = Table(skin)
            if (liveCiv != null) head.add(ImageGetter.getNationPortrait(liveCiv.nation, 28f)).padRight(6f)
            head.add("${if (liveCiv != null) leaderNameOf(liveCiv) else seat}: ".toLabel()).left()
            row.add(head).left().growX().row()
            row.add(live.toLabel(Color.LIGHT_GRAY).apply { setWrap(true) })
                .left().growX().width(messageWrapWidth()).padTop(2f).row()
            messageTable.add(row).left().growX().pad(3f).padBottom(4f).row()
        }
        messageTable.pack()
        messageScroll.layout()
        messageScroll.scrollY = messageScroll.maxY   // open at the newest line, not round 1
    }

    override fun close() {
        closed = true
        super.close()
    }
}
