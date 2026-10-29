package dev.dreamz.mmc.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** M7 review #2: the pending card maps to the real test-result submission. */
class VoiceConfirmTest {
    @Test
    fun voltageSubmitsWithAssumptionVisible() {
        val sub = VoiceConfirm.submissionFor(VoiceIntent.LogVoltage(12.4), emptyMap())!!
        assertEquals("battery_voltage_rest_running", sub.testRef)
        assertEquals(12.4, sub.measured["voltage_v"] as Double, 1e-9)
        assertTrue(sub.measured.containsKey("engine_on"))
    }

    @Test
    fun trimNeedsBothHalves() {
        val pending = VoiceIntent.LogTrim("cruise", 4.0)
        // One half staged: not submittable — Confirm must ask for the other.
        assertNull(VoiceConfirm.submissionFor(pending, mapOf("idle_trim_pct" to 18.0)))
        val staged = VoiceConfirm.stageTrim(
            VoiceConfirm.stageTrim(emptyMap(), VoiceIntent.LogTrim("idle", 18.0)),
            pending,
        )
        val sub = VoiceConfirm.submissionFor(pending, staged)!!
        assertEquals("trim_idle_vs_2500", sub.testRef)
        assertEquals(18.0, sub.measured["idle_trim_pct"] as Double, 1e-9)
        assertEquals(4.0, sub.measured["cruise_trim_pct"] as Double, 1e-9)
    }

    @Test
    fun partlessTrimFillsIdleFirst() {
        val staged = VoiceConfirm.stageTrim(emptyMap(), VoiceIntent.LogTrim(null, 9.0))
        assertEquals(9.0, staged["idle_trim_pct"]!!, 1e-9)
    }

    @Test
    fun nonLogIntentsAreNotSubmittable() {
        assertNull(VoiceConfirm.submissionFor(VoiceIntent.NextTest, emptyMap()))
        assertNull(VoiceConfirm.submissionFor(null, emptyMap()))
        assertNull(VoiceConfirm.submissionFor(VoiceIntent.Confirm, emptyMap()))
    }
}
