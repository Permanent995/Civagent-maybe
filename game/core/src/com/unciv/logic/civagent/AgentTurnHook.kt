package com.unciv.logic.civagent

import com.unciv.logic.GameInfo
import com.unciv.logic.civilization.Civilization
import com.unciv.logic.civilization.PlayerType
import com.unciv.models.ruleset.unique.UniqueType
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/** Runtime knobs for the game-side agent integration. */
object AgentConfig {
    var enabled = true
    var aiServerUrl: String = System.getenv("CIVAGENT_AI_SERVER")
        ?: AgentSecretsStore.load().aiServerUrl.ifBlank { "http://127.0.0.1:2335" }
            .trimEnd('/')
    var decisionTimeoutMs: Int = 15_000
    /** W3: leader dialogue enabled (chat=off keeps the native game only). */
    var chatEnabled = true
    /** W4: how long the human may take to confirm an intent batch. */
    var confirmationTimeoutMs: Int = 30_000
    /** W4: headless/test override for the confirmation popup (null = real popup when possible). */
    var confirmationHandler: ((String) -> Boolean)? = null
    /** "Accept all": stop asking for the rest of the session (user choice, not persisted). */
    var autoAcceptIntents = false
    /**
     * 激活门（用户定纲 2026-09-12）：大脑与数据传输只在「Agent 席位遇见人类玩家」后启动；
     * AI-AI 相遇不触发；纯 AI 局（训练场）默认全程静默。T1/T2 方法论测试用此开关强制开启。
     */
    var forceBrainWithoutHuman = false
    var appliedTimeoutMs: Int = 3_000
}

/** Owns the gateway for the currently loaded game. */
object AgentMod {
    @Volatile
    private var current: AgentGateway? = null
    @Volatile
    private var currentGameId: String? = null

    /** Chat-drafted intents (W3): submitted from the dialogue popup, applied on the seat's NEXT turn. */
    private val draftIntents = HashMap<String, MutableList<BrainIntent>>()

    fun ensureStarted(gameInfo: GameInfo): AgentGateway? {
        if (!AgentConfig.enabled) return null // 总开关关闭：无网关、无大脑、无数据传输
        return try {
        val existing = current
        val gateway = if (existing != null && currentGameId == gameInfo.gameId) existing
        else {
            existing?.stop()
            AgentGateway(gameInfo).also { it.start() }.also {
                seedSeats(gameInfo, it)
                current = it
                currentGameId = gameInfo.gameId
            }
        }
        // 仅在确有 CivAgent 席（Fill 或已注册）时拉 Python 大脑——T0 全原生不得 spawn
        if (gameInfo.gameParameters.civAgentFillSeats || gateway.getAgentSeats().isNotEmpty()) {
            AgentBrainLauncher.ensureRunningAsync("ensureStarted")
        }
        gateway
    } catch (_: Exception) {
        null // port taken, HTTP unavailable - the civ silently falls back to native AI
    }
    }

    /** W2.4: `Fill empty seats with CivAgent` marks every alive AI major civ as an agent seat. */
    private fun seedSeats(gameInfo: GameInfo, gateway: AgentGateway) {
        if (!gameInfo.gameParameters.civAgentFillSeats) return
        if (gateway.getAgentSeats().isNotEmpty()) return // manual /seats registrations win
        gateway.setAgentSeats(
            gameInfo.civilizations
                .filter { it.playerType == PlayerType.AI && it.isMajorCiv() && !it.isDefeated() }
                .map { it.civID }
        )
    }

    /** Recent CivAgent actions per "gameId/civID".
     *  Unciv refuses to keep notifications for PlayerType.AI seats (addNotification returns early),
     *  so the agent's own action feed is kept here and fed into the observation slice. */
    private val actionLog = HashMap<String, ArrayDeque<String>>()

    fun recordAgentActions(gameId: String, civID: String, actions: List<String>) {
        if (actions.isEmpty()) return
        synchronized(actionLog) {
            val deque = actionLog.getOrPut("$gameId/$civID") { ArrayDeque() }
            actions.forEach { deque.addLast(it) }
            while (deque.size > 12) deque.removeFirst()
        }
    }

