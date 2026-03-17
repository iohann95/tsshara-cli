package com.tsshara.app.service

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.tsshara.app.MainActivity
import com.tsshara.app.R

/**
 * Handles creating notification channels and posting notifications.
 */
object NotificationHelper {

    const val CHANNEL_ID = "tsshara_ups_alerts"
    const val CHANNEL_ID_MONITOR = "tsshara_monitor"
    private const val NOTIFICATION_ID_BASE = 1000

    fun createNotificationChannel(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)

        // High-importance channel for alert notifications (power fail, battery low, etc.)
        val alertChannel = NotificationChannel(
            CHANNEL_ID,
            context.getString(R.string.notif_channel_name),
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = context.getString(R.string.notif_channel_desc)
            enableVibration(true)
            enableLights(true)
        }
        manager.createNotificationChannel(alertChannel)

        // Low-importance channel for the persistent monitoring notification (silent)
        val monitorChannel = NotificationChannel(
            CHANNEL_ID_MONITOR,
            context.getString(R.string.notif_monitor_channel_name),
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = context.getString(R.string.notif_monitor_channel_desc)
            enableVibration(false)
            enableLights(false)
            setShowBadge(false)
        }
        manager.createNotificationChannel(monitorChannel)
    }

    fun sendNotification(
        context: Context,
        title: String,
        body: String,
        notificationId: Int = NOTIFICATION_ID_BASE
    ) {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
        }
        val pendingIntent = PendingIntent.getActivity(
            context, 0, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build()

        val manager = context.getSystemService(NotificationManager::class.java)
        manager.notify(notificationId, notification)
    }

    fun sendTestNotification(context: Context) {
        sendNotification(
            context,
            context.getString(R.string.notif_test_title),
            context.getString(R.string.notif_test_body),
            notificationId = NOTIFICATION_ID_BASE + 99
        )
    }
}
