package dev.dreamz.mmc.session

import dev.dreamz.mmc.adapter.WifiTcpTransport
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** DevicePrefs persistence over an in-memory fake (no Android framework). */
class DevicePrefsTest {
    private class FakeStore : DevicePrefs.Store {
        val map = mutableMapOf<String, String>()
        override fun get(key: String, default: String): String = map[key] ?: default
        override fun put(key: String, value: String) {
            map[key] = value
        }
    }

    @Test
    fun btDeviceRoundTripsAndSurvivesAsPrefs() {
        val store = FakeStore()
        assertNull(DevicePrefs.btMac(store))
        DevicePrefs.rememberBtDevice(store, "AA:BB:CC:DD:EE:3F", "OBDII")
        assertEquals("AA:BB:CC:DD:EE:3F", DevicePrefs.btMac(store))
        assertEquals("OBDII", DevicePrefs.btName(store))
        // A fresh reader over the same store sees the remembered device.
        assertEquals("AA:BB:CC:DD:EE:3F", DevicePrefs.btMac(store))
    }

    @Test
    fun btLabelAndShortMacNeverShowFullMac() {
        val label = DevicePrefs.btLabel("OBDII", "AA:BB:CC:DD:EE:3F")
        assertTrue(label.contains("OBDII"))
        assertTrue(label.contains("…:3F"))
        assertTrue(!label.contains("AA:BB"))
        assertEquals("…:3F", DevicePrefs.shortMac("AA:BB:CC:DD:EE:3F"))
    }

    @Test
    fun wifiDefaultsMatchElmWifiConvention() {
        val store = FakeStore()
        assertEquals(WifiTcpTransport.DEFAULT_HOST, DevicePrefs.wifiHost(store))
        assertEquals(WifiTcpTransport.DEFAULT_PORT, DevicePrefs.wifiPort(store))
        assertEquals("192.168.0.10", DevicePrefs.wifiHost(store))
        assertEquals(35000, DevicePrefs.wifiPort(store))
    }

    @Test
    fun wifiEndpointSaveAndValidation() {
        val store = FakeStore()
        DevicePrefs.saveWifi(store, "192.168.1.5", "35000")
        assertEquals("192.168.1.5", DevicePrefs.wifiHost(store))
        assertEquals(35000, DevicePrefs.wifiPort(store))

        // Junk falls back to the defaults instead of breaking connect.
        DevicePrefs.saveWifi(store, "", "abc")
        assertEquals("192.168.0.10", DevicePrefs.wifiHost(store))
        assertEquals(35000, DevicePrefs.wifiPort(store))

        DevicePrefs.saveWifi(store, "10.0.0.2", "99999")
        assertEquals(35000, DevicePrefs.wifiPort(store))

        val (h, p) = DevicePrefs.wifiEndpoint(" 10.0.0.9 ", "8080")
        assertEquals("10.0.0.9", h)
        assertEquals(8080, p)
    }
}
