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
        assertTrue(client.isPartial("""{"type":"transcript","transcript":"next","final":false,"audioProcessedMs":800}"""))
        assertTrue(client.isFinal("""{"type":"transcript","transcript":"next test","final":true,"audioProcessedMs":1200}"""))
        assertTrue(client.isFinal("""{"type":"final","text":"next test","duration_s":1.2}"""))
        assertEquals(false, client.isPartial("""{"type":"transcript","transcript":"x","final":true}"""))
        assertEquals(false, client.isFinal("""{"type":"transcript","transcript":"x","final":false}"""))
    }

    @Test
    fun speechCompleteCountsAsFinalProgressDoesNot() {
        assertTrue(client.isFinal("""{"type":"speechComplete","turnId":1,"transcript":"Next test","audioProcessedMs":1200}"""))
        assertEquals(false, client.isPartial("""{"type":"audioProgress","audioProcessedMs":800}"""))
        assertEquals(false, client.isFinal("""{"type":"audioProgress","audioProcessedMs":800}"""))
    }

    @Test
    fun audioSpecIs24kPcm16Mono() {
        assertEquals(24000, VoiceAudioSpec.SAMPLE_RATE_HZ)
        assertEquals(16, VoiceAudioSpec.BITS_PER_SAMPLE)
        assertEquals(2400, VoiceAudioSpec.frameSamples())
        assertTrue(VoiceAudioSpec.MIC_EXPLANATION.contains("next test"))
    }
}
