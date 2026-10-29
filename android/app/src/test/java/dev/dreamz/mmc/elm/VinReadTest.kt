package dev.dreamz.mmc.elm

import dev.dreamz.mmc.adapter.SimTransport
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Mode 09 VIN read through the real driver + simulator (allowlist intact). */
class VinReadTest {
    @Test
    fun genuineSimReturnsItsVin() {
        val driver = Elm327Driver(SimTransport(Elm327Sim(scenario = Scenario.HEALTHY)))
        driver.init()
        assertEquals(Elm327Sim.VIN, driver.queryVin())
        assertTrue(driver.commandsSent.contains("0902"))
    }

    @Test
    fun vinReadNeedsInitFirst() {
        val driver = Elm327Driver(SimTransport(Elm327Sim()))
        try {
            driver.queryVin()
            fail("expected init() first")
        } catch (e: IllegalStateException) {
            assertTrue(e.message!!.contains("init"))
        }
    }

    @Test
    fun noDataVinSurfacesAsNoObdData() {
        val sim = Elm327Sim()
        val empty = object : dev.dreamz.mmc.adapter.Transport {
            override fun transact(line: String, timeoutMs: Int): String =
                if (line == "0902") "NO DATA\r\r>" else sim.transact(line, timeoutMs)
        }
        val driver = Elm327Driver(empty)
        driver.init()
        try {
            driver.queryVin()
            fail("expected NoObdDataException")
        } catch (e: NoObdDataException) {
            // best-effort read: the controller continues without a vehicle.
        }
    }

    @Test
    fun allowlistStillBlocksWritesAndMode04() {
        val driver = Elm327Driver(SimTransport(Elm327Sim()))
        driver.init()
        try {
            driver.query("04")
            fail("expected mode 04 block")
        } catch (e: BlockedRequestException) {
            // unchanged: no token, no clear.
        }
        try {
            driver.query("2E")
            fail("expected UDS block")
        } catch (e: BlockedRequestException) {
            // unchanged.
        }
    }

    @Test
    fun simVinParsesAsPlausibleMazdaFree() {
        // The sim carries a Ford-pattern VIN; the parse path is what matters.
        val payload = byteArrayOf(0x49, 0x02, 0x01) +
            Elm327Sim.VIN.toByteArray(Charsets.US_ASCII)
        assertEquals(Elm327Sim.VIN, Parsers.parseVin(payload))
    }
}
