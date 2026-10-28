package dev.dreamz.mmc.adapter

import dev.dreamz.mmc.elm.Elm327Sim

/** In-process simulator transport: same [Transport] shape as the real ones. */
class SimTransport(private val sim: Elm327Sim = Elm327Sim()) : Transport {
    override fun transact(line: String, timeoutMs: Int): String = sim.transact(line, timeoutMs)
}

/** Timed transcript feed. Replays recorded request/response pairs at the
 * original offsets (scaled by [timeScale]), so the backend cannot tell
 * replay from live. */
class ReplayTransport(
    private val events: List<ReplayEvent>,
    private val timeScale: Double = 1.0,
) : Transport {
    data class ReplayEvent(val offsetMs: Long, val request: String, val response: String)

    private var index = 0
    private var startNs: Long = System.nanoTime()

    override fun transact(line: String, timeoutMs: Int): String {
        if (index >= events.size) throw IllegalStateException("replay exhausted")
        val event = events[index++]
        if (event.request != line.trim()) {
            throw IllegalStateException("replay diverged: expected <${event.request}>, got <${line.trim()}>")
        }
        val elapsedMs = (System.nanoTime() - startNs) / 1_000_000.0
        val waitMs = event.offsetMs * timeScale - elapsedMs
        if (waitMs > 0) Thread.sleep(waitMs.toLong())
        return event.response
    }

    companion object {
        private fun unescapeJsonString(s: String): String {
            val out = StringBuilder()
            var i = 0
            while (i < s.length) {
                val c = s[i]
                if (c == '\\' && i + 1 < s.length) {
                    when (s[i + 1]) {
                        'r' -> out.append('\r')
                        'n' -> out.append('\n')
                        '"' -> out.append('"')
                        '\\' -> out.append('\\')
                        else -> out.append(s[i + 1])
                    }
                    i += 2
                } else {
                    out.append(c)
                    i += 1
                }
            }
            return out.toString()
        }

        private val ROW = Regex(""""t":(\d+),"dir":"(tx|rx)","data":"(.*)"\}""")

        /** Minimal JSON-lines parse for transcript.jsonl rows. */
        fun parseTranscriptJsonl(text: String): List<ReplayEvent> {
            val out = mutableListOf<ReplayEvent>()
            var pendingTx: Pair<Long, String>? = null
            for (line in text.lines()) {
                val t = line.trim()
                if (t.isEmpty()) continue
                val m = ROW.find(t) ?: continue
                val offset = m.groupValues[1].toLong()
                val dir = m.groupValues[2]
                val data = unescapeJsonString(m.groupValues[3])
                if (dir == "tx") {
                    pendingTx = offset to data.trim()
                } else {
                    val tx = pendingTx
                    if (tx != null) {
                        out.add(ReplayEvent(tx.first, tx.second, data))
                        pendingTx = null
                    }
                }
            }
            return out
        }
    }
}

/** Classic Bluetooth RFCOMM (SPP) transport behind an injectable socket. */
class ClassicRfcommTransport(private val socketFactory: () -> ByteSocket) : Transport {
    companion object {
        const val SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"
    }

    private var socket: ByteSocket? = null

    fun connect(): ByteSocket {
        val s = socketFactory()
        socket = s
        return s
    }

    override fun transact(line: String, timeoutMs: Int): String {
        val s = socket ?: throw IllegalStateException("connect() first")
        return s.exchange(line)
    }

    fun close() {
        socket?.close()
        socket = null
    }
}

/** BLE UART-style transport (vendor services FFE0/FFE1 or FFF0/FFF1). */
class BleTransport(
    private val socketFactory: (serviceUuid: String) -> ByteSocket,
    val serviceUuid: String = PRIMARY_SERVICE,
) : Transport {
    companion object {
        const val PRIMARY_SERVICE = "FFE0"
        const val PRIMARY_CHAR = "FFE1"
        const val ALT_SERVICE = "FFF0"
        const val ALT_CHAR = "FFF1"
    }

    private var socket: ByteSocket? = null

    fun connect(): ByteSocket {
        val s = socketFactory(serviceUuid)
        socket = s
        return s
    }

    override fun transact(line: String, timeoutMs: Int): String {
        val s = socket ?: throw IllegalStateException("connect() first")
        return s.exchange(line)
    }

    fun close() {
        socket?.close()
        socket = null
    }
}

/** Wi-Fi TCP transport (default ELM Wi-Fi endpoint 192.168.0.10:35000). */
class WifiTcpTransport(
    private val socketFactory: (host: String, port: Int) -> ByteSocket,
    val host: String = DEFAULT_HOST,
    val port: Int = DEFAULT_PORT,
) : Transport {
    companion object {
        const val DEFAULT_HOST = "192.168.0.10"
        const val DEFAULT_PORT = 35000
    }

    private var socket: ByteSocket? = null

    fun connect(): ByteSocket {
        val s = socketFactory(host, port)
        socket = s
        return s
    }

    override fun transact(line: String, timeoutMs: Int): String {
        val s = socket ?: throw IllegalStateException("connect() first")
        return s.exchange(line)
    }

    fun close() {
        socket?.close()
        socket = null
    }
}
