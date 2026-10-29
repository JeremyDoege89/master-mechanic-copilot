package dev.dreamz.mmc.session

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** VIN validation, WMI/year tables and vPIC parsing (no network in tests). */
class VinDecodeTest {
    // Mazda 3, 2018 (10th char J), 17 chars, no I/O/Q.
    private val MAZDA_VIN = "JM1BL1H59J1123456"

    @Test
    fun plausibleVinGate() {
        assertTrue(VinDecode.isPlausibleVin(MAZDA_VIN))
        assertTrue(VinDecode.isPlausibleVin("jm1bl1h59j1123456"))
        assertFalse(VinDecode.isPlausibleVin("JM1BL1H59J112345")) // 16 chars
        assertFalse(VinDecode.isPlausibleVin("JM1BL1H59J11234567")) // 18 chars
        assertFalse(VinDecode.isPlausibleVin("JM1BL1H59I1123456")) // I forbidden
        assertFalse(VinDecode.isPlausibleVin("JM1BL1H59O1123456")) // O forbidden
        assertFalse(VinDecode.isPlausibleVin(""))
    }

    @Test
    fun wmiMakeCoversOwnerMazdaAndFord() {
        assertEquals("Mazda", VinDecode.wmiMake(MAZDA_VIN))
        assertEquals("Mazda", VinDecode.wmiMake("JM7BL1H59J1123456"))
        assertEquals("Ford", VinDecode.wmiMake("1FATP8UH5P5123456"))
        assertEquals("Toyota", VinDecode.wmiMake("JTDBR32E242012345"))
        assertEquals("Unknown", VinDecode.wmiMake("WVWZZZ1KZ6W123456"))
        assertEquals("Unknown", VinDecode.wmiMake("XX"))
    }

    @Test
    fun modelYearFromTenthChar() {
        assertEquals(2018, VinDecode.vinModelYear(MAZDA_VIN, nowYear = 2026))
        // '5' in 2026 is 2005 (2035 is in the future).
        assertEquals(2005, VinDecode.vinModelYear("JM1BL1H5951123456", nowYear = 2026))
        // 'Y' is 2000/2030 -> 2000 while 2030 is future.
        assertEquals(2000, VinDecode.vinModelYear("JM1BL1H59Y1123456", nowYear = 2026))
        assertNull(VinDecode.vinModelYear("JM1BL1H59U1123456", nowYear = 2026)) // U invalid
        assertNull(VinDecode.vinModelYear("SHORT", nowYear = 2026))
    }

    @Test
    fun last6IsTheOnlyShownSubstring() {
        assertEquals("123456", VinDecode.last6(MAZDA_VIN))
    }

    @Test
    fun fallbackVehicleIsBackendValid() {
        val v = VinDecode.fallbackVehicle(MAZDA_VIN, nowYear = 2026)
        assertEquals("Mazda", v["make"])
        assertEquals("Unknown", v["model"])
        assertEquals(2018, v["year"])
        assertEquals(MAZDA_VIN, v["vin"])
    }

    @Test
    fun vpicMazdaResponseParsesToBackendShape() {
        val body = """{"Results":[{
            "Make":"MAZDA","Model":"3","ModelYear":"2018",
            "DisplacementL":"2.0","EngineCylinders":"4","ErrorCode":"0"}]}"""
        val v = VinDecode.parseVpicResponse(MAZDA_VIN, body)!!
        assertEquals("Mazda", v["make"])
        assertEquals("3", v["model"])
        assertEquals(2018, v["year"])
        assertEquals(MAZDA_VIN, v["vin"])
        assertEquals(2.0, v["DisplacementL"] as Double, 1e-9)
        assertEquals("2.0L I4", v["engine"])
    }

    @Test
    fun vpicJunkReturnsNullSoCallerFallsBack() {
        assertNull(VinDecode.parseVpicResponse(MAZDA_VIN, "not json"))
        assertNull(VinDecode.parseVpicResponse(MAZDA_VIN, """{"Results":[]}"""))
        val noMake = """{"Results":[{"Make":"","Model":"3","ModelYear":"2018","ErrorCode":"1"}]}"""
        assertNull(VinDecode.parseVpicResponse(MAZDA_VIN, noMake))
    }

    @Test
    fun fetchVpicUsesInjectedFetcherAndSurvivesFailure() {
        val body = """{"Results":[{
            "Make":"MAZDA","Model":"3","ModelYear":"2018",
            "DisplacementL":"2.0","EngineCylinders":"4","ErrorCode":"0"}]}"""
        var askedUrl = ""
        val ok = VinDecode.fetchVpic(MAZDA_VIN) { url, _ ->
            askedUrl = url
            body
        }
        assertEquals("Mazda", ok!!["make"])
        assertTrue(askedUrl.contains(MAZDA_VIN))

        // Offline / throwing fetcher -> null (caller uses the WMI fallback).
        val down = VinDecode.fetchVpic(MAZDA_VIN) { _, _ -> throw java.io.IOException("offline") }
        assertNull(down)
        assertNull(VinDecode.fetchVpic("SHORT") { _, _ -> body })
    }
}
