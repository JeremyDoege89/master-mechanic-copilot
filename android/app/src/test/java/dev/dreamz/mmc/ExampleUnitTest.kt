package dev.dreamz.mmc

import org.junit.Assert.assertEquals
import org.junit.Test

/** M0 shell tests. ELM allowlist/simulator tests land with M2. */
class ExampleUnitTest {
    @Test
    fun hex_parses_can_id() {
        // Sanity for the ECU header parsing M2 will build on: 0x7E8 is the
        // standard engine-ECU response CAN id.
        assertEquals(2024, "7E8".toInt(16))
    }

    @Test
    fun dtc_first_char_maps_to_powertrain() {
        // DTC byte decoding (M2 Parsers.kt): top two bits 00 -> 'P'.
        val firstByte = 0x01
        val table = charArrayOf('P', 'C', 'B', 'U')
        assertEquals('P', table[(firstByte shr 6) and 0x03])
    }
}
