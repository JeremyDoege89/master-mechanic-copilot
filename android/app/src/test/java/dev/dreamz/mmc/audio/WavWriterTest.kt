package dev.dreamz.mmc.audio

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** WAV writer + capture spec — offline JVM tests, no microphone. */
class WavWriterTest {
    private fun tag(bytes: ByteArray, off: Int): String =
        bytes.copyOfRange(off, off + 4).toString(Charsets.US_ASCII)

    private fun leInt(bytes: ByteArray, off: Int): Int =
        (bytes[off].toInt() and 0xFF) or
            ((bytes[off + 1].toInt() and 0xFF) shl 8) or
            ((bytes[off + 2].toInt() and 0xFF) shl 16) or
            ((bytes[off + 3].toInt() and 0xFF) shl 24)

    private fun leShort(bytes: ByteArray, off: Int): Int =
        (bytes[off].toInt() and 0xFF) or ((bytes[off + 1].toInt() and 0xFF) shl 8)

    @Test
    fun wavHeader_is16BitMono44100() {
        val samples = ShortArray(44100) { (it % 1000).toShort() }
        val wav = WavWriter.pcmToWav(samples)

        assertEquals(44 + samples.size * 2, wav.size)
        assertEquals("RIFF", tag(wav, 0))
        assertEquals("WAVE", tag(wav, 8))
        assertEquals("fmt ", tag(wav, 12))
        assertEquals(1, leShort(wav, 20)) // PCM
        assertEquals(1, leShort(wav, 22)) // mono
        assertEquals(44100, leInt(wav, 24)) // no low sample rates
        assertEquals(16, leShort(wav, 34)) // 16-bit
        assertEquals("data", tag(wav, 36))
        assertEquals(samples.size * 2, leInt(wav, 40))
    }

    @Test
    fun wavBody_roundTripsSamples() {
        val samples = shortArrayOf(0, 1000, -1000, Short.MAX_VALUE, Short.MIN_VALUE)
        val wav = WavWriter.pcmToWav(samples)
        val body = wav.copyOfRange(44, wav.size)
        val back = ShortArray(samples.size) { i ->
            (((body[i * 2 + 1].toInt() shl 8) or (body[i * 2].toInt() and 0xFF))).toShort()
        }
        assertArrayEquals(samples, back)
    }

    @Test
    fun sixSecondsFitsBackendWindow() {
        val sixSec = ShortArray(EngineSoundSpec.SAMPLE_RATE_HZ * 6)
        val wav = WavWriter.pcmToWav(sixSec)
        val seconds = (wav.size - 44) / 2 / EngineSoundSpec.SAMPLE_RATE_HZ
        assertTrue(seconds in 5..8)
    }
}

class EngineSoundSpecTest {
    @Test
    fun sampleRateStays44100() {
        // The squeal band reaches 6 kHz: never drop to a telephony rate.
        assertEquals(44100, EngineSoundSpec.SAMPLE_RATE_HZ)
        assertEquals(1, EngineSoundSpec.CHANNELS)
        assertEquals(16, EngineSoundSpec.BITS_PER_SAMPLE)
    }

    @Test
    fun durationWindowIs5to8Seconds() {
        val rate = EngineSoundSpec.SAMPLE_RATE_HZ
        assertTrue(EngineSoundSpec.validateSamples(rate * 5 - 1) != null)
        assertTrue(EngineSoundSpec.validateSamples(rate * 5) == null)
        assertTrue(EngineSoundSpec.validateSamples(rate * 8) == null)
        assertTrue(EngineSoundSpec.validateSamples(rate * 8 + 1) != null)
    }

    @Test
    fun explanationIsPlainAndMentionsMicAndWindow() {
        val text = EngineSoundSpec.MIC_EXPLANATION
        assertTrue(text.contains("microphone"))
        assertTrue(text.contains("5–8 seconds"))
        assertTrue(text.isNotEmpty())
    }

    @Test
    fun gateTextRequiresRunningEngineOutdoors() {
        val text = EngineSoundSpec.GATE_TEXT
        assertTrue(text.contains("outdoors"))
        assertTrue(text.contains("running"))
    }
}
