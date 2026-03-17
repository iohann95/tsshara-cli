package com.tsshara.app.ui

import android.widget.Toast
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.*
import androidx.compose.material3.MenuAnchorType
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.tsshara.app.R
import com.tsshara.app.data.PrefsManager
import com.tsshara.app.network.ApiClient
import com.tsshara.app.ui.theme.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun StatusScreen(settings: PrefsManager.AppSettings) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val devices = settings.devices.filter { it.isConfigured }

    var selectedDeviceIndex by remember { mutableIntStateOf(0) }
    var isLoading by remember { mutableStateOf(false) }
    var statusData by remember { mutableStateOf<JSONObject?>(null) }
    var healthData by remember { mutableStateOf<JSONObject?>(null) }
    var infoData by remember { mutableStateOf<JSONObject?>(null) }
    var errorMsg by remember { mutableStateOf<String?>(null) }
    var errorIsConnection by remember { mutableStateOf(true) }
    var lastUpdate by remember { mutableStateOf("") }

    val selectedDevice = devices.getOrNull(selectedDeviceIndex)

    fun refresh() {
        val device = selectedDevice ?: return
        scope.launch {
            isLoading = true
            errorMsg = null
            withContext(Dispatchers.IO) {
                // Fire all 3 requests in parallel instead of sequentially
                val statusDeferred = async { ApiClient.getStatus(device) }
                val healthDeferred = async { ApiClient.getHealth(device) }
                val infoDeferred = async { ApiClient.getInfo(device) }

                val statusResult = statusDeferred.await()
                val healthResult = healthDeferred.await()
                val infoResult = infoDeferred.await()

                withContext(Dispatchers.Main) {
                    if (statusResult.success) {
                        statusData = statusResult.data?.optJSONObject("data")
                        lastUpdate = statusResult.data?.optString("timestamp", "") ?: ""
                    } else {
                        errorMsg = statusResult.error
                        errorIsConnection = statusResult.httpCode == 0
                    }
                    if (healthResult.success) healthData = healthResult.data
                    if (infoResult.success) infoData = infoResult.data
                    isLoading = false
                }
            }
        }
    }

    // Auto-refresh when selected device changes
    LaunchedEffect(selectedDevice?.id) {
        statusData = null; healthData = null; infoData = null; errorMsg = null; errorIsConnection = true
        if (selectedDevice != null) refresh()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.status_title)) },
                actions = {
                    if (selectedDevice != null) {
                        IconButton(onClick = { refresh() }, enabled = !isLoading) {
                            Icon(Icons.Default.Refresh, contentDescription = stringResource(R.string.status_refresh))
                        }
                    }
                }
            )
        }
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
        ) {
            if (devices.isEmpty()) {
                NotConfiguredMessage()
            } else {
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .verticalScroll(rememberScrollState())
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    // Device selector (only show if multiple devices)
                    if (devices.size > 1) {
                        DeviceSelector(
                            devices = devices,
                            selectedIndex = selectedDeviceIndex,
                            onSelect = { selectedDeviceIndex = it }
                        )
                    }

                    if (isLoading && statusData == null) {
                        Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator()
                        }
                    } else if (errorMsg != null && statusData == null) {
                        ErrorMessage(errorMsg!!, isConnectionError = errorIsConnection, onRetry = { refresh() })
                    } else {
                        // Loading indicator on top for refreshes
                        if (isLoading) {
                            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
                        }

                        statusData?.let { data ->
                            StatusBadge(data)
                            Spacer(modifier = Modifier.height(4.dp))
                            VoltageCard(data)
                            BatteryCard(data)
                            FlagsCard(data)
                        }

                        healthData?.let { data ->
                            HealthCard(data)
                        }

                        infoData?.let { data ->
                            InfoCard(data)
                        }

                        // Test commands
                        selectedDevice?.let { device ->
                            TestsCard(device)
                            BeepCard(device, statusData)
                        }

                        if (lastUpdate.isNotBlank()) {
                            Text(
                                text = stringResource(R.string.status_last_update, lastUpdate.take(19)),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.fillMaxWidth(),
                                textAlign = TextAlign.Center
                            )
                        }
                    }

                    Spacer(modifier = Modifier.height(80.dp))
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DeviceSelector(
    devices: List<PrefsManager.UpsDevice>,
    selectedIndex: Int,
    onSelect: (Int) -> Unit
) {
    var expanded by remember { mutableStateOf(false) }
    val selected = devices.getOrNull(selectedIndex)

    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = it }
    ) {
        OutlinedTextField(
            value = selected?.displayName ?: "",
            onValueChange = {},
            readOnly = true,
            label = { Text(stringResource(R.string.status_select_device)) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
            modifier = Modifier
                .fillMaxWidth()
                .menuAnchor(MenuAnchorType.PrimaryNotEditable),
            colors = ExposedDropdownMenuDefaults.outlinedTextFieldColors()
        )
        ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            devices.forEachIndexed { index, device ->
                DropdownMenuItem(
                    text = { Text(device.displayName) },
                    onClick = {
                        onSelect(index)
                        expanded = false
                    }
                )
            }
        }
    }
}

