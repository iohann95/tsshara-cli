package com.tsshara.app.ui

import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.tsshara.app.R
import com.tsshara.app.data.PrefsManager
import com.tsshara.app.network.ApiClient
import com.tsshara.app.service.NotificationHelper
import com.tsshara.app.service.UpsMonitorWorker
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    settings: PrefsManager.AppSettings,
    prefsManager: PrefsManager,
    onLanguageChange: (String) -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var monitoringEnabled by remember(settings) { mutableStateOf(settings.monitoringEnabled) }
    var pollInterval by remember(settings) { mutableStateOf(settings.pollIntervalSeconds.toString()) }
    var language by remember(settings) { mutableStateOf(settings.language) }

    // Notification type toggles
    var notifPowerFail by remember(settings) { mutableStateOf(settings.notifPowerFail) }
    var notifBatteryLow by remember(settings) { mutableStateOf(settings.notifBatteryLow) }
    var notifUpsFailed by remember(settings) { mutableStateOf(settings.notifUpsFailed) }
    var notifPowerRestored by remember(settings) { mutableStateOf(settings.notifPowerRestored) }
    var notifConnectionError by remember(settings) { mutableStateOf(settings.notifConnectionError) }
    var notifConnectionRestored by remember(settings) { mutableStateOf(settings.notifConnectionRestored) }
    var notifTestRunning by remember(settings) { mutableStateOf(settings.notifTestRunning) }

    // Dialog state for add/edit device
    var showDeviceDialog by remember { mutableStateOf(false) }
    var editingDevice by remember { mutableStateOf<PrefsManager.UpsDevice?>(null) }
    var deleteConfirmDevice by remember { mutableStateOf<PrefsManager.UpsDevice?>(null) }

    Scaffold(
        topBar = {
            TopAppBar(title = { Text(stringResource(R.string.settings_title)) })
        }
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)
        ) {
            // ── Devices ──
            SectionHeader(stringResource(R.string.settings_devices))

            Text(
                text = stringResource(R.string.settings_devices_hint),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(bottom = 4.dp)
            )

            if (settings.devices.isEmpty()) {
                Text(
                    text = stringResource(R.string.settings_no_devices),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(vertical = 16.dp)
                )
            } else {
                settings.devices.forEach { device ->
                    DeviceListItem(
                        device = device,
                        onEdit = { editingDevice = device; showDeviceDialog = true },
                        onDelete = { deleteConfirmDevice = device }
                    )
                }
            }

            OutlinedButton(
                onClick = { editingDevice = null; showDeviceDialog = true },
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.Add, null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text(stringResource(R.string.settings_add_device))
            }

            HorizontalDivider()

            // ── Monitoring ──
            SectionHeader(stringResource(R.string.settings_monitoring))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(stringResource(R.string.settings_enable_monitoring))
                    Text(
                        stringResource(R.string.settings_enable_monitoring_desc),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Switch(checked = monitoringEnabled, onCheckedChange = { monitoringEnabled = it })
            }

            if (monitoringEnabled) {
                OutlinedTextField(
                    value = pollInterval,
                    onValueChange = { pollInterval = it.filter { c -> c.isDigit() } },
                    label = { Text(stringResource(R.string.settings_poll_interval)) },
                    placeholder = { Text(stringResource(R.string.settings_poll_interval_hint)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    leadingIcon = { Icon(Icons.Default.Timer, null) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number)
                )

                // ── Notification type toggles ──
                Spacer(modifier = Modifier.height(4.dp))
                Text(
                    text = stringResource(R.string.settings_notif_types),
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = stringResource(R.string.settings_notif_types_desc),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(bottom = 4.dp)
                )

                NotifToggleRow(stringResource(R.string.settings_notif_power_fail), notifPowerFail) { notifPowerFail = it }
                NotifToggleRow(stringResource(R.string.settings_notif_battery_low), notifBatteryLow) { notifBatteryLow = it }
                NotifToggleRow(stringResource(R.string.settings_notif_ups_failed), notifUpsFailed) { notifUpsFailed = it }
                NotifToggleRow(stringResource(R.string.settings_notif_power_restored), notifPowerRestored) { notifPowerRestored = it }
                NotifToggleRow(stringResource(R.string.settings_notif_connection_error), notifConnectionError) { notifConnectionError = it }
                NotifToggleRow(stringResource(R.string.settings_notif_connection_restored), notifConnectionRestored) { notifConnectionRestored = it }
                NotifToggleRow(stringResource(R.string.settings_notif_test_running), notifTestRunning) { notifTestRunning = it }
            }

            // Test notification
            OutlinedButton(
                onClick = {
                    NotificationHelper.sendTestNotification(context)
                    Toast.makeText(context, context.getString(R.string.settings_test_notification_sent), Toast.LENGTH_SHORT).show()
                },
                modifier = Modifier.fillMaxWidth()
            ) {
                Icon(Icons.Default.Notifications, null, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text(stringResource(R.string.settings_test_notification))
            }

            HorizontalDivider()

            // ── Language ──
            SectionHeader(stringResource(R.string.settings_language))

            LanguageSelector(
                selected = language,
                onSelect = { lang ->
                    language = lang
                    onLanguageChange(lang)
                }
            )

            Spacer(modifier = Modifier.height(8.dp))

            // ── Save ──
            Button(
                onClick = {
                    val intervalSec = pollInterval.toIntOrNull() ?: PrefsManager.DEFAULT_POLL_SECONDS
                    val newSettings = settings.copy(
                        monitoringEnabled = monitoringEnabled,
                        pollIntervalSeconds = intervalSec,
                        language = language,
                        notifPowerFail = notifPowerFail,
                        notifBatteryLow = notifBatteryLow,
                        notifUpsFailed = notifUpsFailed,
                        notifPowerRestored = notifPowerRestored,
                        notifConnectionError = notifConnectionError,
                        notifConnectionRestored = notifConnectionRestored,
                        notifTestRunning = notifTestRunning,
                    )
                    scope.launch { prefsManager.save(newSettings) }
                    onLanguageChange(language)

                    // Schedule or cancel monitoring
                    if (monitoringEnabled && newSettings.hasConfiguredDevices) {
                        UpsMonitorWorker.schedule(context, intervalSec)
                    } else {
                        UpsMonitorWorker.cancel(context)
                    }

                    Toast.makeText(context, context.getString(R.string.settings_saved), Toast.LENGTH_SHORT).show()
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(50.dp),
                shape = RoundedCornerShape(12.dp)
            ) {
                Icon(Icons.Default.Save, null, modifier = Modifier.size(20.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text(stringResource(R.string.settings_save), fontWeight = FontWeight.SemiBold)
            }

            Spacer(modifier = Modifier.height(80.dp))
        }
    }

    // ── Add/Edit device dialog ──
    if (showDeviceDialog) {
        DeviceDialog(
            device = editingDevice,
            onDismiss = { showDeviceDialog = false },
            onSave = { device ->
                scope.launch {
                    if (editingDevice != null) {
                        prefsManager.updateDevice(device)
                    } else {
                        prefsManager.addDevice(device)
                    }
                }
                showDeviceDialog = false
            }
        )
    }

    // ── Delete confirmation ──
    deleteConfirmDevice?.let { device ->
        AlertDialog(
            onDismissRequest = { deleteConfirmDevice = null },
            title = { Text(stringResource(R.string.settings_delete_device)) },
            text = { Text(stringResource(R.string.settings_delete_confirm, device.displayName)) },
            confirmButton = {
                TextButton(onClick = {
                    scope.launch { prefsManager.removeDevice(device.id) }
                    deleteConfirmDevice = null
                }) { Text(stringResource(R.string.delete), color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = {
                TextButton(onClick = { deleteConfirmDevice = null }) {
                    Text(stringResource(R.string.cancel))
                }
            }
        )
    }
}

@Composable
private fun DeviceListItem(
    device: PrefsManager.UpsDevice,
    onEdit: () -> Unit,
    onDelete: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f))
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Icon(Icons.Default.Dns, null, tint = MaterialTheme.colorScheme.primary)
            Spacer(modifier = Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = device.displayName,
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.Medium
                )
                Text(
                    text = device.baseUrl,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            IconButton(onClick = onEdit) {
                Icon(Icons.Default.Edit, contentDescription = stringResource(R.string.settings_edit_device))
            }
            IconButton(onClick = onDelete) {
                Icon(
                    Icons.Default.Delete,
                    contentDescription = stringResource(R.string.settings_delete_device),
                    tint = MaterialTheme.colorScheme.error
                )
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DeviceDialog(
    device: PrefsManager.UpsDevice?,
    onDismiss: () -> Unit,
    onSave: (PrefsManager.UpsDevice) -> Unit
) {
    val isEditing = device != null
    val initial = device ?: PrefsManager.UpsDevice()
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    var name by remember { mutableStateOf(initial.name) }
    var host by remember { mutableStateOf(initial.host) }
    var port by remember { mutableStateOf(initial.port) }
    var useHttps by remember { mutableStateOf(initial.useHttps) }
    var useAuth by remember { mutableStateOf(initial.username.isNotBlank()) }
    var username by remember { mutableStateOf(initial.username) }
    var password by remember { mutableStateOf(initial.password) }
    var passwordVisible by remember { mutableStateOf(false) }
    var isTesting by remember { mutableStateOf(false) }

    fun buildDevice() = initial.copy(
        name = name.trim(),
        host = host.trim(),
        port = port.trim().ifBlank { PrefsManager.DEFAULT_PORT },
        useHttps = useHttps,
        username = if (useAuth) username.trim() else "",
        password = if (useAuth) password else "",
    )

    AlertDialog(
        onDismissRequest = onDismiss,
        title = {
            Text(
                stringResource(
                    if (isEditing) R.string.settings_edit_device
                    else R.string.settings_add_device
                )
            )
        },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text(stringResource(R.string.settings_device_name)) },
                    placeholder = { Text(stringResource(R.string.settings_device_name_hint)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    leadingIcon = { Icon(Icons.Default.Edit, null) }
                )

                OutlinedTextField(
                    value = host,
                    onValueChange = { host = it },
                    label = { Text(stringResource(R.string.settings_server_host)) },
                    placeholder = { Text(stringResource(R.string.settings_server_host_hint)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    leadingIcon = { Icon(Icons.Default.Dns, null) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri)
                )

                OutlinedTextField(
                    value = port,
                    onValueChange = { port = it.filter { c -> c.isDigit() } },
                    label = { Text(stringResource(R.string.settings_server_port)) },
                    placeholder = { Text(stringResource(R.string.settings_server_port_hint)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    leadingIcon = { Icon(Icons.Default.Tag, null) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number)
                )

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(stringResource(R.string.settings_use_https))
                    Switch(checked = useHttps, onCheckedChange = { useHttps = it })
                }

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(stringResource(R.string.settings_use_auth))
                    Switch(checked = useAuth, onCheckedChange = { useAuth = it })
                }

                if (useAuth) {
                    OutlinedTextField(
                        value = username,
                        onValueChange = { username = it },
                        label = { Text(stringResource(R.string.settings_username)) },
                        placeholder = { Text(stringResource(R.string.settings_username_hint)) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        leadingIcon = { Icon(Icons.Default.Person, null) }
                    )

                    OutlinedTextField(
                        value = password,
                        onValueChange = { password = it },
                        label = { Text(stringResource(R.string.settings_password)) },
                        placeholder = { Text(stringResource(R.string.settings_password_hint)) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        leadingIcon = { Icon(Icons.Default.Lock, null) },
                        visualTransformation = if (passwordVisible) VisualTransformation.None else PasswordVisualTransformation(),
                        trailingIcon = {
                            IconButton(onClick = { passwordVisible = !passwordVisible }) {
                                Icon(
                                    if (passwordVisible) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                                    contentDescription = null
                                )
                            }
                        }
                    )
                }

                // Test connection
                OutlinedButton(
                    onClick = {
                        scope.launch {
                            isTesting = true
                            val result = withContext(Dispatchers.IO) {
                                ApiClient.getHealth(buildDevice())
                            }
                            isTesting = false
                            val msg = if (result.success)
                                context.getString(R.string.settings_test_success)
                            else
                                context.getString(R.string.settings_test_fail, result.error ?: "")
                            Toast.makeText(context, msg, Toast.LENGTH_LONG).show()
                        }
                    },
                    enabled = host.isNotBlank() && !isTesting,
                    modifier = Modifier.fillMaxWidth()
                ) {
                    if (isTesting) {
                        CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp)
                        Spacer(modifier = Modifier.width(8.dp))
                    }
                    Text(stringResource(R.string.settings_test_connection))
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onSave(buildDevice()) },
                enabled = host.isNotBlank()
            ) {
                Text(stringResource(R.string.save))
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text(stringResource(R.string.cancel))
            }
        }
    )
}

@Composable
private fun SectionHeader(title: String) {
    Text(
        text = title,
        style = MaterialTheme.typography.titleSmall,
        fontWeight = FontWeight.SemiBold,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(top = 4.dp)
    )
}

@Composable
private fun LanguageSelector(selected: String, onSelect: (String) -> Unit) {
    val options = listOf(
        PrefsManager.LANG_SYSTEM to stringResource(R.string.settings_language_system),
        PrefsManager.LANG_EN to stringResource(R.string.settings_language_en),
        PrefsManager.LANG_PT_BR to stringResource(R.string.settings_language_pt_br),
    )

    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        options.forEach { (value, label) ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 2.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                RadioButton(
                    selected = selected == value,
                    onClick = { onSelect(value) }
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(label, style = MaterialTheme.typography.bodyLarge)
            }
        }
    }
}

@Composable
private fun NotifToggleRow(label: String, checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 2.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(1f)
        )
        Checkbox(checked = checked, onCheckedChange = onCheckedChange)
    }
}
