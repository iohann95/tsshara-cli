package com.tsshara.app.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.*
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "tsshara_settings")

/**
 * Manages all app settings using Jetpack DataStore.
 * Supports multiple UPS devices, each with its own connection config.
 */
class PrefsManager(private val context: Context) {

    companion object {
        val KEY_MONITORING_ENABLED = booleanPreferencesKey("monitoring_enabled")
        val KEY_POLL_INTERVAL_SECONDS = intPreferencesKey("poll_interval_seconds")
        val KEY_LANGUAGE = stringPreferencesKey("language")
        val KEY_DEVICES_JSON = stringPreferencesKey("devices_json")
        // Track last-known status per device to avoid duplicate notifications
        val KEY_LAST_NOTIF_STATE = stringPreferencesKey("last_notif_state_json")

        // Per-type notification preferences (default: all enabled)
        val KEY_NOTIF_POWER_FAIL = booleanPreferencesKey("notif_power_fail")
        val KEY_NOTIF_BATTERY_LOW = booleanPreferencesKey("notif_battery_low")
        val KEY_NOTIF_UPS_FAILED = booleanPreferencesKey("notif_ups_failed")
        val KEY_NOTIF_POWER_RESTORED = booleanPreferencesKey("notif_power_restored")
        val KEY_NOTIF_CONNECTION_ERROR = booleanPreferencesKey("notif_connection_error")
        val KEY_NOTIF_CONNECTION_RESTORED = booleanPreferencesKey("notif_connection_restored")
        val KEY_NOTIF_TEST_RUNNING = booleanPreferencesKey("notif_test_running")

        const val DEFAULT_PORT = "8080"
        const val DEFAULT_POLL_SECONDS = 5
        const val LANG_SYSTEM = "system"
        const val LANG_EN = "en"
        const val LANG_PT_BR = "pt-BR"
    }

    /** A single UPS device connection configuration. */
    data class UpsDevice(
        val id: String = UUID.randomUUID().toString(),
        val name: String = "",
        val host: String = "",
        val port: String = DEFAULT_PORT,
        val useHttps: Boolean = false,
        val username: String = "",
        val password: String = "",
    ) {
        val isConfigured: Boolean get() = host.isNotBlank()

        val baseUrl: String
            get() {
                val scheme = if (useHttps) "https" else "http"
                val p = port.ifBlank { DEFAULT_PORT }
                return "$scheme://$host:$p"
            }

        val displayName: String get() = name.ifBlank { "$host:${port.ifBlank { DEFAULT_PORT }}" }

        fun toJson(): JSONObject = JSONObject().apply {
            put("id", id)
            put("name", name)
            put("host", host)
            put("port", port)
            put("useHttps", useHttps)
            put("username", username)
            put("password", password)
        }

        companion object {
            fun fromJson(j: JSONObject) = UpsDevice(
                id = j.optString("id", UUID.randomUUID().toString()),
                name = j.optString("name", ""),
                host = j.optString("host", ""),
                port = j.optString("port", DEFAULT_PORT),
                useHttps = j.optBoolean("useHttps", false),
                username = j.optString("username", ""),
                password = j.optString("password", ""),
            )
        }
    }

    /** Global app settings + list of devices. */
    data class AppSettings(
        val devices: List<UpsDevice> = emptyList(),
        val monitoringEnabled: Boolean = false,
        val pollIntervalSeconds: Int = DEFAULT_POLL_SECONDS,
        val language: String = LANG_SYSTEM,
        val lastNotifState: Map<String, String> = emptyMap(), // deviceId -> last status_text
        // Per-type notification toggles (all default true)
        val notifPowerFail: Boolean = true,
        val notifBatteryLow: Boolean = true,
        val notifUpsFailed: Boolean = true,
        val notifPowerRestored: Boolean = true,
        val notifConnectionError: Boolean = true,
        val notifConnectionRestored: Boolean = true,
        val notifTestRunning: Boolean = true,
    ) {
        val hasConfiguredDevices: Boolean get() = devices.any { it.isConfigured }
    }

