package dev.dreamz.mmc.session

/**
 * Backend URL resolution + transport picker model (M4).
 *
 * Pure Kotlin (no Android framework) so it stays unit-testable on the JVM.
 * The debug default comes from the MMC_BACKEND_URL gradle property
 * (Tailscale address of the dev host); whatever the user saves in Settings
 * wins over the build default.
 */
object BackendUrls {
    const val PREFS = "mmc"
    const val KEY_BACKEND_URL = "backend_url"

    fun resolve(saved: String?, buildDefault: String): String {
        val trimmed = saved?.trim()
        return if (trimmed.isNullOrEmpty()) buildDefault else trimmed.trimEnd('/')
    }
}

/** Transports the app can connect through. Sim stays selectable so the full
 * gate flow (connect → live → next test → report) runs with no car. */
enum class TransportKind(val title: String) {
    SIM("Simulator (no car needed)"),
    CLASSIC("Bluetooth Classic"),
    BLE("Bluetooth LE"),
    WIFI("Wi-Fi TCP"),
}

object TransportOptions {
    val all: List<TransportKind> = TransportKind.entries.toList()
    val default: TransportKind = TransportKind.SIM
}
