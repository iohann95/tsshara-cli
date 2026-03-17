package com.tsshara.app

import android.app.Application
import com.tsshara.app.service.NotificationHelper

class TssharaApp : Application() {
    override fun onCreate() {
        super.onCreate()
        NotificationHelper.createNotificationChannel(this)
    }
}
