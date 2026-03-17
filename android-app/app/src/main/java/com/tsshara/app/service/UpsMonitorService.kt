package com.tsshara.app.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.tsshara.app.R
import com.tsshara.app.data.PrefsManager
import com.tsshara.app.network.ApiClient
import com.tsshara.app.network.NetworkMonitor
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import org.json.JSONObject

/**
 * Persistent foreground service for UPS monitoring.
 *
 * Shows a low-priority ongoing notification to keep the process alive,
 * ensuring reliable network access even when the app is closed/killed.
 * Polls all configured UPS devices on a timer and sends alert
 * notifications on status changes (with debounce).
 *
 * ## Why a foreground service?
 * Android aggressively restricts background network access after the app
 * is removed from recents. WorkManager self-chaining OneTimeWorkRequests
 * get throttled and their network requests fail, causing false
 * "connection error" notifications. A foreground service guarantees
 * uninterrupted network access.
 */
class UpsMonitorService : Service() {

    companion object {
        private const val TAG = "UpsMonitorService"
        private const val FOREGROUND_NOTIF_ID = 900

        // Debounce thresholds (consecutive polls required before notifying)
        private const val THRESHOLD_CONNECTION_ERROR = 3
        private const val THRESHOLD_RECOVERY = 2
        private const val THRESHOLD_POST_TEST = 3
        private const val THRESHOLD_IMMEDIATE = 1

        fun start(context: Context) {
            val intent = Intent(context, UpsMonitorService::class.java)
            context.startForegroundService(intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, UpsMonitorService::class.java))
        }

