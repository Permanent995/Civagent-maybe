package com.unciv.logic.civagent

import com.unciv.utils.Concurrency
import com.unciv.utils.Log
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Host-in-game: when CivAgent mode is on, try to start the local Python brain if it is down.
 *
 * Does not touch GameInfo. Spawns `python -m deployment.ai.agent_api` in [AgentSecrets.repoRoot]
 * (or CIVAGENT_REPO / common Desktop paths). Failures stay soft — native AI still plays.
 */
object AgentBrainLauncher {
    enum class Status {
        AlreadyUp,
        Started,
        Starting,
        NoRepo,
        SpawnFailed,
        TimedOut,
        Disabled,
    }

    private val launching = AtomicBoolean(false)
    @Volatile private var child: Process? = null
    /** Last spawn attempt, so a failing probe cannot spawn a brain every single civ-turn. */
    @Volatile private var lastSpawnAttemptMs = 0L

    /** Don't try to spawn again within this window (see the zombie-brain incident). */
    private const val RETRY_COOLDOWN_MS = 20_000L

    fun ensureRunningAsync(reason: String = "civagent") {
        Concurrency.run("AgentBrainLaunch") {
            val status = ensureRunning(reason)
            Log.debug("AgentBrainLauncher($reason): $status")
        }
    }

    /** Blocking probe + optional spawn. Safe to call off the GL thread. */
    fun ensureRunning(reason: String = "civagent"): Status {
        if (!AgentConfig.enabled) return Status.Disabled
        if (brainListening()) return Status.AlreadyUp
        if (!launching.compareAndSet(false, true)) return Status.Starting
        try {
            if (brainListening()) return Status.AlreadyUp
            // Cooldown: repeated civ-turns must not each spawn a new process. Without this the
            // game could start a brain per turn faster than the old one bound its port.
            val now = System.currentTimeMillis()
            if (now - lastSpawnAttemptMs < RETRY_COOLDOWN_MS) return Status.Starting
            lastSpawnAttemptMs = now
            val repo = resolveRepoRoot() ?: return Status.NoRepo
            if (!File(repo, "deployment/ai/agent_api.py").isFile &&
                !File(repo, "deployment${File.separator}ai${File.separator}agent_api.py").isFile
            ) return Status.NoRepo

            val logDir = File(repo, "deployment/data/host").also { it.mkdirs() }
            val logFile = File(logDir, "agent_api.game-launch.log")
            val python = findPython() ?: return Status.SpawnFailed
            return try {
                val pb = ProcessBuilder(python, "-m", "deployment.ai.agent_api")
                    .directory(File(repo))
                    .redirectErrorStream(true)
                    .redirectOutput(logFile)
                val env = pb.environment()
                // Prefer sidecar keys already on disk; do not inject secrets into the command line.
                env.putIfAbsent("AGENT_API_PORT", "2335")
                child = pb.start()
                if (waitUntilListening(20_000)) Status.Started else Status.TimedOut
            } catch (ex: Exception) {
                Log.debug("AgentBrainLauncher spawn failed: ${ex.message}")
                Status.SpawnFailed
            }
        } finally {
            launching.set(false)
        }
    }

    /** Ask the running brain to exit, wait for the port to free, then spawn a fresh one.
     *  The brain runs from source, so code/config changes need a restart; this backs the
     *  dialogue "+" menu entry. Status is reported off the GL thread. */
    fun restart(onStatus: (Status) -> Unit = {}) {
        Concurrency.run("AgentBrainRestart") {
            val base = AgentConfig.aiServerUrl.trimEnd('/')
            try {
                AgentHttp.postJson("$base/v1/shutdown", "{}", 2_000)
            } catch (_: Exception) {
                // already down, or a brain built before /v1/shutdown existed
            }
            waitForPortFree(8_000)
            if (brainListening()) {
                // Older brain: kill the process we spawned ourselves, if any.
                child?.let { process ->
                    try {
                        process.destroy()
                    } catch (_: Exception) {
                        // nothing more we can do
                    }
                    waitForPortFree(5_000)
                }
            }
            if (brainListening()) {
                onStatus(Status.TimedOut) // not ours to kill - ask the user to stop it
                return@run
            }
            onStatus(ensureRunning("restart"))
        }
    }

    private fun waitForPortFree(timeoutMs: Long) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline && brainListening()) Thread.sleep(250)
    }

    fun statusText(status: Status): String = when (status) {
        Status.AlreadyUp -> "Brain already running."
        Status.Started -> "Brain started."
        Status.Starting -> "Brain start already in progress\u2026"
        Status.NoRepo -> "Repo not found. Set Repo path to your Civagent_repo folder."
        Status.SpawnFailed -> "Could not spawn python. Is python on PATH?"
        Status.TimedOut -> "Brain did not restart - stop it manually, then reopen the game."
        Status.Disabled -> "CivAgent disabled."
    }

    fun brainListening(): Boolean {
        val base = AgentConfig.aiServerUrl.trimEnd('/')
        return try {
            val body = AgentHttp.getJson("$base/healthz", timeoutMs = 1_500)
            body.contains("llm")
        } catch (_: Exception) {
            false
        }
    }

    fun resolveRepoRoot(): String? {
        val secrets = AgentSecretsStore.load()
        val candidates = listOfNotNull(
            secrets.repoRoot.takeIf { it.isNotBlank() },
            System.getenv("CIVAGENT_REPO"),
            File(System.getProperty("user.home"), "Desktop${File.separator}Civagent_repo").absolutePath,
            File(System.getProperty("user.home"), "Desktop${File.separator}CivAgent_repo").absolutePath,
            File(System.getProperty("user.home"), "Documents${File.separator}Civagent_repo").absolutePath,
        )
        return candidates.firstOrNull { path ->
            File(path, "deployment/ai/agent_api.py").isFile ||
                File(path, "deployment${File.separator}ai${File.separator}agent_api.py").isFile
        }
    }

    private fun findPython(): String? {
        val names = if (isWindows()) listOf("python", "py") else listOf("python3", "python")
        for (name in names) {
            try {
                val p = ProcessBuilder(name, "--version").redirectErrorStream(true).start()
                if (p.waitFor() == 0) return name
            } catch (_: Exception) { /* try next */ }
        }
        return null
    }

    private fun waitUntilListening(timeoutMs: Long): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (brainListening()) return true
            if (child?.isAlive == false) return false
            Thread.sleep(400)
        }
        return brainListening()
    }

    private fun isWindows(): Boolean =
        System.getProperty("os.name")?.lowercase()?.contains("win") == true
}
