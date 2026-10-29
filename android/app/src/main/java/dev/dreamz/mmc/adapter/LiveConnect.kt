package dev.dreamz.mmc.adapter

import android.os.Build

/**
 * On-vehicle connection helpers (Bluetooth Classic SPP + Wi-Fi TCP).
 *
 * Everything hardware-free lives here as pure Kotlin so it runs on the JVM
 * test harness: the [ByteSocket] stream adapter, the secure → insecure →
 * channel-1 fallback order, the API-level permission rule, and the readable
 * error mapping. The thin Android glue (BluetoothAdapter, java.net.Socket)
 * is in `AndroidSockets.kt` and is deliberately untested without hardware.
 */
object BtSetup {
    /** SPP UUID every ELM327 clone advertises. */
    const val SPP_UUID = ClassicRfcommTransport.SPP_UUID

    /** Connection attempt order for ELM327 clones (worst clones first-fail). */
    enum class BtAttempt { SECURE, INSECURE, REFLECT_CHANNEL_1 }

    val ATTEMPT_ORDER: List<BtAttempt> =
        listOf(BtAttempt.SECURE, BtAttempt.INSECURE, BtAttempt.REFLECT_CHANNEL_1)

    /** Plain pre-permission explanation, shown before the system prompt. */
    const val BT_EXPLANATION =
        "Bluetooth is used only to talk to your ELM327 adapter " +
            "(already paired in Android settings). No contacts, files or location are accessed."

    /** Message when the user denies the permission. */
    const val BT_DENIED =
        "Bluetooth permission was denied, so the app cannot reach the adapter. " +
            "Allow it in Android Settings → Apps → MMC → Permissions, or use Wi-Fi TCP."

    /** Bluetooth LE is not wired yet — honest placeholder, never an error. */
    const val BLE_COMING_SOON =
        "Bluetooth LE is coming soon. For now use Bluetooth Classic (paired " +
            "ELM327) or Wi-Fi TCP."

    /** True on API levels where BLUETOOTH_CONNECT is a runtime permission. */
    fun needsRuntimeBtConnect(sdkInt: Int = Build.VERSION.SDK_INT): Boolean = sdkInt >= 31

    /**
     * Connect step tags, so the next field failure is diagnosable from the
     * screen alone: every failure line names the step plus the real
     * exception class and message.
     */
    enum class FailStep(val tag: String) {
        PERMISSION("permission"),
        SOCKET_CREATE("socket create"),
        CONNECT_ATTEMPT("connect attempt"),
        ATZ("ATZ"),
        PROTOCOL("protocol"),
        VIN("VIN"),
        SESSION("session"),
        LIVE_DATA("live data"),
    }

    /**
     * True when a bonded-device SPP connect may proceed. Connecting to an
     * already-paired device needs only BLUETOOTH_CONNECT on API 31+ — never
     * BLUETOOTH_SCAN. Legacy APIs need no runtime grant.
     */
    fun mayConnectClassic(sdkInt: Int = Build.VERSION.SDK_INT, connectGranted: Boolean): Boolean =
        sdkInt < 31 || connectGranted

    /**
     * True when touching discovery (isDiscovering / cancelDiscovery) is safe.
     * Both calls require BLUETOOTH_SCAN on API 31+, so with SCAN denied the
     * caller must skip them entirely — checking isDiscovering() itself would
     * throw SecurityException. Legacy APIs need no runtime grant.
     */
    fun mayTouchDiscovery(sdkInt: Int = Build.VERSION.SDK_INT, scanGranted: Boolean): Boolean =
        sdkInt < 31 || scanGranted

    /** Real exception class + message, truncated, with no MAC/host leak. */
    fun causedBy(e: Throwable): String =
        "${e.javaClass.simpleName}: ${(e.message ?: "(no message)").take(200)}"

    /**
     * One step-labeled failure line for the screen AND logcat, e.g.
     * "BT failed [step=connect attempt SECURE]: IOException: refused".
     */
    fun describeFailure(step: FailStep, e: Throwable, attempt: BtAttempt? = null): String {
        val where = if (step == FailStep.CONNECT_ATTEMPT && attempt != null) {
            "${step.tag} $attempt"
        } else {
            step.tag
        }
        return "failed [step=$where]: ${causedBy(e)}"
    }

    /**
     * Run [attempts] in order, returning the first success. Every failure is
     * collected with its 1-based index and exception class; when all fail
     * the last error is thrown with the attempt trail attached, so logcat
     * shows which fallbacks were tried.
     */
    fun <T> connectFirstOf(attempts: List<Pair<BtAttempt, () -> T>>): T {
        require(attempts.isNotEmpty()) { "no connection attempts" }
        val failures = mutableListOf<String>()
        var last: Exception? = null
        for ((index, entry) in attempts.withIndex()) {
            val (kind, attempt) = entry
            try {
                return attempt()
            } catch (e: Exception) {
                failures.add("attempt ${index + 1}/${attempts.size} $kind: ${causedBy(e)}")
                last = e
            }
        }
        throw java.io.IOException(
            "ELM327 connect failed (${failures.joinToString("; ")})",
            last,
        )
    }

    /**
     * Readable connect failure for the status line. Never echoes MACs,
     * hostnames or stack traces — just what the owner can act on, plus the
     * real cause in brackets so the screen alone diagnoses the failure.
     */
    fun friendlyConnectError(e: Exception): String {
        val cause = causedBy(e)
        val msg = (e.message ?: "").lowercase()
        if (e is SecurityException || msg.contains("permission")) {
            return "Bluetooth permission missing — allow it and tap Connect again. [$cause]"
        }
        if (msg.contains("refused") || msg.contains("unreachable") ||
            msg.contains("etimedout") || msg.contains("timed out") ||
            msg.contains("timeout")
        ) {
            return "Could not reach the adapter: not paired, out of range, or in use " +
                "by another app. Check pairing and try again. [$cause]"
        }
        return "Connect failed. Check the adapter is paired, powered and in range. [$cause]"
    }
}
