package dev.dreamz.mmc.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** M7 §1 JVM grammar mirror — fixture transcripts shape pending + TTS. */
class VoiceCommandTest {
    @Test
    fun nextTestHasNoMutation() {
        assertEquals(VoiceIntent.NextTest, VoiceCommands.parse("next test"))
    }

    @Test
    fun logVoltsDigitsAndWords() {
        val digits = VoiceCommands.parse("log 12.4 volts") as VoiceIntent.LogVoltage
        assertEquals(12.4, digits.volts, 1e-9)
        assertEquals("battery_voltage_rest_running", digits.testRef)
        val words = VoiceCommands.parse("log twelve point four volts") as VoiceIntent.LogVoltage
        assertEquals(12.4, words.volts, 1e-9)
    }

    @Test
    fun logIdleTrimStagesPart() {
        val intent = VoiceCommands.parse("log idle trim plus eighteen") as VoiceIntent.LogTrim
        assertEquals("idle", intent.part)
        assertEquals(18.0, intent.value, 1e-9)
    }

    @Test
    fun rpmTokensNeverBecomeTheValue() {
        // M7 review #5: 2500/rpm is the condition, never the measurement.
        val cruise = VoiceCommands.parse("log cruise trim plus four at 2500") as VoiceIntent.LogTrim
        assertEquals("cruise", cruise.part)
        assertEquals(4.0, cruise.value, 1e-9)
        val volts = VoiceCommands.parse("log voltage twelve volts and 2500 rpm") as VoiceIntent.LogVoltage
        assertEquals(12.0, volts.volts, 1e-9)
    }

    @Test
    fun sixtyToNinetyNumberWords() {
        val trim = VoiceCommands.parse("log idle trim plus sixty") as VoiceIntent.LogTrim
        assertEquals(60.0, trim.value, 1e-9)
        val volts = VoiceCommands.parse("log sixteen volts") as VoiceIntent.LogVoltage
        assertEquals(16.0, volts.volts, 1e-9)
    }

    @Test
    fun repeatRiskLevelUnknown() {
        assertEquals(VoiceIntent.Repeat, VoiceCommands.parse("repeat"))
        assertEquals(VoiceIntent.Risk, VoiceCommands.parse("what's the risk"))
        assertEquals(VoiceIntent.Level("L1"), VoiceCommands.parse("level one"))
        assertTrue(VoiceCommands.parse("clear the codes please") is VoiceIntent.Unknown)
    }

    @Test
    fun ttsTextSelectsSpokenReply() {
        assertTrue(VoiceCommands.ttsText(VoiceIntent.NextTest, "Fuel trim check").startsWith("Next test:"))
        assertTrue(VoiceCommands.ttsText(VoiceIntent.LogVoltage(12.4)).contains("confirm"))
        assertTrue(VoiceCommands.ttsText(VoiceIntent.LogTrim("idle", 18.0)).contains("2500"))
        assertTrue(VoiceCommands.ttsText(VoiceIntent.Unknown("x")).contains("Didn't catch that"))
    }

    @Test
    fun uiStateHandsFreePendingConfirm() {
        val ui = VoiceUiState()
        assertEquals(false, ui.handsFree)
        ui.setHandsFree(true)
        assertEquals(true, ui.handsFree)
        ui.onPartial("next")
        assertEquals("next", ui.caption)
        ui.onFinal("log 12.4 volts")
        assertTrue(ui.pending is VoiceIntent.LogVoltage)
        assertTrue(ui.lastSpoken.contains("confirm"))
        ui.clearPending()
        assertNull(ui.pending)
        ui.onFinal("next test", rankOneTitle = "Fuel trim check")
        assertNull(ui.pending)
        assertTrue(ui.lastSpoken.contains("Fuel trim check"))
    }
}
