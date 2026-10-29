package dev.dreamz.mmc.elm

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class Elm327SimTest {
    private fun driverFor(personality: Personality, scenario: Scenario, misfireCyl: Int = 3): Elm327Driver {
        val sim = Elm327Sim(personality, scenario, misfireCyl)
        return Elm327Driver(sim)
    }

    private fun pid(driver: Elm327Driver, pid: Int): Double {
        val r = driver.query("01%02X".format(pid))
        require(r is ElmResult.Data) { "pid $pid failed: $r" }
        return Pids.decode(pid, r.ecus.single().bytes.copyOfRange(2, r.ecus.single().bytes.size))
    }

    @Test
    fun eachPersonalityCompletesInit() {
        for (p in Personality.entries) {
            val driver = driverFor(p, Scenario.HEALTHY)
            val profile = driver.init()
            assertTrue(profile.kind.isNotEmpty())
        }
    }

    @Test
    fun badCloneDegradesToLimitedWithoutCrash() {
        val driver = driverFor(Personality.BAD_CLONE, Scenario.HEALTHY)
        val profile = driver.init()
        assertEquals("limited_clone", profile.kind)
        try {
            driver.queryMultiPid(listOf(0x0C, 0x0D))
            throw AssertionError("limited profile must refuse multi-PID")
        } catch (e: BlockedRequestException) {
            // expected
        }
        // Single PID still works on the degraded profile.
        assertTrue(pid(driver, 0x0C) > 0)
    }

    @Test
    fun genuineSupportsMode22() {
        val driver = driverFor(Personality.GENUINE, Scenario.HEALTHY)
        assertTrue(driver.init().mode22)
    }

    @Test
    fun healthyScenarioReadsClean() {
        val driver = driverFor(Personality.GENUINE, Scenario.HEALTHY)
        driver.init()
        assertEquals(750.0, pid(driver, 0x0C), 1.0)
        val r = driver.query("03")
        assertTrue(r is ElmResult.TypedError && r.kind == ErrorKind.NO_DATA)
        val ect = pid(driver, 0x05)
        assertTrue("warm ect $ect", ect in 88.0..100.0)
    }

    @Test
    fun mafUnpluggedReadsZeroWithP0102() {
        val driver = driverFor(Personality.GENUINE, Scenario.MAF_UNPLUGGED)
        driver.init()
        assertEquals(0.0, pid(driver, 0x10), 1e-9)
        val r = driver.query("03")
        require(r is ElmResult.Data)
        assertEquals(listOf("P0102"), Parsers.decodeDtcs(r.ecus.single().bytes.copyOfRange(1, 3)))
    }

    @Test
    fun iatUnpluggedReadsMinus40WithP0113() {
        val driver = driverFor(Personality.GENUINE, Scenario.IAT_UNPLUGGED)
        driver.init()
        assertEquals(-40.0, pid(driver, 0x0F), 1e-9)
        val r = driver.query("03")
        require(r is ElmResult.Data)
        assertEquals(listOf("P0113"), Parsers.decodeDtcs(r.ecus.single().bytes.copyOfRange(1, 3)))
    }

    @Test
    fun vacuumLeakTrimsHighAtIdleLowAtCruise() {
        val sim = Elm327Sim(Personality.GENUINE, Scenario.VACUUM_LEAK)
        val driver = Elm327Driver(sim)
        driver.init()
        sim.rpm = 750.0
        val idle = pid(driver, 0x06) + pid(driver, 0x07)
        sim.rpm = 2500.0
        val cruise = pid(driver, 0x06) + pid(driver, 0x07)
        assertTrue("idle trim $idle", idle in 16.0..20.0)
        assertTrue("cruise trim $cruise", cruise in 2.0..7.0)
    }

    @Test
    fun fuelFuseRemovedDoesNotStart() {
        val driver = driverFor(Personality.GENUINE, Scenario.FUEL_FUSE_REMOVED)
        driver.init()
        // No start: the ECU yields no RPM or MAF data.
        assertEquals(
            ElmResult.TypedError(ErrorKind.NO_DATA),
            driver.query("010C"),
        )
        assertEquals(
            ElmResult.TypedError(ErrorKind.NO_DATA),
            driver.query("0110"),
        )
    }

    @Test
    fun coilUnpluggedShowsMisfiresAndP030n() {
        val driver = driverFor(Personality.GENUINE, Scenario.COIL_UNPLUGGED, misfireCyl = 3)
        driver.init()
        val r = driver.query("03")
        require(r is ElmResult.Data)
        assertEquals(listOf("P0303"), Parsers.decodeDtcs(r.ecus.single().bytes.copyOfRange(1, 3)))
        // Re-parse through the PCI-frame path for realism (headers on by default).
        val sim = Elm327Sim(Personality.GENUINE, Scenario.COIL_UNPLUGGED, 3)
        val raw = sim.transact("06", 300)
        val frameLines = raw.substringBefore(">").lines().map { l -> l.trim() }.filter { it.isNotEmpty() }
        val pci = frameLines.map { ln ->
            val ecu = (Parsers.frame(ln + "\r>") as ElmResult.Data).ecus.single()
            Parsers.hexOf(ecu.bytes)
        }
        val payload = Parsers.reassembleIsoTp(pci)
        val entries = Parsers.parseMode06(payload.copyOfRange(1, payload.size))
        assertEquals(4, entries.size)
        assertEquals(45, entries.first { it.tid == 3 }.value)
        assertEquals(0, entries.first { it.tid == 1 }.value)
    }

    @Test
    fun thermostatStuckOpenPlateausAt70WithP0128() {
        val sim = Elm327Sim(Personality.GENUINE, Scenario.THERMOSTAT_STUCK_OPEN)
        sim.simTimeSec = 0.0
        val driver = Elm327Driver(sim)
        driver.init()
        sim.simTimeSec = 1800.0
        assertEquals(70.0, pid(driver, 0x05), 3.0)
        val r = driver.query("03")
        require(r is ElmResult.Data)
        assertEquals(listOf("P0128"), Parsers.decodeDtcs(r.ecus.single().bytes.copyOfRange(1, 3)))
    }

    @Test
    fun driverMode06ReadsMisfireCounts() {
        val driver = driverFor(Personality.GENUINE, Scenario.COIL_UNPLUGGED, misfireCyl = 3)
        driver.init()
        val entries = driver.queryMode06()
        assertEquals(4, entries.size)
        assertEquals(45, entries.first { it.tid == 3 }.value)
        assertEquals(0, entries.first { it.tid == 1 }.value)

        val healthy = driverFor(Personality.GENUINE, Scenario.HEALTHY)
        healthy.init()
        assertTrue(healthy.queryMode06().all { it.value == 0 })
    }

    @Test
    fun genuineLiveReadsWorkAfterInitPackedConfig() {
        // Field configuration: driver.init() sends ATS0 + ATH1, so the sim
        // answers packed with headers (Mazda 3 field log). Every live read
        // the app performs must decode in that configuration.
        val sim = Elm327Sim(Personality.GENUINE, Scenario.HEALTHY)
        val driver = Elm327Driver(sim)
        driver.init()
        assertEquals(750.0, pid(driver, 0x0C), 1.0)
        assertTrue(pid(driver, 0x05) in 88.0..100.0)
        assertTrue(pid(driver, 0x10) > 0.0)
        assertEquals(Elm327Sim.VIN, driver.queryVin())
        val entries = driver.queryMode06()
        assertEquals(4, entries.size)
        assertTrue(entries.all { it.value == 0 })
        val dtcs = driver.query("03")
        assertTrue(dtcs is ElmResult.TypedError && dtcs.kind == ErrorKind.NO_DATA)
    }

    @Test
    fun ats0PacksRepliesAndAts1RestoresSpaces() {
        val sim = Elm327Sim(Personality.GENUINE, Scenario.HEALTHY)
        assertTrue(sim.transact("ATS0", 300).startsWith("OK"))
        sim.transact("ATH1", 300)
        val packed = sim.transact("010C", 300)
        assertTrue("headers on: $packed", packed.startsWith("7E8"))
        assertTrue("no spaces after ATS0: $packed", !packed.substringBefore(">").contains(" "))
        sim.transact("ATS1", 300)
        assertTrue(sim.transact("010C", 300).contains(" "))
    }

    @Test
    fun syntheticJm1VinReadsThroughPackedConfig() {
        // Synthetic JM1 VIN (never an owner's real VIN: the repo is public).
        val sim = Elm327Sim(Personality.GENUINE, Scenario.HEALTHY, vin = "JM1BM9M72S1000001")
        val driver = Elm327Driver(sim)
        driver.init() // ATS0 + ATH1: packed PCI frames with headers
        assertEquals("JM1BM9M72S1000001", driver.queryVin())
    }

    @Test
    fun warmupCurveIsFirstOrderTo95() {
        val sim = Elm327Sim(Personality.GENUINE, Scenario.HEALTHY)
        sim.simTimeSec = 0.0
        assertEquals(20.0, sim.ect(), 1e-9)
        sim.simTimeSec = 300.0
        assertEquals(95.0 - 75.0 / Math.E, sim.ect(), 1e-9)
    }
}
