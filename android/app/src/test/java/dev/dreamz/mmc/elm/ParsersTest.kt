package dev.dreamz.mmc.elm

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Byte-exact parser checks against the golden transcripts (plan §2 oracle). */
class ParsersTest {
    private fun transcript(name: String): String {
        val stream = javaClass.getResourceAsStream("/transcripts/$name")
            ?: error("missing test resource transcripts/$name")
        return stream.bufferedReader().readText()
    }

    private fun data(name: String): List<EcuLine> {
        val r = Parsers.frame(transcript(name))
        require(r is ElmResult.Data) { "$name did not parse as data: $r" }
        return r.ecus
    }

    @Test
    fun singlePidRpmDecodes1726() {
        val ecus = data("pid-rpm-single.txt")
        assertEquals(1, ecus.size)
        assertEquals("7E8", ecus[0].canId)
        assertArrayEquals(byteArrayOf(0x41, 0x0C, 0x1A, 0xF8.toByte()), ecus[0].bytes)
        assertEquals(1726.0, Pids.decode(0x0C, ecus[0].bytes.copyOfRange(2, 4)), 1e-9)
    }

    @Test
    fun searchingIsStripped() {
        val ecus = data("searching-then-data.txt")
        assertEquals(1, ecus.size)
        assertEquals(1726.0, Pids.decode(0x0C, ecus[0].bytes.copyOfRange(2, 4)), 1e-9)
    }

    @Test
    fun supportBitmapMatchesExpectedPids() {
        val ecus = data("pid-support-bitmap.txt")
        val bitmap = ecus[0].bytes.copyOfRange(2, 6)
        assertTrue(Parsers.isPidSupported(bitmap, 0x01))
        assertFalse(Parsers.isPidSupported(bitmap, 0x02))
        assertTrue(Parsers.isPidSupported(bitmap, 0x04))
        assertTrue(Parsers.isPidSupported(bitmap, 0x0C))
    }

    @Test
    fun multiEcuSeparates7E8and7E9() {
        val ecus = data("multi-ecu.txt")
        assertEquals(2, ecus.size)
        assertEquals("7E8", ecus[0].canId)
        assertEquals("7E9", ecus[1].canId)
        assertEquals(1726.0, Pids.decode(0x0C, ecus[0].bytes.copyOfRange(2, 4)), 1e-9)
        assertEquals(1728.0, Pids.decode(0x0C, ecus[1].bytes.copyOfRange(2, 4)), 1e-9)
    }

    @Test
    fun isotpVinReassemblesToFakeVin() {
        val raw = transcript("isotp-vin.txt")
        val lines = raw.substringBefore(">").lines().map { it.trim() }.filter { it.isNotEmpty() }
        val payload = Parsers.reassembleIsoTp(lines)
        assertEquals("1FATP8UH5P5123456", Parsers.parseVin(payload))
    }

    @Test
    fun storedDtcsDecodeToP0133AndP0203() {
        val ecus = data("dtc-stored.txt")
        assertArrayEquals(byteArrayOf(0x43, 0x01, 0x33, 0x02, 0x03), ecus[0].bytes)
        assertEquals(listOf("P0133", "P0203"), Parsers.decodeDtcs(ecus[0].bytes.copyOfRange(1, 5)))
    }

    @Test
    fun pciMultiframeReassemblesToMode06Entries() {
        val raw = transcript("mode06-pci-multiframe.txt")
        val lines = raw.substringBefore(">").lines().map { it.trim() }.filter { it.isNotEmpty() }
        // Strip CAN-id prefixes back to PCI bytes, as the driver does with headers on.
        val pci = lines.map { ln ->
            val ecu = (Parsers.frame(ln + "\r>") as ElmResult.Data).ecus.single()
            Parsers.hexOf(ecu.bytes)
        }
        val payload = Parsers.reassembleIsoTp(pci)
        assertEquals(0x46, payload[0].toInt() and 0xFF)
        val entries = Parsers.parseMode06(payload.copyOfRange(1, payload.size))
        assertEquals(2, entries.size)
        assertEquals(Parsers.Mode06Entry(0x0B, 0x01, 9, 0, 15), entries[0])
        assertEquals(Parsers.Mode06Entry(0x0B, 0x02, 0, 0, 10), entries[1])
    }

    @Test
    fun eachErrorStringMapsToItsTypedResult() {
        val cases = mapOf(
            "error-no-data.txt" to ErrorKind.NO_DATA,
            "error-can-error.txt" to ErrorKind.CAN_ERROR,
            "error-bus-init.txt" to ErrorKind.BUS_INIT_ERROR,
            "error-buffer-full.txt" to ErrorKind.BUFFER_FULL,
            "error-stopped.txt" to ErrorKind.STOPPED,
            "error-unable.txt" to ErrorKind.UNABLE_TO_CONNECT,
            "init-badclone-atpps.txt" to ErrorKind.UNKNOWN_COMMAND,
        )
        for ((file, kind) in cases) {
            val r = Parsers.frame(transcript(file))
            assertEquals("$file", ElmResult.TypedError(kind), r)
        }
    }

    @Test
    fun pidFormulasSpotCheck() {
        assertEquals(90.0, Pids.decode(0x05, byteArrayOf(0x82.toByte())), 1e-9) // ECT 130-40
        assertEquals(-40.0, Pids.decode(0x0F, byteArrayOf(0x00)), 1e-9) // IAT unplugged
        assertEquals(0.0, Pids.decode(0x10, byteArrayOf(0x00, 0x00)), 1e-9) // MAF unplugged
        assertEquals(14.2, Pids.decode(0x42, byteArrayOf(0x37, 0x78)), 1e-9) // 0x377C=14236mV
        assertEquals(2, Parsers.dtcCount(0x82.toByte()))
    }
}
