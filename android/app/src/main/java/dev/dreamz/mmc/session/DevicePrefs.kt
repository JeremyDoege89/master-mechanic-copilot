package dev.dreamz.mmc.session

import android.content.Context
import android.content.SharedPreferences
import dev.dreamz.mmc.adapter.WifiTcpTransport

/**
 * Remembered on-vehicle connection settings: the picked Bluetooth device
 * (MAC) and the editable Wi-Fi TCP endpoint.
 *
 * All logic runs against the [Store] interface so persistence is JVM-tested
 * with an in-memory fake; the app wires [SharedPrefsStore] over the same
 * "mmc" prefs file the backend URL already uses.
 */
object DevicePrefs {
    const val KEY_BT_MAC = "bt_mac"
    const val KEY_BT_NAME = "bt_name"
    const val KEY_WIFI_HOST = "wifi_host"
    const val KEY_WIFI_PORT = "wifi_port"

    /** Minimal key/value seam over SharedPreferences (fake in tests). */
    interface Store {
        fun get(key: String, default: String): String
        fun put(key: String, value: String)
    }

    class SharedPrefsStore(prefs: SharedPreferences) : Store {
        private val p = prefs
        override fun get(key: String, default: String): String =
            p.getString(key, default) ?: default
        override fun put(key: String, value: String) {
            p.edit().putString(key, value).apply()
        }
    }

    fun sharedPrefs(context: Context): Store =
        SharedPrefsStore(context.getSharedPreferences(BackendUrls.PREFS, Context.MODE_PRIVATE))

    // -- Bluetooth device -------------------------------------------------

    fun btMac(store: Store): String? =
        store.get(KEY_BT_MAC, "").trim().takeIf { it.isNotEmpty() }

    fun btName(store: Store): String =
        store.get(KEY_BT_NAME, "").trim()

    fun rememberBtDevice(store: Store, mac: String, name: String) {
        store.put(KEY_BT_MAC, mac.trim())
        store.put(KEY_BT_NAME, name.trim())
    }

    /** Picker label: "ELM327 (AA:BB:…)". Never logs the full MAC. */
    fun btLabel(name: String, mac: String): String =
        "${name.ifBlank { "(unnamed)" }} (${shortMac(mac)})"

    /** Last octet group only for on-screen/log use ("…:3F"). */
    fun shortMac(mac: String): String {
        val tail = mac.trim().takeLast(2).uppercase()
        return if (tail.length == 2) "…:$tail" else mac
    }

    // -- Wi-Fi TCP endpoint -------------------------------------------------

    fun wifiHost(store: Store): String =
        store.get(KEY_WIFI_HOST, WifiTcpTransport.DEFAULT_HOST).trim()
            .takeIf { it.isNotEmpty() } ?: WifiTcpTransport.DEFAULT_HOST

    fun wifiPort(store: Store): Int =
        store.get(KEY_WIFI_PORT, WifiTcpTransport.DEFAULT_PORT.toString()).trim()
            .toIntOrNull()?.takeIf { it in 1..65535 } ?: WifiTcpTransport.DEFAULT_PORT

    fun saveWifi(store: Store, host: String, port: String) {
        val h = host.trim().takeIf { it.isNotEmpty() } ?: WifiTcpTransport.DEFAULT_HOST
        val p = port.trim().toIntOrNull()?.takeIf { it in 1..65535 }
            ?: WifiTcpTransport.DEFAULT_PORT
        store.put(KEY_WIFI_HOST, h)
        store.put(KEY_WIFI_PORT, p.toString())
    }

    /** "host:port" for the status line; validated, never blank. */
    fun wifiEndpoint(host: String, port: String): Pair<String, Int> {
        val h = host.trim().takeIf { it.isNotEmpty() } ?: WifiTcpTransport.DEFAULT_HOST
        val p = port.trim().toIntOrNull()?.takeIf { it in 1..65535 }
            ?: WifiTcpTransport.DEFAULT_PORT
        return h to p
    }
}
