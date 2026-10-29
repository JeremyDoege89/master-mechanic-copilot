package dev.dreamz.mmc.adapter

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream

/** StreamByteSocket framing over fake streams (no radio needed). */
class StreamSocketTest {
    private fun socketFor(reply: String): Pair<StreamByteSocket, ByteArrayOutputStream> {
        val out = ByteArrayOutputStream()
        val s = StreamByteSocket(ByteArrayInputStream(reply.toByteArray(Charsets.US_ASCII)), out)
        return s to out
    }

    @Test
    fun exchangeWritesLinePlusCrAndReadsToPrompt() {
        val (s, out) = socketFor("ELM327 v1.5\r\r>")
        assertEquals("ELM327 v1.5\r\r>", s.exchange("ATZ"))
        assertEquals("ATZ\r", out.toString("US-ASCII"))
    }

    @Test
    fun searchingLinesPassThrough() {
        val (s, _) = socketFor("SEARCHING...\r7E8 04 41 0C 1A F8\r\r>")
        val reply = s.exchange("010C")
        assertTrue(reply.contains("SEARCHING..."))
        assertTrue(reply.endsWith(">"))
    }

    @Test
    fun promptSplitAcrossReadsStillTerminates() {
        val bytes = "41 0C 1A F8\r\r>".toByteArray(Charsets.US_ASCII)
        var pos = 0
        val trickle = object : InputStream() {
            override fun read(): Int =
                if (pos < bytes.size) bytes[pos++].toInt() and 0xFF else -1
            override fun available(): Int = bytes.size - pos
        }
        val s = StreamByteSocket(trickle, ByteArrayOutputStream(), defaultTimeoutMs = 5_000)
        assertEquals("41 0C 1A F8\r\r>", s.exchange("010C"))
    }

    @Test
    fun emptyStreamTimesOutInsteadOfHanging() {
        val s = StreamByteSocket(
            ByteArrayInputStream(ByteArray(0)), ByteArrayOutputStream(), defaultTimeoutMs = 120,
        )
        try {
            s.exchange("ATZ")
            fail("expected timeout")
        } catch (e: java.io.IOException) {
            assertTrue(e.message!!.contains("timed out"))
        }
    }

    @Test
    fun closedPeerReportsCleanly() {
        val s = StreamByteSocket(
            ByteArrayInputStream(ByteArray(0)).also { it.close() },
            ByteArrayOutputStream(),
            defaultTimeoutMs = 5_000,
        )
        s.close()
        try {
            s.exchange("ATZ")
            fail("expected closed")
        } catch (e: java.io.IOException) {
            assertTrue(e.message!!.contains("closed"))
        }
    }

    @Test
    fun classicTransportWorksOverStreamSocket() {
        val fake = ByteArrayInputStream("OK\r\r>".toByteArray(Charsets.US_ASCII))
        val out = ByteArrayOutputStream()
        val t = ClassicRfcommTransport { StreamByteSocket(fake, out) }
        t.connect()
        assertEquals("OK\r\r>", t.transact("ATE0", 300))
        assertEquals("ATE0\r", out.toString("US-ASCII"))
        t.close()
    }

    @Test
    fun wifiTransportWorksOverStreamSocket() {
        var seenHost = ""
        var seenPort = 0
        val t = WifiTcpTransport(
            { h, p ->
                seenHost = h
                seenPort = p
                StreamByteSocket(
                    ByteArrayInputStream("OK\r\r>".toByteArray(Charsets.US_ASCII)),
                    ByteArrayOutputStream(),
                )
            },
            "192.168.0.10", 35000,
        )
        t.connect()
        assertEquals("OK\r\r>", t.transact("ATRV", 300))
        assertEquals("192.168.0.10", seenHost)
        assertEquals(35000, seenPort)
    }
}
