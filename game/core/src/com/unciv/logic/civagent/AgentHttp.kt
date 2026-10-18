package com.unciv.logic.civagent

import java.net.HttpURLConnection
import java.net.URL

/** Minimal HTTP plumbing shared by the turn hook and the chat client (JDK only, no deps). */
object AgentHttp {
    fun getJson(url: String, timeoutMs: Int): String {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.requestMethod = "GET"
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeoutMs
        connection.setRequestProperty("Accept", "application/json")
        val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
        return stream?.bufferedReader()?.readText() ?: ""
    }

    /** Like [getJson] but never throws on HTTP 4xx/5xx — only on connection failure. */
    fun getJsonAllowError(url: String, timeoutMs: Int): String {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.requestMethod = "GET"
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeoutMs
        connection.setRequestProperty("Accept", "application/json")
        connection.connect()
        val stream = try {
            connection.inputStream
        } catch (_: Exception) {
            connection.errorStream
        }
        return stream?.bufferedReader()?.readText() ?: ""
    }

    fun postJson(url: String, body: String, timeoutMs: Int): String {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.requestMethod = "POST"
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeoutMs
        connection.doOutput = true
        connection.setRequestProperty("Content-Type", "application/json")
        connection.setFixedLengthStreamingMode(body.toByteArray(Charsets.UTF_8).size)
        connection.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
        val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
        return stream?.bufferedReader()?.readText() ?: ""
    }

    /** Streaming GET: returns the raw body (used for SSE - the caller reads incrementally). */
    fun openGet(url: String, timeoutMs: Int): HttpURLConnection {
        val connection = URL(url).openConnection() as HttpURLConnection
        connection.requestMethod = "GET"
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeoutMs
        connection.setRequestProperty("Accept", "text/event-stream")
        return connection
    }
}