@Composable
private fun TestsCard(device: PrefsManager.UpsDevice) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var runningTest by remember { mutableStateOf<String?>(null) }
    var showConfirm by remember { mutableStateOf<Pair<String, String>?>(null) } // testType to label

    val tests = listOf(
        "10s" to stringResource(R.string.test_10s),
        "low" to stringResource(R.string.test_low),
    )

    fun executeTest(testType: String) {
        scope.launch {
            runningTest = testType
            val result = withContext(Dispatchers.IO) {
                ApiClient.runTest(device, testType)
            }
            runningTest = null
            val msg = if (result.success)
                context.getString(R.string.test_sent)
            else
                context.getString(R.string.test_failed, result.error ?: "")
            Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
        }
    }

    // Confirmation dialog
    showConfirm?.let { (testType, label) ->
        AlertDialog(
            onDismissRequest = { showConfirm = null },
            title = { Text(stringResource(R.string.test_confirm_title)) },
            text = { Text(stringResource(R.string.test_confirm_message, label, device.displayName)) },
            confirmButton = {
                TextButton(onClick = {
                    showConfirm = null
                    executeTest(testType)
                }) { Text(stringResource(R.string.ok)) }
            },
            dismissButton = {
                TextButton(onClick = { showConfirm = null }) {
                    Text(stringResource(R.string.cancel))
                }
            }
        )
    }

    SectionCard(stringResource(R.string.tests_title)) {
        TestButtonGrid(tests, runningTest) { type, label -> showConfirm = type to label }
    }
}

@Composable
private fun BeepCard(device: PrefsManager.UpsDevice, statusData: JSONObject?) {
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    var isRunning by remember { mutableStateOf(false) }
    var showConfirm by remember { mutableStateOf(false) }

    val beepOn = statusData?.optBoolean("beep_on", false) ?: false
    val beepLabel = if (beepOn)
        stringResource(R.string.beep_disable)
    else
        stringResource(R.string.beep_enable)

    fun executeToggle() {
        scope.launch {
            isRunning = true
            val result = withContext(Dispatchers.IO) {
                ApiClient.runTest(device, "beep")
            }
            isRunning = false
            val msg = if (result.success)
                context.getString(R.string.test_sent)
            else
                context.getString(R.string.test_failed, result.error ?: "")
            Toast.makeText(context, msg, Toast.LENGTH_SHORT).show()
        }
    }

    if (showConfirm) {
        AlertDialog(
            onDismissRequest = { showConfirm = false },
            title = { Text(stringResource(R.string.test_confirm_title)) },
            text = { Text(stringResource(R.string.test_confirm_message, beepLabel, device.displayName)) },
            confirmButton = {
                TextButton(onClick = {
                    showConfirm = false
                    executeToggle()
                }) { Text(stringResource(R.string.ok)) }
            },
            dismissButton = {
                TextButton(onClick = { showConfirm = false }) {
                    Text(stringResource(R.string.cancel))
                }
            }
        )
    }

    SectionCard("Beep") {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Column {
                Text(
                    text = stringResource(R.string.beep_status),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Text(
                    text = if (beepOn) stringResource(R.string.beep_on) else stringResource(R.string.beep_off),
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.Medium,
                    color = if (beepOn) Green500 else MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            OutlinedButton(
                onClick = { showConfirm = true },
                enabled = !isRunning
            ) {
                if (isRunning) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(16.dp),
                        strokeWidth = 2.dp
                    )
                } else {
                    Text(beepLabel, fontSize = 13.sp)
                }
            }
        }
    }
}