    val settingsFlow: Flow<AppSettings> = context.dataStore.data.map { prefs ->
        val devices = parseDevices(prefs[KEY_DEVICES_JSON] ?: "[]")
        val notifState = try {
            val j = JSONObject(prefs[KEY_LAST_NOTIF_STATE] ?: "{}")
            j.keys().asSequence().associateWith { j.getString(it) }
        } catch (_: Exception) { emptyMap() }

        AppSettings(
            devices = devices,
            monitoringEnabled = prefs[KEY_MONITORING_ENABLED] ?: false,
            pollIntervalSeconds = prefs[KEY_POLL_INTERVAL_SECONDS] ?: DEFAULT_POLL_SECONDS,
            language = prefs[KEY_LANGUAGE] ?: LANG_SYSTEM,
            lastNotifState = notifState,
            notifPowerFail = prefs[KEY_NOTIF_POWER_FAIL] ?: true,
            notifBatteryLow = prefs[KEY_NOTIF_BATTERY_LOW] ?: true,
            notifUpsFailed = prefs[KEY_NOTIF_UPS_FAILED] ?: true,
            notifPowerRestored = prefs[KEY_NOTIF_POWER_RESTORED] ?: true,
            notifConnectionError = prefs[KEY_NOTIF_CONNECTION_ERROR] ?: true,
            notifConnectionRestored = prefs[KEY_NOTIF_CONNECTION_RESTORED] ?: true,
            notifTestRunning = prefs[KEY_NOTIF_TEST_RUNNING] ?: true,
        )
    }

    suspend fun save(settings: AppSettings) {
        context.dataStore.edit { prefs ->
            prefs[KEY_DEVICES_JSON] = serializeDevices(settings.devices)
            prefs[KEY_MONITORING_ENABLED] = settings.monitoringEnabled
            prefs[KEY_POLL_INTERVAL_SECONDS] = settings.pollIntervalSeconds
            prefs[KEY_LANGUAGE] = settings.language
            prefs[KEY_NOTIF_POWER_FAIL] = settings.notifPowerFail
            prefs[KEY_NOTIF_BATTERY_LOW] = settings.notifBatteryLow
            prefs[KEY_NOTIF_UPS_FAILED] = settings.notifUpsFailed
            prefs[KEY_NOTIF_POWER_RESTORED] = settings.notifPowerRestored
            prefs[KEY_NOTIF_CONNECTION_ERROR] = settings.notifConnectionError
            prefs[KEY_NOTIF_CONNECTION_RESTORED] = settings.notifConnectionRestored
            prefs[KEY_NOTIF_TEST_RUNNING] = settings.notifTestRunning
        }
    }

    /** Persist only the language preference (used before locale change triggers activity recreation). */
    suspend fun saveLanguage(language: String) {
        context.dataStore.edit { prefs ->
            prefs[KEY_LANGUAGE] = language
        }
    }

    suspend fun addDevice(device: UpsDevice) {
        context.dataStore.edit { prefs ->
            val existing = parseDevices(prefs[KEY_DEVICES_JSON] ?: "[]")
            prefs[KEY_DEVICES_JSON] = serializeDevices(existing + device)
        }
    }

    suspend fun updateDevice(device: UpsDevice) {
        context.dataStore.edit { prefs ->
            val existing = parseDevices(prefs[KEY_DEVICES_JSON] ?: "[]")
            prefs[KEY_DEVICES_JSON] = serializeDevices(existing.map { if (it.id == device.id) device else it })
        }
    }

    suspend fun removeDevice(deviceId: String) {
        context.dataStore.edit { prefs ->
            val existing = parseDevices(prefs[KEY_DEVICES_JSON] ?: "[]")
            prefs[KEY_DEVICES_JSON] = serializeDevices(existing.filter { it.id != deviceId })
        }
    }

    /** Save map of deviceId -> last notified status to avoid duplicate notifications. */
    suspend fun saveNotifState(state: Map<String, String>) {
        context.dataStore.edit { prefs ->
            prefs[KEY_LAST_NOTIF_STATE] = JSONObject(state).toString()
        }
    }

    private fun parseDevices(json: String): List<UpsDevice> = try {
        val arr = JSONArray(json)
        (0 until arr.length()).map { UpsDevice.fromJson(arr.getJSONObject(it)) }
    } catch (_: Exception) { emptyList() }

    private fun serializeDevices(devices: List<UpsDevice>): String {
        val arr = JSONArray(); devices.forEach { arr.put(it.toJson()) }; return arr.toString()
    }
}
