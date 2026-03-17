package com.tsshara.app.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.tsshara.app.data.PrefsManager
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking

/**
 * Receives BOOT_COMPLETED broadcast and re-schedules the UPS monitoring worker.
 */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            Log.i("BootReceiver", "Device booted, checking monitoring settings")
            try {
                val prefs = PrefsManager(context)
                val settings = runBlocking { prefs.settingsFlow.first() }

                if (settings.monitoringEnabled && settings.hasConfiguredDevices) {
                    UpsMonitorService.start(context)
                    Log.i("BootReceiver", "Started UPS monitoring service after boot")
                }
            } catch (e: Exception) {
                Log.e("BootReceiver", "Error re-scheduling after boot", e)
            }
        }
    }
}
