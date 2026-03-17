package com.tsshara.app.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities

/**
 * Monitors network connectivity using ConnectivityManager.
 *
 * Uses [NetworkCapabilities.NET_CAPABILITY_VALIDATED] to ensure the device
 * actually has working internet access — not just a Wi-Fi or cellular radio
 * connection. Android validates this by probing a known endpoint, so it
 * correctly handles captive portals, routers without WAN, and other
 * "connected but no internet" scenarios.
 */
class NetworkMonitor(context: Context) {

    private val connectivityManager =
        context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager

    @Volatile
    var isInternetAvailable: Boolean = checkNow()
        private set

    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
            isInternetAvailable = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        }

        override fun onLost(network: Network) {
            // Re-check: another network may still be available
            isInternetAvailable = checkNow()
        }
    }

    fun register() {
        // Seed with current state before registering for changes
        isInternetAvailable = checkNow()
        connectivityManager.registerDefaultNetworkCallback(networkCallback)
    }

    fun unregister() {
        try {
            connectivityManager.unregisterNetworkCallback(networkCallback)
        } catch (_: IllegalArgumentException) {
            // Already unregistered
        }
    }

    /**
     * Synchronous point-in-time check.  Useful as a fallback when the
     * callback-based [isInternetAvailable] hasn't caught up yet.
     */
    fun checkNow(): Boolean {
        val network = connectivityManager.activeNetwork ?: return false
        val caps = connectivityManager.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    }
}