    fun recentAgentActions(gameId: String, civID: String, limit: Int = 8): List<String> =
        synchronized(actionLog) {
            actionLog["$gameId/$civID"]?.toList()?.takeLast(limit).orEmpty()
        }

    /** P-memory: last notificationsLog turn already shipped to the brain, per (gameId, civId). */
    private val eventCursors = HashMap<String, Int>()

    /**
     * New engine notifications for one seat since the last call - the brain's event stream.
     * Source is [Civilization.notificationsLog] (grouped by turn, written for every civ,
     * already fog-filtered by the engine because it is what this civ itself was told).
     */
    fun drainNewEvents(gameId: String, civ: Civilization): List<EngineEvent> {
        val cursorKey = "$gameId/${civ.civID.lowercase()}"
        val lastTurn = eventCursors[cursorKey] ?: -1
        val events = civ.notificationsLog.asSequence()
            .filter { it.turn > lastTurn }
            .flatMap { log -> log.notifications.asSequence().map { EngineEvent(log.turn, it.category.name, it.text) } }
            .take(40) // cap a burst (e.g. first turns) so one decision payload stays bounded
            .toList()
        val maxSeen = events.maxOfOrNull { it.turn }
        if (maxSeen != null) eventCursors[cursorKey] = maxSeen
        return events
    }

    fun queueDraftIntent(gameId: String, civID: String, intent: BrainIntent) {
        synchronized(draftIntents) {
            draftIntents.getOrPut("$gameId/$civID") { mutableListOf() }.add(intent)
        }
    }

    fun drainDraftIntents(gameId: String, civID: String): List<BrainIntent> = synchronized(draftIntents) {
        draftIntents.remove("$gameId/$civID") ?: emptyList()
    }
}

@Serializable
data class AppliedEntry(
    val type: String,
    val targetCivID: String? = null,
    val ok: Boolean,
    val error: String? = null
)

@Serializable
data class AppliedReport(
    val gameId: String,
    val civID: String,
    val applied: List<AppliedEntry>,
    val fellBackToNative: Boolean
)

@Serializable
data class BrainDecision(val intents: List<BrainIntent> = emptyList())

@Serializable
data class BrainTradeOffer(val type: String, val amount: Float = 0f, val ref: String? = null)

@Serializable
data class BrainTrade(
    val ourOffers: List<BrainTradeOffer> = emptyList(),
    val theirOffers: List<BrainTradeOffer> = emptyList()
)

@Serializable
data class BrainIntel(val claim: String)

@Serializable
data class BrainIntent(
    val type: String,
    val actorCivID: String? = null,
    val targetCivID: String? = null,
    val chatText: String? = null,
    val trade: BrainTrade? = null,
    val intel: BrainIntel? = null,
    /** Optional strategic hint; dropped when target is not land-reachable without navy. */
    val warFocus: String? = null
)

/**
 * W2: called right before an AI civ's native automation. If the civ is a registered
 * CivAgent seat and the brain answers in time, its intents are executed on top of the
 * turn; any failure (unreachable, timeout, bad reply) means the seat plays the full
 * native turn (EXECUTION.md W2.1 超时回原生).
 *
 * Note: the decision HTTP call blocks the calling thread for up to
 * [AgentConfig.decisionTimeoutMs]; turn processing is synchronous in Unciv, so this is
 * acceptable for the prototype and revisited when W4 adds confirmation popups.
 */
object AgentTurnHook {
    private val json = Json { encodeDefaults = true; ignoreUnknownKeys = true; isLenient = true }

    /** Intent categories this seat covered with successfully applied intents this turn (P1 方案 A). */
    enum class Coverage { DIPLOMACY, TRADE }

    private val diplomacyTypes = setOf("declare_war", "make_peace", "denounce")
    private val tradeTypes = setOf("propose_trade", "buy_luxury")

    /** Native automation calls to skip this turn because the agent already acted there. */
    fun nativeSkipSet(covered: Set<Coverage>): Set<String> = buildSet {
        if (Coverage.DIPLOMACY in covered) {
            add("declareWar"); add("offerPeaceTreaty"); add("denounce")
        }
        if (Coverage.TRADE in covered) add("exchangeLuxuries")
    }

