package dev.dreamz.mmc.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** M7 §1 ASR socket shape — offline, no network. */
class RealtimeAsrClientTest {
    private val client = RealtimeAsrClient("http://example.invalid:8000/", "sess123")

    @Test
    fun socketUrlReusesStateSocketLifecycle() {
        assertEquals("ws://example.invalid:8000/sessions/sess123/asr", client.socketUrl())
    }

    @Test
    fun controlFramesAreStartStop() {
        assertTrue(client.controlFrame("start").contains("start"))
        assertTrue(client.controlFrame("stop").contains("stop"))
    }

    @Test
    fun partialFinalShapesFromFixtures() {
        assertTrue(client.isPartial("""{"type":"partial","text":"next"}"""))
        assertTrue(client.isFinal("""{"type":"final","text":"next test","duration_s":1.2}"""))
        assertEquals(false, client.isPartial("""{"type":"final","text":"x"}"""))
        assertEquals(false, client.isFinal("""{"type":"partial","text":"x"}"""))
    }

    @Test
    fun audioSpecIs16kPcm16Mono() {
        assertEquals(16000, VoiceAudioSpec.SAMPLE_RATE_HZ)
        assertEquals(16, VoiceAudioSpec.BITS_PER_SAMPLE)
        assertTrue(VoiceAudioSpec.MIC_EXPLANATION.contains("next test"))
    }
}