@Composable
private fun TestButtonGrid(
    items: List<Pair<String, String>>,
    runningTest: String?,
    onConfirm: (type: String, label: String) -> Unit
) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        items.chunked(2).forEach { row ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                row.forEach { (type, label) ->
                    OutlinedButton(
                        onClick = { onConfirm(type, label) },
                        enabled = runningTest == null,
                        modifier = Modifier.weight(1f),
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 8.dp)
                    ) {
                        if (runningTest == type) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 2.dp
                            )
                        } else {
                            Icon(
                                Icons.Default.PlayArrow,
                                contentDescription = null,
                                modifier = Modifier.size(16.dp)
                            )
                        }
                        Spacer(modifier = Modifier.width(4.dp))
                        Text(label, fontSize = 13.sp)
                    }
                }
                if (row.size < 2) Spacer(modifier = Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun StatusBadge(data: JSONObject) {
    val statusText = data.optString("status_text", "UNKNOWN")
    val (label, color) = when (statusText) {
        "OK" -> stringResource(R.string.status_ok) to Green500
        "POWER_FAIL" -> stringResource(R.string.status_power_fail) to Red500
        "BATTERY_LOW" -> stringResource(R.string.status_battery_low) to Orange500
        "TEST" -> stringResource(R.string.status_test) to Blue500
        else -> stringResource(R.string.status_unknown) to Gray400
    }
    val animatedColor by animateColorAsState(targetValue = color, label = "statusColor")

    Card(
        modifier = Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = animatedColor.copy(alpha = 0.12f)),
        shape = RoundedCornerShape(16.dp)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(20.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center
        ) {
            Box(
                modifier = Modifier
                    .size(16.dp)
                    .clip(CircleShape)
                    .background(animatedColor)
            )
            Spacer(modifier = Modifier.width(12.dp))
            Text(
                text = label,
                fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
                color = animatedColor
            )
        }
    }
}

@Composable
private fun VoltageCard(data: JSONObject) {
    SectionCard(stringResource(R.string.field_input_voltage).substringBefore(" ")) {
        DataRow(stringResource(R.string.field_input_voltage),
            stringResource(R.string.unit_volts, data.optDouble("input_voltage", 0.0).toFloat()))
        DataRow(stringResource(R.string.field_output_voltage),
            stringResource(R.string.unit_volts, data.optDouble("output_voltage", 0.0).toFloat()))
        DataRow(stringResource(R.string.field_input_fault_voltage),
            stringResource(R.string.unit_volts, data.optDouble("input_fault_voltage", 0.0).toFloat()))
        DataRow(stringResource(R.string.field_frequency),
            stringResource(R.string.unit_hz, data.optDouble("frequency", 0.0).toFloat()))
        DataRow(stringResource(R.string.field_current),
            stringResource(R.string.unit_amps, data.optDouble("current", 0.0).toFloat()))
    }
}

@Composable
private fun BatteryCard(data: JSONObject) {
    val batteryVoltage = data.optDouble("battery", 0.0)
    val temperature = data.optDouble("temperature", 0.0)

    SectionCard(stringResource(R.string.field_battery)) {
        DataRow(stringResource(R.string.field_battery),
            stringResource(R.string.unit_volts, batteryVoltage.toFloat()))
        DataRow(stringResource(R.string.field_temperature),
            stringResource(R.string.unit_celsius, temperature.toFloat()))
    }
}

@Composable
private fun FlagsCard(data: JSONObject) {
    SectionCard("Flags") {
        FlagRow(stringResource(R.string.field_utility_fail), data.optBoolean("utility_fail", false), isAlert = true)
        FlagRow(stringResource(R.string.field_battery_low), data.optBoolean("battery_low", false), isAlert = true)
        FlagRow(stringResource(R.string.field_bypass_mode), data.optBoolean("bypass_mode", false))
        FlagRow(stringResource(R.string.field_ups_failed), data.optBoolean("ups_failed", false), isAlert = true)
        FlagRow(stringResource(R.string.field_ups_standby), data.optBoolean("ups_standby", false), isNeutral = true)
        FlagRow(stringResource(R.string.field_test_in_progress), data.optBoolean("test_in_progress", false), isNeutral = true)
        FlagRow(stringResource(R.string.field_shutdown_active), data.optBoolean("shutdown_active", false), isAlert = true)
        FlagRow(stringResource(R.string.field_beep_on), data.optBoolean("beep_on", false), isNeutral = true)
    }
}