    fun applyIntents(civ: Civilization): Set<Coverage> {
        if (!AgentConfig.enabled) return emptySet()
        val gameInfo = civ.gameInfo
        val gateway = AgentMod.ensureStarted(gameInfo) ?: return emptySet()
        if (civ.civID !in gateway.getAgentSeats()) return emptySet()

        // 激活门：未遇见人类玩家前静默大脑与数据传输（AI-AI 相遇也不触发）。
        // 例外：显式开关（训练场 T1/T2）。人类玩家出现且相遇后，此门常开。
        if (!AgentConfig.forceBrainWithoutHuman) {
            val human = gameInfo.civilizations.firstOrNull { it.isHuman() && !it.isSpectator() }
                ?: return emptySet() // 纯 AI 局：无人类则大脑全程静默
            if (civ.getKnownCivs().none { it.civID == human.civID }) return emptySet()
        }

        // P5: clear slots whose engine-side TradeRequest is gone (accepted/declined/expired)
        AgentTradeSlots.reconcile(gameInfo.gameId, civ)

        // Chat-drafted intents (W3) always apply on the seat's turn, even when the brain is unreachable.
        val drafts = AgentMod.drainDraftIntents(gameInfo.gameId, civ.civID)

        val brainIntents = try {
            val slice = ObservationSlicer.slice(gameInfo, civ)
                .copy(
                    newEvents = AgentMod.drainNewEvents(gameInfo.gameId, civ),
                    language = try {
                        com.unciv.UncivGame.Current.settings.language
                    } catch (_: Exception) {
                        "English"
                    }
                )
            val response = AgentHttp.postJson("${AgentConfig.aiServerUrl}/v1/decision", json.encodeToString(slice), AgentConfig.decisionTimeoutMs)
            json.decodeFromString<BrainDecision>(response).intents
        } catch (_: Exception) {
            emptyList() // timeout / unreachable -> the rest of the seat plays full native
        }
        if (brainIntents.isEmpty() && drafts.isEmpty()) return emptySet()

        // W4.1/W4.2: the human confirms the batch; rejection or timeout = the seat plays
        // a full native turn and nothing is re-sent. Headless runs (tests, no GL) skip this.
        val ordered = (brainIntents + drafts).sortedBy { IntentNarrator.order(it) }
        if (!confirmIntents(civ, ordered)) return emptySet()

        val applied = IntentExecutor.execute(gateway, civ, ordered)
        val covered = applied.filter { it.ok }.map { it.type }.toSet().let { types ->
            buildSet {
                if (types.any { it in diplomacyTypes }) add(Coverage.DIPLOMACY)
                if (types.any { it in tradeTypes }) add(Coverage.TRADE)
            }
        }
        // Keep an action feed: Unciv does not retain notifications for AI seats, and the player
        // needs to see what actually landed (a confirmed batch can still fail per intent).
        AgentMod.recordAgentActions(
            gameInfo.gameId, civ.civID,
            applied.map { entry ->
                val target = entry.targetCivID ?: "-"
                if (entry.ok) "${entry.type} -> $target"
                else "${entry.type} -> $target FAILED: ${entry.error ?: "?"}"
            }
        )
        // P4 (#4): every agent action is recorded into the NATIVE notification log of every
        // human who has met the actor - the action feed toast alone vanished on restart.
        notifyHumans(gameInfo, civ, ordered.zip(applied))
        val failures = applied.filterNot { it.ok }
        if (failures.isNotEmpty()) AgentIntentReporter.reportFailures(civ, failures)
        try {
            AgentHttp.postJson(
                "${AgentConfig.aiServerUrl}/v1/applied",
                json.encodeToString(AppliedReport(gameInfo.gameId, civ.civID, applied, fellBackToNative = brainIntents.isEmpty())),
                AgentConfig.appliedTimeoutMs
            )
        } catch (_: Exception) {
            // applied receipt is best-effort
        }
        return covered
    }

