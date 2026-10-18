package com.unciv.logic.civagent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

class AgentSecretsStoreTest {
    @Test
    fun keyHintMasksSecret() {
        assertEquals("(not set)", AgentSecretsStore.keyHint(""))
        assertEquals("••••", AgentSecretsStore.keyHint("ab"))
        assertEquals("••••wxyz", AgentSecretsStore.keyHint("sk-abcdefghwxyz"))
    }

    @Test
    fun roundTripDoesNotTouchGameParameters() {
        val prevHome = System.getProperty("user.home")
        val temp = Files.createTempDirectory("civagent-secrets-test").toFile()
        try {
            System.setProperty("user.home", temp.absolutePath)
            val secrets = AgentSecrets(
                provider = "deepseek",
                apiKey = "sk-roundtrip-0011",
                baseUrl = "",
                aiServerUrl = "http://127.0.0.1:2335",
            )
            AgentSecretsStore.save(secrets)
            val loaded = AgentSecretsStore.load()
            assertEquals("sk-roundtrip-0011", loaded.apiKey)
            assertTrue(AgentSecretsStore.secretsFile().absolutePath.contains(".civagent"))
            assertFalse(AgentSecretsStore.secretsFile().absolutePath.contains("GameSettings"))
        } finally {
            System.setProperty("user.home", prevHome)
            temp.deleteRecursively()
        }
    }
}