@Composable
private fun HealthCard(data: JSONObject) {
    val monitoring = data.optBoolean("monitoring", false)

    SectionCard(stringResource(R.string.health_title)) {
        DataRow(stringResource(R.string.health_monitoring),
            if (monitoring) stringResource(R.string.health_active) else stringResource(R.string.health_inactive))
        data.optString("version", "").takeIf { it.isNotBlank() }?.let {
            DataRow(stringResource(R.string.health_version), it)
        }
        val uptime = data.optInt("uptime_seconds", 0)
        if (uptime > 0) {
            val hours = uptime / 3600
            val mins = (uptime % 3600) / 60
            DataRow(stringResource(R.string.health_uptime), "${hours}h ${mins}m")
        }
        val failures = data.optInt("consecutive_failures", 0)
        DataRow(stringResource(R.string.health_failures), failures.toString())
        data.optString("port", "").takeIf { it.isNotBlank() && it != "null" }?.let {
            DataRow(stringResource(R.string.health_serial_port), it)
        }
    }
}

@Composable
private fun InfoCard(data: JSONObject) {
    SectionCard(stringResource(R.string.info_title)) {
        data.optString("hardware_id", "").takeIf { it.isNotBlank() }?.let {
            DataRow(stringResource(R.string.info_hardware_id), it)
        }
        data.optString("hostname", "").takeIf { it.isNotBlank() }?.let {
            DataRow(stringResource(R.string.info_hostname), it)
        }
        data.optString("platform", "").takeIf { it.isNotBlank() }?.let {
            DataRow(stringResource(R.string.info_platform), it)
        }
    }
}

@Composable
private fun SectionCard(title: String, content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = title,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(bottom = 8.dp)
            )
            content()
        }
    }
}

@Composable
private fun DataRow(label: String, value: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f)
        )
        Text(
            text = value,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = FontWeight.Medium,
            color = MaterialTheme.colorScheme.onSurface
        )
    }
}

@Composable
private fun FlagRow(label: String, active: Boolean, isAlert: Boolean = false, isNeutral: Boolean = false) {
    val yesText = stringResource(R.string.flag_yes)
    val noText = stringResource(R.string.flag_no)
    val valueColor = when {
        active && isAlert -> Red500
        active && isNeutral -> Blue500
        active -> Orange500
        else -> Green500
    }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f)
        )
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(8.dp)
                    .clip(CircleShape)
                    .background(valueColor)
            )
            Spacer(modifier = Modifier.width(6.dp))
            Text(
                text = if (active) yesText else noText,
                style = MaterialTheme.typography.bodyMedium,
                fontWeight = FontWeight.Medium,
                color = valueColor
            )
        }
    }
}

@Composable
private fun NotConfiguredMessage() {
    Box(
        modifier = Modifier.fillMaxSize(),
        contentAlignment = Alignment.Center
    ) {
        Text(
            text = stringResource(R.string.status_not_configured),
            style = MaterialTheme.typography.bodyLarge,
            textAlign = TextAlign.Center,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(32.dp)
        )
    }
}

@Composable
private fun ErrorMessage(error: String, isConnectionError: Boolean = true, onRetry: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        if (!isConnectionError) {
            // API responded but UPS not found — show "Conexão OK" in green first
            Text(
                text = stringResource(R.string.status_error_api_conn_ok),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                color = Color(0xFF2E7D32) // green
            )
            Spacer(modifier = Modifier.height(8.dp))
        }
        Text(
            text = stringResource(
                if (isConnectionError) R.string.status_error else R.string.status_error_api
            ),
            style = MaterialTheme.typography.titleMedium,
            color = if (isConnectionError) MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onSurfaceVariant
        )
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            text = error,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center
        )
        Spacer(modifier = Modifier.height(16.dp))
        Button(onClick = onRetry) {
            Text(stringResource(R.string.status_refresh))
        }
    }
}