    /** #4: mirror agent actions into human players' notification logs (persistent, translatable). */
    private fun notifyHumans(gameInfo: GameInfo, actor: Civilization, pairs: List<Pair<BrainIntent, AppliedEntry>>) {
        val humans = gameInfo.civilizations.filter {
            it.isHuman() && !it.isSpectator() && it != actor &&
                it.getKnownCivs().any { known -> known.civID == actor.civID }
        }
        if (humans.isEmpty()) return
        for ((intent, entry) in pairs) {
            if (intent.type == "chat") continue // prose flavor, not an action
            var text = IntentNarrator.describeOne(actor, intent)
            if (!entry.ok) text += " — rejected: ${entry.error?.take(120)}"
            val category = when (intent.type) {
                "declare_war" -> com.unciv.logic.civilization.NotificationCategory.War
                "propose_trade", "buy_luxury" -> com.unciv.logic.civilization.NotificationCategory.Trade
                else -> com.unciv.logic.civilization.NotificationCategory.Diplomacy
            }
            for (human in humans) {
                try {
                    human.addNotification(text, category, "OtherIcons/Chat")
                } catch (_: Exception) {
                    // a broken icon must never break a turn
                }
            }
        }
    }

    /**
     * Approval gate, scoped by user feedback (2026-09-12): the human only confirms batches
     * that target THEIR civ. AI-vs-AI diplomacy/trade applies automatically - the player
     * sees it in the notification log, not a popup. "Accept all" stops asking this session.
     */
    fun needsConfirmation(gameInfo: GameInfo, intents: List<BrainIntent>): Boolean {
        if (AgentConfig.autoAcceptIntents) return false
        val human = gameInfo.civilizations.firstOrNull { it.isHuman() && !it.isSpectator() }
            ?: return false // no human in this game (simulation) - nothing to approve
        return intents.any { it.type != "chat" && it.targetCivID == human.civID }
    }

    /** True = batch confirmed. Tests set [AgentConfig.confirmationHandler]; on the GL thread
     *  a real popup blocks this background thread via a latch. */
    private fun confirmIntents(civ: Civilization, intents: List<BrainIntent>): Boolean {
        val gameInfo = civ.gameInfo
        if (!needsConfirmation(gameInfo, intents)) return true // unrelated to the player - just act
        val customHandler = AgentConfig.confirmationHandler
        if (customHandler != null) {
            // Tests / headless set this; a throwing handler must not crash the turn either.
            return try {
                customHandler(IntentNarrator.describe(civ, intents))
            } catch (exception: Exception) {
                com.unciv.utils.Log.debug("intent confirmation handler failed: ${exception.message}")
                false
            }
        }
        val worldScreen = try {
            com.unciv.UncivGame.Current.worldScreen
        } catch (_: Exception) {
            null
        } ?: return true // headless (tests/simulation): no human to confirm
        val confirmed = java.util.concurrent.atomic.AtomicBoolean(false)
        val latch = java.util.concurrent.CountDownLatch(1)
        com.unciv.utils.Concurrency.runOnGLThread {
            try {
                com.unciv.ui.screens.worldscreen.AgentIntentConfirmPopup(
                    worldScreen, IntentNarrator.describe(civ, intents)
                ) { result ->
                    if (result == com.unciv.ui.screens.worldscreen.ConfirmResult.ACCEPT_ALL)
                        AgentConfig.autoAcceptIntents = true
                    confirmed.set(result != com.unciv.ui.screens.worldscreen.ConfirmResult.REJECT)
                    latch.countDown()
                }
            } catch (exception: Exception) {
                // A confirmation-UI failure must never crash a turn (see the stageToShowOn NPE).
                // Treat it as "not confirmed" so the seat plays the full native turn instead.
                com.unciv.utils.Log.debug("AgentIntentConfirmPopup failed: ${exception.message}")
                confirmed.set(false)
                latch.countDown()
            }
        }
        val confirmedInTime = latch.await(AgentConfig.confirmationTimeoutMs.toLong(), java.util.concurrent.TimeUnit.MILLISECONDS)
        return confirmedInTime && confirmed.get() // timeout = rejection, batch is not re-sent
    }
}

/**
 * Executes brain intents through the same whitelist-checked console pipeline as W1.
 * W4: trades go through [com.unciv.logic.trade.TradeLogic] with ONE outbound offer per
 * civ pair - a new confirmed offer retracts the previous one (AgentTradeSlots). Physical
 * checks happen in the engine: gold can never appear from nowhere, amounts above treasury
 * are refused here before the trade is even built.
 */
