package dev.dreamz.mmc.adapter

import java.io.EOFException
import java.io.InputStream
import java.io.OutputStream
import java.io.IOException

/**
 * Adapts blocking byte streams (BluetoothSocket, TCP socket) to the
 * [ByteSocket] seam used by [ClassicRfcommTransport] / [WifiTcpTransport].
 *
 * Protocol: write the line + "\r", then read until the '>' prompt, honouring
 * [defaultTimeoutMs] per exchange. Reads run on the calling thread; the
 * underlying stream's read must unblock on [close] (true for both
 * BluetoothSocket and java.net.Socket streams).
 *
 * Pure JVM (no Android framework) so the framing is unit-tested with fake
 * streams: prompt split across reads, SEARCHING... lines, and timeouts.
 */
class StreamByteSocket(
    private val input: InputStream,
    private val output: OutputStream,
    private val defaultTimeoutMs: Int = 2000,
    private val onClose: () -> Unit = {},
) : ByteSocket {
    @Volatile
    private var closed = false

    override fun exchange(request: String): String {
        if (closed) throw IOException("socket closed")
        val line = request.trim() + "\r"
        synchronized(output) {
            output.write(line.toByteArray(Charsets.US_ASCII))
            output.flush()
        }
        return readUntilPrompt(defaultTimeoutMs)
    }

    private fun readUntilPrompt(timeoutMs: Int): String {
        val deadlineNs = System.nanoTime() + timeoutMs * 1_000_000L
        val buf = StringBuilder()
        val one = ByteArray(1)
        while (true) {
            val remainingMs = (deadlineNs - System.nanoTime()) / 1_000_000
            if (remainingMs <= 0) throw IOException("read timed out waiting for '>' prompt")
            val available = try {
                input.available()
            } catch (e: IOException) {
                throw IOException("socket read failed: ${e.message}")
            }
            if (available <= 0) {
                if (closed) throw EOFException("socket closed while reading")
                // Fake/test streams may never report available>0 promptly;
                // poll briefly rather than blocking forever.
                try {
                    Thread.sleep(5)
                } catch (ie: InterruptedException) {
                    Thread.currentThread().interrupt()
                    throw IOException("interrupted while reading")
                }
                continue
            }
            val n = try {
                input.read(one, 0, 1)
            } catch (e: IOException) {
                throw IOException("socket read failed: ${e.message}")
            }
            if (n < 0) throw EOFException("adapter closed the connection")
            buf.append(one[0].toInt().and(0xFF).toChar())
            if (one[0].toInt().and(0xFF) == '>'.code) return buf.toString()
            if (buf.length > 64 * 1024) throw IOException("adapter reply too long (no '>' prompt)")
        }
    }

    override fun close() {
        closed = true
        try {
            onClose()
        } catch (_: Exception) {
        }
    }
}
