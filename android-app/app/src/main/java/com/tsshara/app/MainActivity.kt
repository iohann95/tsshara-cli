package com.tsshara.app

import android.Manifest
import android.app.LocaleManager
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.LocaleList
import kotlinx.coroutines.runBlocking
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Monitor
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.lifecycleScope
import com.tsshara.app.data.PrefsManager
import com.tsshara.app.service.UpsMonitorWorker
import com.tsshara.app.ui.AboutScreen
import com.tsshara.app.ui.SettingsScreen
import com.tsshara.app.ui.StatusScreen
import com.tsshara.app.ui.theme.TssharaTheme
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private lateinit var prefsManager: PrefsManager

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* granted or not, the app continues */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        prefsManager = PrefsManager(this)

        // Request POST_NOTIFICATIONS permission (Android 13+)
        requestNotificationPermission()

        // Apply saved language preference
        lifecycleScope.launch {
            val settings = prefsManager.settingsFlow.first()
            applyLanguage(settings.language)

            // Re-schedule monitoring if enabled
            if (settings.monitoringEnabled && settings.hasConfiguredDevices) {
                UpsMonitorWorker.schedule(this@MainActivity, settings.pollIntervalSeconds)
            }
        }

        setContent {
            TssharaTheme {
                TssharaMainContent(
                    prefsManager = prefsManager,
                    onLanguageChange = { lang -> applyLanguage(lang) }
                )
            }
        }
    }

    private fun requestNotificationPermission() {
        if (checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    private fun applyLanguage(language: String) {
        // Persist language BEFORE LocaleManager triggers activity recreation
        runBlocking { prefsManager.saveLanguage(language) }

        val localeManager = getSystemService(LocaleManager::class.java)
        when (language) {
            PrefsManager.LANG_EN -> localeManager.applicationLocales = LocaleList.forLanguageTags("en")
            PrefsManager.LANG_PT_BR -> localeManager.applicationLocales = LocaleList.forLanguageTags("pt-BR")
            else -> localeManager.applicationLocales = LocaleList.getEmptyLocaleList()
        }
    }
}

@Composable
fun TssharaMainContent(prefsManager: PrefsManager, onLanguageChange: (String) -> Unit) {
    var selectedTab by remember { mutableIntStateOf(0) }
    val settings by prefsManager.settingsFlow.collectAsState(initial = PrefsManager.AppSettings())

    Scaffold(
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    selected = selectedTab == 0,
                    onClick = { selectedTab = 0 },
                    icon = { Icon(Icons.Default.Monitor, contentDescription = null) },
                    label = { Text(stringResource(R.string.nav_status)) }
                )
                NavigationBarItem(
                    selected = selectedTab == 1,
                    onClick = { selectedTab = 1 },
                    icon = { Icon(Icons.Default.Settings, contentDescription = null) },
                    label = { Text(stringResource(R.string.nav_settings)) }
                )
                NavigationBarItem(
                    selected = selectedTab == 2,
                    onClick = { selectedTab = 2 },
                    icon = { Icon(Icons.Default.Info, contentDescription = null) },
                    label = { Text(stringResource(R.string.nav_about)) }
                )
            }
        }
    ) { innerPadding ->
        Box(modifier = Modifier.padding(innerPadding)) {
            when (selectedTab) {
                0 -> StatusScreen(settings = settings)
                1 -> SettingsScreen(
                    settings = settings,
                    prefsManager = prefsManager,
                    onLanguageChange = onLanguageChange,
                )
                2 -> AboutScreen()
            }
        }
    }
}