object IntentExecutor {
    fun execute(gateway: AgentGateway, civ: Civilization, intents: List<BrainIntent>): List<AppliedEntry> {
        val applied = mutableListOf<AppliedEntry>()
        for (intent in intents) {
            when (intent.type) {
                "declare_war", "make_peace", "seek_peace", "denounce" -> {
                    // D1: a ruleset may forbid diplomatic relationship changes (mods / scenarios).
                    // The agent is a player, not a GM - it must obey the same gate as native AI.
                    if (civ.gameInfo.ruleset.modOptions.hasUnique(UniqueType.DiplomaticRelationshipsCannotChange)) {
                        applied.add(AppliedEntry(intent.type, intent.targetCivID, ok = false,
                            error = "ruleset forbids diplomatic relationship changes"))
                        continue
                    }
                    val target = intent.targetCivID
                    if (target == null) {
                        applied.add(AppliedEntry(intent.type, null, ok = false, error = "missing targetCivID"))
                        continue
                    }
                    val subcommand = when (intent.type) {
                        "declare_war" -> "declare-war"
                        "make_peace", "seek_peace" -> "make-peace"
                        else -> "denounce"
                    }
                    // Console splits on whitespace - multi-word civ names MUST be quoted
                    val reply = gateway.executeConsole("diplomacy $subcommand \"${civ.civID}\" \"$target\"", tierGm = false)
                    val note = if (intent.type == "declare_war") warFocusNote(civ, target, intent.warFocus) else null
                    val error = listOfNotNull(reply.message?.take(200), note).joinToString("; ").ifBlank { null }
                    applied.add(
                        AppliedEntry(intent.type, target, ok = reply.isOk, error = if (reply.isOk) note else error)
                    )
                }
                "propose_trade", "buy_luxury" -> applied.add(executeTrade(civ, intent))
                "open_border", "research_agreement", "mutual_defense", "form_ally" ->
                    applied.add(executeAgreement(civ, intent))
                "share_intel" -> applied.add(executeShareIntel(intent))
                "chat" -> applied.add(AppliedEntry("chat", intent.targetCivID, ok = true, error = null))
                else -> applied.add(
                    AppliedEntry(intent.type, intent.targetCivID, ok = false, error = "intent type not executable")
                )
            }
        }
        return applied
    }

    /** W5: claim text only — never writes fog/tech. Brain stores the claim via /v1/applied. */
    /**
     * open_border / research_agreement / mutual_defense / form_ally: agreement intents mapped onto
     * the engine's own native forms (a Trade offer, or a declaration of friendship).
     *
     * Before this they fell through to "intent type not executable" and failed *silently* after the
     * human had already confirmed the batch - e.g. a confirmed make-peace that never happened.
     */
    private fun executeAgreement(civ: Civilization, intent: BrainIntent): AppliedEntry {
        val target = intent.targetCivID
            ?: return AppliedEntry(intent.type, null, ok = false, error = "missing targetCivID")
        val otherCiv = civ.gameInfo.civilizations.firstOrNull { it.civID == target && !it.isDefeated() }
            ?: return AppliedEntry(intent.type, target, ok = false, error = "unknown or defeated target civ")
        if (otherCiv == civ) {
            return AppliedEntry(intent.type, target, ok = false, error = "cannot treat with oneself")
        }
        val diplomacy = civ.getDiplomacyManager(otherCiv)
            ?: return AppliedEntry(intent.type, target, ok = false, error = "no diplomacy with $target")

        if (intent.type == "form_ally") {
            return try {
                diplomacy.signDeclarationOfFriendship()
                AppliedEntry(intent.type, target, ok = true, error = null)
            } catch (exception: Exception) {
                AppliedEntry(intent.type, target, ok = false, error = exception.message?.take(200))
            }
        }

        val speed = civ.gameInfo.speed
        val name = when (intent.type) {
            "open_border" -> com.unciv.Constants.openBorders
            "research_agreement" -> com.unciv.Constants.researchAgreement
            else -> com.unciv.Constants.defensivePact
        }
        val offerType = if (intent.type == "open_border") com.unciv.logic.trade.TradeOfferType.Agreement
        else com.unciv.logic.trade.TradeOfferType.Treaty
        val trade = com.unciv.logic.trade.Trade()
        trade.ourOffers.add(com.unciv.logic.trade.TradeOffer(name, offerType, speed = speed))
        // Treaties are shared by both sides; an agreement such as open borders is one-sided.
        if (offerType == com.unciv.logic.trade.TradeOfferType.Treaty) {
            trade.theirOffers.add(com.unciv.logic.trade.TradeOffer(name, offerType, speed = speed))
        }
        AgentTradeSlots.put(civ.gameInfo.gameId, civ.civID, target, trade)
        otherCiv.tradeRequests.removeAll { it.requestingCiv == civ.civID }
        otherCiv.tradeRequests.add(com.unciv.logic.trade.TradeRequest(civ.civID, trade.reverse()))
        return AppliedEntry(intent.type, target, ok = true, error = "agreement offered (slot)")
    }

