package com.tsshara.app.service

import android.content.Context
import android.util.Log
import androidx.work.*
import com.tsshara.app.R
import com.tsshara.app.data.PrefsManager
import com.tsshara.app.network.ApiClient
import kotlinx.coroutines.flow.first
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * WorkManager worker that polls all configured UPS devices and sends
 * notifications only when status **changes** and is **stable** (no spam).
 *
 * Uses OneTimeWorkRequest with initial delay to support intervals < 15 min
 * (WorkManager PeriodicWorkRequest minimum is 15 min).
 * Re-schedules itself after each run.
 *
 * ## Debounce logic
 * Notifications are only sent after a status has been observed for N
 * consecutive polls (the "threshold"). This prevents notification spam
 * from flapping connections (e.g. NAT hairpin, unstable Wi-Fi).
 *
 * State format per device: `currentStatus|consecutiveCount|lastNotifiedStatus`
 */
class UpsMonitorWorker(
    context: Context,
    params: WorkerParameters
) : CoroutineWorker(context, params) {

    companion object {
        private const val TAG = "UpsMonitorWorker"
        const val UNIQUE_WORK_NAME = "ups_monitor"

        // Debounce thresholds (consecutive polls required before notifying)
        private const val THRESHOLD_CONNECTION_ERROR = 3  // ~15-90s depending on poll interval
        private const val THRESHOLD_RECOVERY = 2          // 2 consecutive OK before "restored"
        private const val THRESHOLD_POST_TEST = 3         // after TEST ends, need 3 polls to confirm real POWER_FAIL
        private const val THRESHOLD_IMMEDIATE = 1          // UPS events: instant

        /**
         * Start UPS monitoring as a foreground service.
         * The service shows a persistent notification and polls reliably
         * even when the app is closed/killed.
         */
        fun schedule(context: Context, intervalSeconds: Int) {
            // Cancel any legacy WorkManager chain
            WorkManager.getInstance(context).cancelUniqueWork(UNIQUE_WORK_NAME)
            // Start the foreground service
            UpsMonitorService.start(context)
            Log.i(TAG, "Started monitoring service (interval handled by service)")
        }

        /**
         * Stop UPS monitoring.
         */
        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(UNIQUE_WORK_NAME)
            UpsMonitorService.stop(context)
            Log.i(TAG, "Stopped monitoring service")
        }

        /**
         * How many consecutive polls in [newStatus] are needed before
         * sending a notification, considering what was last notified.
         */
        private fun notifyThreshold(newStatus: String, lastNotified: String?, prevStatus: String?): Int = when {
            newStatus == "CONNECTION_ERROR" -> THRESHOLD_CONNECTION_ERROR
            newStatus == "API_NO_DATA" -> Int.MAX_VALUE  // never notify for this
            // During/after a TEST the UPS reports utility_fail → suppress POWER_FAIL
            newStatus.contains("POWER_FAIL") && (
                lastNotified == "TEST" || prevStatus == "TEST"
            ) -> THRESHOLD_POST_TEST
            // Recovering from connection error → wait a bit to confirm stability
            lastNotified == "CONNECTION_ERROR"
                    && !newStatus.contains("POWER_FAIL")
                    && !newStatus.contains("BATTERY_LOW") -> THRESHOLD_RECOVERY
            else -> THRESHOLD_IMMEDIATE
        }
    }

    // ── Per-device debounce state ──────────────────────────────────────────

    /**
     * Tracks notification debounce state per device.
     * Serialized as `currentStatus|consecutiveCount|lastNotifiedStatus`.
     */
    private data class DeviceNotifState(
        val currentStatus: String,
        val consecutiveCount: Int,
        val lastNotifiedStatus: String?
    ) {
        fun serialize(): String =
            "$currentStatus|$consecutiveCount|${lastNotifiedStatus ?: ""}"

        companion object {
            fun parse(raw: String): DeviceNotifState {
                if ("|" !in raw) {
                    // Legacy format (pre-debounce): just "STATUS".
                    // Treat as already stable & notified so we don't re-send.
                    return DeviceNotifState(raw, 99, raw)
                }
                val parts = raw.split("|", limit = 3)
                return DeviceNotifState(
                    currentStatus = parts[0],
                    consecutiveCount = parts.getOrElse(1) { "1" }.toIntOrNull() ?: 1,
                    lastNotifiedStatus = parts.getOrElse(2) { "" }.ifBlank { null }
                )
            }
        }
    }

    // ── Main work ──────────────────────────────────────────────────────────

    override suspend fun doWork(): Result {
        val prefs = PrefsManager(applicationContext)
        val settings = prefs.settingsFlow.first()

        try {
            if (!settings.hasConfiguredDevices) {
                Log.w(TAG, "No configured devices, skipping")
                return Result.success()
            }

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
                    // httpCode == 0 → connection failed (IOException)
                    // httpCode > 0  → server responded but API returned error
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
                    // Status changed → reset counter
                    DeviceNotifState(polledStatus, 1, prev?.lastNotifiedStatus)
                } else {
                    // Same status → increment (cap at 999)
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
                                    applicationContext.getString(R.string.notif_connection_error_title),
                                    applicationContext.getString(R.string.notif_connection_error_body, deviceName),
                                    notificationId = baseNotifId + 9
                                )
                            }
                            finalNotifiedStatus = polledStatus
                        }

                        "POWER_FAIL" -> {
                            if (settings.notifPowerFail) {
                                NotificationHelper.sendNotification(
                                    applicationContext,
                                    applicationContext.getString(R.string.notif_power_fail_title),
                                    applicationContext.getString(R.string.notif_power_fail_body, deviceName),
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
                                    applicationContext.getString(R.string.notif_battery_low_title),
                                    applicationContext.getString(
                                        R.string.notif_battery_low_body,
                                        deviceName,
                                        battery.toFloat()
                                    ),
                                    notificationId = baseNotifId + 2
                                )
                            }
                            finalNotifiedStatus = polledStatus
                        }

                        "TEST" -> {
                            if (settings.notifTestRunning) {
                                NotificationHelper.sendNotification(
                                    applicationContext,
                                    applicationContext.getString(R.string.notif_test_running_title),
                                    applicationContext.getString(R.string.notif_test_running_body, deviceName),
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
                                        // Network/server connection restored (not a power event)
                                        NotificationHelper.sendNotification(
                                            applicationContext,
                                            applicationContext.getString(R.string.notif_connection_restored_title),
                                            applicationContext.getString(R.string.notif_connection_restored_body, deviceName),
                                            notificationId = baseNotifId + 8
                                        )
                                    }
                                }
                                prevNotified != null
                                        && prevNotified != "OK"
                                        && prevNotified != "API_NO_DATA" -> {
                                    if (settings.notifPowerRestored) {
                                        // Power / UPS issue resolved
                                        NotificationHelper.sendNotification(
                                            applicationContext,
                                            applicationContext.getString(R.string.notif_power_restored_title),
                                            applicationContext.getString(R.string.notif_power_restored_body, deviceName),
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
                            applicationContext.getString(R.string.notif_ups_failed_title),
                            applicationContext.getString(R.string.notif_ups_failed_body, deviceName),
                            notificationId = baseNotifId + 3
                        )
                    }
                }

                stateMap[device.id] =
                    updated.copy(lastNotifiedStatus = finalNotifiedStatus).serialize()
            }

            // Persist new notification state
            prefs.saveNotifState(stateMap)

        } catch (e: Exception) {
            Log.e(TAG, "Worker failed", e)
        }

        // Re-schedule next run (self-chaining for seconds-based intervals)
        if (settings.monitoringEnabled && settings.hasConfiguredDevices) {
            schedule(applicationContext, settings.pollIntervalSeconds)
        }

        return Result.success()
    }
}
