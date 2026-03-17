package com.tsshara.app.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext

private val LightColorScheme = lightColorScheme(
    primary = Green700,
    onPrimary = Gray50,
    primaryContainer = Green100,
    onPrimaryContainer = Gray900,
    secondary = Blue500,
    onSecondary = Gray50,
    background = Gray50,
    onBackground = Gray900,
    surface = Gray50,
    onSurface = Gray900,
    surfaceVariant = Gray100,
    onSurfaceVariant = Gray600,
    error = Red700,
    onError = Gray50,
)

private val DarkColorScheme = darkColorScheme(
    primary = Green300,
    onPrimary = Gray900,
    primaryContainer = Green700,
    onPrimaryContainer = Green100,
    secondary = Blue500,
    onSecondary = Gray900,
    background = Gray900,
    onBackground = Gray50,
    surface = Gray800,
    onSurface = Gray50,
    surfaceVariant = Gray800,
    onSurfaceVariant = Gray400,
    error = Red500,
    onError = Gray900,
)

@Composable
fun TssharaTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit
) {
    val colorScheme = when {
        // Use dynamic color on Android 12+ if available
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val context = LocalContext.current
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        }
        darkTheme -> DarkColorScheme
        else -> LightColorScheme
    }

    MaterialTheme(
        colorScheme = colorScheme,
        content = content
    )
}