    private fun executeShareIntel(intent: BrainIntent): AppliedEntry {
        val claim = intent.intel?.claim?.trim().orEmpty()
        if (claim.isEmpty()) {
            return AppliedEntry("share_intel", intent.targetCivID, ok = false, error = "share_intel requires intel.claim text")
        }
        return AppliedEntry("share_intel", intent.targetCivID, ok = true, error = "claim recorded (${claim.take(80)})")
    }

    /**
     * If warFocus names an unreachable civ (other continent, no navy), keep declare_war but
     * annotate that war_focus was downgraded (决策 W5.5).
     */
    private fun warFocusNote(civ: Civilization, targetId: String, warFocus: String?): String? {
        val focusId = warFocus?.takeIf { it.isNotBlank() } ?: targetId
        val other = civ.gameInfo.civilizations.firstOrNull { it.civID == focusId } ?: return null
        val capitalA = civ.getCapital()?.getCenterTile() ?: return null
        val capitalB = other.getCapital()?.getCenterTile() ?: return "war_focus unreachable (no capital)"
        val sameContinent = capitalA.getContinent() == capitalB.getContinent()
        val navy = civ.units.getCivUnits().count { it.isMilitary() && it.baseUnit.isWaterUnit }
        if (!sameContinent && navy == 0) {
            return "war_focus downgraded: $focusId not land-reachable and navyShipCount=0"
        }
        return null
    }