        private fun notifyThreshold(newStatus: String, lastNotified: String?, prevStatus: String?): Int = when {
            newStatus == "CONNECTION_ERROR" -> THRESHOLD_CONNECTION_ERROR
            newStatus == "API_NO_DATA" -> Int.MAX_VALUE
            // During/after a TEST the UPS reports utility_fail → suppress POWER_FAIL
            newStatus.contains("POWER_FAIL") && (
                lastNotified == "TEST" || prevStatus == "TEST"
            ) -> THRESHOLD_POST_TEST
            lastNotified == "CONNECTION_ERROR"
                    && !newStatus.contains("POWER_FAIL")
                    && !newStatus.contains("BATTERY_LOW") -> THRESHOLD_RECOVERY
            else -> THRESHOLD_IMMEDIATE
        }
    }

    // ── Per-device debounce state ──────────────────────────────────────────

    private data class DeviceNotifState(
        val currentStatus: String,
        val consecutiveCount: Int,
        val lastNotifiedStatus: String?
    ) {
        fun serialize(): String =
            "$currentStatus|$consecutiveCount|${lastNotifiedStatus ?: ""}"

        companion object {
            fun parse(raw: String): DeviceNotifState {
                if ("|" !in raw) return DeviceNotifState(raw, 99, raw)
                val parts = raw.split("|", limit = 3)
                return DeviceNotifState(
                    currentStatus = parts[0],
                    consecutiveCount = parts.getOrElse(1) { "1" }.toIntOrNull() ?: 1,
                    lastNotifiedStatus = parts.getOrElse(2) { "" }.ifBlank { null }
                )
            }
        }
    }

    // ── Service lifecycle ──────────────────────────────────────────────────

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var prefs: PrefsManager
    private lateinit var networkMonitor: NetworkMonitor

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        prefs = PrefsManager(applicationContext)
        networkMonitor = NetworkMonitor(applicationContext)
        networkMonitor.register()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        promoteToForeground()
        startPolling()
        return START_STICKY
    }

    override fun onDestroy() {
        serviceScope.cancel()
        networkMonitor.unregister()
        Log.i(TAG, "Service destroyed")
        super.onDestroy()
    }

    private fun promoteToForeground() {
        val notification = NotificationCompat.Builder(this, NotificationHelper.CHANNEL_ID_MONITOR)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(getString(R.string.notif_monitor_title))
            .setContentText(getString(R.string.notif_monitor_body))
            .setOngoing(true)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()

        ServiceCompat.startForeground(
            this,
            FOREGROUND_NOTIF_ID,
            notification,
            ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        )
    }

    // ── Polling loop ───────────────────────────────────────────────────────

    private fun startPolling() {
        // Cancel any previous loop (e.g. if onStartCommand called again)
        serviceScope.coroutineContext.cancelChildren()

        serviceScope.launch {
            Log.i(TAG, "Polling loop started")
            while (isActive) {
                val settings = prefs.settingsFlow.first()

                if (!settings.monitoringEnabled || !settings.hasConfiguredDevices) {
                    Log.i(TAG, "Monitoring disabled or no devices, stopping service")
                    stopSelf()
                    return@launch
                }

                if (!networkMonitor.isInternetAvailable) {
                    Log.d(TAG, "No internet — skipping poll cycle")
                } else {
                    try {
                        pollDevices(settings)
                    } catch (e: Exception) {
                        Log.e(TAG, "Poll cycle failed", e)
                    }
                }

                delay(settings.pollIntervalSeconds * 1000L)
            }
        }
    }

    // ── Poll all devices ───────────────────────────────────────────────────

    private suspend fun pollDevices(settings: PrefsManager.AppSettings) {
        val stateMap = mutableMapOf<String, String>()

        for (device in settings.devices) {
            if (!device.isConfigured) continue

            val result = ApiClient.getStatus(device)
            val deviceName = device.displayName
            val deviceIdx = settings.devices.indexOf(device)
            val baseNotifId = 1000 + deviceIdx * 10

            // ── Determine polled status ─────────────────────────────
            val polledStatus: String
            val responseData: JSONObject?

            if (!result.success) {
                // httpCode 0 = I/O failure.  Before blaming the server,
                // re-check connectivity: if the phone has no validated
                // internet the failure is a network issue, not a server one.
                if (result.httpCode == 0 && !networkMonitor.checkNow()) {
                    Log.d(TAG, "${device.displayName}: HTTP failed but no internet — skipping")
                    // Preserve previous debounce state unchanged for this device
                    val prevRaw = settings.lastNotifState[device.id]
                    if (prevRaw != null) stateMap[device.id] = prevRaw
                    continue
                }
                polledStatus = if (result.httpCode == 0) "CONNECTION_ERROR" else "API_NO_DATA"
                responseData = null
            } else {
                responseData = result.data?.optJSONObject("data")
                if (responseData == null) {
                    polledStatus = "API_NO_DATA"
                } else {
                    val statusText = responseData.optString("status_text", "OK")
                    val upsFailed = responseData.optBoolean("ups_failed", false)
                    polledStatus = if (upsFailed) "${statusText}+UPS_FAILED" else statusText
                }
            }

            // ── Update debounce state ───────────────────────────────
            val prevRaw = settings.lastNotifState[device.id]
            val prev = prevRaw?.let { DeviceNotifState.parse(it) }

            val updated = if (prev == null || polledStatus != prev.currentStatus) {
                DeviceNotifState(polledStatus, 1, prev?.lastNotifiedStatus)
            } else {
                prev.copy(consecutiveCount = (prev.consecutiveCount + 1).coerceAtMost(999))
            }

            // ── Check notification threshold ────────────────────────
            val threshold = notifyThreshold(polledStatus, updated.lastNotifiedStatus, prev?.currentStatus)
            val shouldNotify = updated.consecutiveCount >= threshold
                    && polledStatus != updated.lastNotifiedStatus

            var finalNotifiedStatus = updated.lastNotifiedStatus

            if (shouldNotify) {
                val baseStatus = polledStatus.split("+").first()

                when (baseStatus) {
                    "CONNECTION_ERROR" -> {
                        if (settings.notifConnectionError) {
                            NotificationHelper.sendNotification(
                                applicationContext,
                                getString(R.string.notif_connection_error_title),
                                getString(R.string.notif_connection_error_body, deviceName),
                                notificationId = baseNotifId + 9
                            )
                        }
                        finalNotifiedStatus = polledStatus
                    }

                    "POWER_FAIL" -> {
                        if (settings.notifPowerFail) {
                            NotificationHelper.sendNotification(
                                applicationContext,
                                getString(R.string.notif_power_fail_title),
                                getString(R.string.notif_power_fail_body, deviceName),
                                notificationId = baseNotifId + 1
                            )
                        }
                        finalNotifiedStatus = polledStatus
                    }

                    "BATTERY_LOW" -> {
                        if (settings.notifBatteryLow) {
                            val battery = responseData?.optDouble("battery", 0.0) ?: 0.0
                            NotificationHelper.sendNotification(
                                applicationContext,
                                getString(R.string.notif_battery_low_title),
                                getString(R.string.notif_battery_low_body, deviceName, battery.toFloat()),
                                notificationId = baseNotifId + 2
                            )
                        }
                        finalNotifiedStatus = polledStatus
                    }

                    "TEST" -> {
                        if (settings.notifTestRunning) {
                            NotificationHelper.sendNotification(
                                applicationContext,
                                getString(R.string.notif_test_running_title),
                                getString(R.string.notif_test_running_body, deviceName),
                                notificationId = baseNotifId + 5
                            )
                        }
                        finalNotifiedStatus = polledStatus
                    }

                    "OK" -> {
                        val prevNotified = updated.lastNotifiedStatus
                        when {
                            prevNotified == "CONNECTION_ERROR" -> {
                                if (settings.notifConnectionRestored) {
                                    NotificationHelper.sendNotification(
                                        applicationContext,
                                        getString(R.string.notif_connection_restored_title),
                                        getString(R.string.notif_connection_restored_body, deviceName),
                                        notificationId = baseNotifId + 8
                                    )
                                }
                            }
                            prevNotified != null
                                    && prevNotified != "OK"
                                    && prevNotified != "API_NO_DATA" -> {
                                if (settings.notifPowerRestored) {
                                    NotificationHelper.sendNotification(
                                        applicationContext,
                                        getString(R.string.notif_power_restored_title),
                                        getString(R.string.notif_power_restored_body, deviceName),
                                        notificationId = baseNotifId + 4
                                    )
                                }
                            }
                        }
                        finalNotifiedStatus = polledStatus
                    }

                    // API_NO_DATA: no notification (threshold = MAX_VALUE)
                }

                // UPS_FAILED flag — separate notification track
                if (polledStatus.contains("UPS_FAILED")
                    && updated.lastNotifiedStatus?.contains("UPS_FAILED") != true
                    && settings.notifUpsFailed
                ) {
                    NotificationHelper.sendNotification(
                        applicationContext,
                        getString(R.string.notif_ups_failed_title),
                        getString(R.string.notif_ups_failed_body, deviceName),
                        notificationId = baseNotifId + 3
                    )
                }
            }

            stateMap[device.id] =
                updated.copy(lastNotifiedStatus = finalNotifiedStatus).serialize()
        }

        // Persist new notification state
        prefs.saveNotifState(stateMap)
    }
}
