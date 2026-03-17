package com.tsshara.app.network

import com.tsshara.app.data.PrefsManager
import org.json.JSONObject
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.util.Base64

/**
 * Lightweight HTTP API client using HttpURLConnection (no extra dependencies).
 * Supports Basic Auth and connects to the tsshara-cli REST API.
 * Now operates on UpsDevice instead of global Settings.
 *
 * Includes automatic retry on I/O failures (e.g. NAT hairpin, transient
 * network issues) — browsers retry transparently but HttpURLConnection does not.
 */
object ApiClient {

    data class ApiResult(
        val success: Boolean,
        val data: JSONObject? = null,
        val error: String? = null,
        val httpCode: Int = 0,
    )

    private const val CONNECT_TIMEOUT = 6_000   // per attempt
    private const val READ_TIMEOUT = 12_000     // per attempt
    private const val MAX_RETRIES = 3           // total attempts on I/O errors
    private const val RETRY_DELAY_MS = 1_500L   // delay between retries

    /** GET request with automatic retry on connection failures. */
    fun get(device: PrefsManager.UpsDevice, endpoint: String): ApiResult {
        var lastError: String = "Connection failed"
        for (attempt in 1..MAX_RETRIES) {
            try {
                val url = URL("${device.baseUrl}$endpoint")
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = CONNECT_TIMEOUT
                    readTimeout = READ_TIMEOUT
                    useCaches = false
                    setRequestProperty("Accept", "application/json")
                }
                applyAuth(conn, device)
                return readResponse(conn)
            } catch (e: IOException) {
                // Connection / read failed — retry
                lastError = e.message ?: "I/O error"
                if (attempt < MAX_RETRIES) {
                    try { Thread.sleep(RETRY_DELAY_MS) } catch (_: InterruptedException) { break }
                }
            } catch (e: Exception) {
                // Non-retriable error (e.g. malformed URL)
                return ApiResult(success = false, error = e.message ?: e.javaClass.simpleName)
            }
        }
        return ApiResult(success = false, error = lastError)
    }

    /** POST request (empty body) with automatic retry on connection failures. */
    fun post(device: PrefsManager.UpsDevice, endpoint: String): ApiResult {
        var lastError: String = "Connection failed"
        for (attempt in 1..MAX_RETRIES) {
            try {
                val url = URL("${device.baseUrl}$endpoint")
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = CONNECT_TIMEOUT
                    readTimeout = READ_TIMEOUT
                    useCaches = false
                    setRequestProperty("Accept", "application/json")
                    setRequestProperty("Content-Length", "0")
                    doOutput = true
                }
                applyAuth(conn, device)
                conn.outputStream.close() // send empty body
                return readResponse(conn)
            } catch (e: IOException) {
                lastError = e.message ?: "I/O error"
                if (attempt < MAX_RETRIES) {
                    try { Thread.sleep(RETRY_DELAY_MS) } catch (_: InterruptedException) { break }
                }
            } catch (e: Exception) {
                return ApiResult(success = false, error = e.message ?: e.javaClass.simpleName)
            }
        }
        return ApiResult(success = false, error = lastError)
    }

    private fun applyAuth(conn: HttpURLConnection, device: PrefsManager.UpsDevice) {
        if (device.username.isNotBlank()) {
            val credentials = "${device.username}:${device.password}"
            val encoded = Base64.getEncoder().encodeToString(credentials.toByteArray())
            conn.setRequestProperty("Authorization", "Basic $encoded")
        }
    }

    private fun readResponse(conn: HttpURLConnection): ApiResult {
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val body = stream?.let {
            BufferedReader(InputStreamReader(it, "UTF-8")).use { reader -> reader.readText() }
        } ?: ""
        conn.disconnect()

        if (code in 200..299 && body.isNotBlank()) {
            val json = JSONObject(body)
            // API may return 200 with success=false when the UPS command failed
            val apiSuccess = json.optBoolean("success", true)
            return if (apiSuccess) {
                ApiResult(success = true, data = json, httpCode = code)
            } else {
                val errorMsg = json.optString("error", "").ifBlank {
                    json.optString("response", "Command failed")
                }
                ApiResult(success = false, data = json, error = errorMsg, httpCode = code)
            }
        } else if (body.isNotBlank()) {
            // Non-2xx but has a JSON body — try to extract error message
            return try {
                val json = JSONObject(body)
                val errorMsg = json.optString("error", "HTTP $code")
                ApiResult(success = false, data = json, error = errorMsg, httpCode = code)
            } catch (_: Exception) {
                ApiResult(success = false, error = "HTTP $code", httpCode = code)
            }
        } else {
            return ApiResult(success = false, error = "HTTP $code", httpCode = code)
        }
    }

    fun getStatus(device: PrefsManager.UpsDevice) = get(device, "/api/status")
    fun getHealth(device: PrefsManager.UpsDevice) = get(device, "/api/health")
    fun getInfo(device: PrefsManager.UpsDevice) = get(device, "/api/info")

    /** Trigger a test command: 10s, low, beep, status, firmware */
    fun runTest(device: PrefsManager.UpsDevice, testType: String) = post(device, "/api/test/$testType")
}