    private fun executeTrade(civ: Civilization, intent: BrainIntent): AppliedEntry {
        val target = intent.targetCivID
            ?: return AppliedEntry(intent.type, null, ok = false, error = "missing targetCivID")
        val tradePayload = intent.trade
            ?: return AppliedEntry(intent.type, target, ok = false, error = "trade payload missing")
        val otherCiv = civ.gameInfo.civilizations.firstOrNull { it.civID == target && !it.isDefeated() }
            ?: return AppliedEntry(intent.type, target, ok = false, error = "unknown or defeated target civ")
        if (otherCiv == civ) {
            return AppliedEntry(intent.type, target, ok = false, error = "cannot trade with oneself")
        }

        val trade = com.unciv.logic.trade.Trade()
        val speed = civ.gameInfo.speed
        var ourGoldCost = 0
        for (offer in tradePayload.ourOffers) {
            when (offer.type.lowercase()) {
                "gold" -> {
                    if (offer.amount <= 0) return AppliedEntry(intent.type, target, ok = false, error = "gold amount must be positive")
                    ourGoldCost += offer.amount.toInt()
                    trade.ourOffers.add(com.unciv.logic.trade.TradeOffer(com.unciv.Constants.flatGold, com.unciv.logic.trade.TradeOfferType.Gold, offer.amount.toInt(), speed))
                }
                "gold_per_turn", "goldpt" -> {
                    if (offer.amount <= 0) return AppliedEntry(intent.type, target, ok = false, error = "gold_per_turn amount must be positive")
                    if (offer.amount.toInt() > civ.stats.statsForNextTurn.gold.toInt())
                        return AppliedEntry(intent.type, target, ok = false, error = "gold per turn above our income")
                    trade.ourOffers.add(com.unciv.logic.trade.TradeOffer(com.unciv.Constants.goldPerTurn, com.unciv.logic.trade.TradeOfferType.Gold_Per_Turn, offer.amount.toInt(), speed))
                }
                "open_border", "open_borders" -> trade.ourOffers.add(
                    com.unciv.logic.trade.TradeOffer(com.unciv.Constants.openBorders, com.unciv.logic.trade.TradeOfferType.Agreement, speed = speed)
                )
                "embassy" -> trade.ourOffers.add(
                    com.unciv.logic.trade.TradeOffer(com.unciv.Constants.acceptEmbassy, com.unciv.logic.trade.TradeOfferType.Embassy, speed = speed)
                )
                else -> {
                    // luxury / strategic / stockpiled resources by name
                    val resource = civ.gameInfo.ruleset.tileResources[offer.ref ?: offer.type]
                        ?: return AppliedEntry(intent.type, target, ok = false, error = "unknown offer type '${offer.type}'")
                    val offerType = if (resource.resourceType == com.unciv.models.ruleset.tile.ResourceType.Strategic)
                        com.unciv.logic.trade.TradeOfferType.Strategic_Resource
                    else com.unciv.logic.trade.TradeOfferType.Luxury_Resource
                    if (civ.getResourceAmount(resource.name) < offer.amount.toInt())
                        return AppliedEntry(intent.type, target, ok = false, error = "not enough ${resource.name} (have ${civ.getResourceAmount(resource.name)})")
                    trade.ourOffers.add(com.unciv.logic.trade.TradeOffer(resource.name, offerType, offer.amount.toInt(), speed))
                }
            }
        }
        if (ourGoldCost > 0 && ourGoldCost > civ.gold) {
            // the fuse (PLAN.md 6.2): money may never appear from nowhere. Only fires when
            // gold is actually offered - a resource-only trade from a deficit treasury
            // (-4) must NOT be rejected ("gold cost 0 exceeds treasury -4").
            return AppliedEntry(intent.type, target, ok = false, error = "gold cost $ourGoldCost exceeds treasury ${civ.gold}")
        }
        for (offer in tradePayload.theirOffers) {
            when (offer.type.lowercase()) {
                "gold" -> trade.theirOffers.add(com.unciv.logic.trade.TradeOffer(com.unciv.Constants.flatGold, com.unciv.logic.trade.TradeOfferType.Gold, offer.amount.toInt(), speed))
                "gold_per_turn", "goldpt" -> trade.theirOffers.add(com.unciv.logic.trade.TradeOffer(com.unciv.Constants.goldPerTurn, com.unciv.logic.trade.TradeOfferType.Gold_Per_Turn, offer.amount.toInt(), speed))
                "open_border", "open_borders" -> trade.theirOffers.add(
                    com.unciv.logic.trade.TradeOffer(com.unciv.Constants.openBorders, com.unciv.logic.trade.TradeOfferType.Agreement, speed = speed)
                )
                "embassy" -> trade.theirOffers.add(
                    com.unciv.logic.trade.TradeOffer(com.unciv.Constants.acceptEmbassy, com.unciv.logic.trade.TradeOfferType.Embassy, speed = speed)
                )
                else -> {
                    val resource = civ.gameInfo.ruleset.tileResources[offer.ref ?: offer.type]
                        ?: return AppliedEntry(intent.type, target, ok = false, error = "unknown their-offer '${offer.ref ?: offer.type}'")
                    val offerType = if (resource.resourceType == com.unciv.models.ruleset.tile.ResourceType.Strategic)
                        com.unciv.logic.trade.TradeOfferType.Strategic_Resource
                    else com.unciv.logic.trade.TradeOfferType.Luxury_Resource
                    trade.theirOffers.add(com.unciv.logic.trade.TradeOffer(resource.name, offerType, offer.amount.toInt(), speed))
                }
            }
        }
        if (trade.ourOffers.isEmpty() && trade.theirOffers.isEmpty()) {
            return AppliedEntry(intent.type, target, ok = false, error = "empty trade")
        }

        // W4.3: one slot per pair - a confirmed new offer retracts the old pending one
        AgentTradeSlots.put(civ.gameInfo.gameId, civ.civID, target, trade)
        // Deliver as a trade request on the target's side (same channel the native AI uses),
        // removing any previous request from us first so only the newest survives.
        otherCiv.tradeRequests.removeAll { it.requestingCiv == civ.civID }
        otherCiv.tradeRequests.add(com.unciv.logic.trade.TradeRequest(civ.civID, trade.reverse()))
        return AppliedEntry(intent.type, target, ok = true, error = "trade request delivered (slot)")
    }
}
