package dev.dreamz.mmc.audio

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder

/**
 * Engine-sound capture spec (M5O §4 + phone addition).
 *
 * The squeal band reaches 6 kHz, so the rate is fixed at 44.1 kHz —
 * never a low telephony rate. Captures are 5–8 s of 16-bit mono PCM,
 * stored as WAV for the `/vision/engine-sound` backend endpoint.
 */
object EngineSoundSpec {
    const val SAMPLE_RATE_HZ = 44100
    const val CHANNELS = 1
    const val BITS_PER_SAMPLE = 16
    const val MIN_SECONDS = 5
    const val MAX_SECONDS = 8
    const val PERMISSION = android.Manifest.permission.RECORD_AUDIO

    /** Plain pre-permission explanation, shown before the system prompt. */
    const val MIC_EXPLANATION =
        "The microphone records 5–8 seconds of engine sound so the app can " +
            "listen for a misfire, a belt squeal or a vacuum hiss. " +
            "The clip is only used for this test."

    const val GATE_TEXT =
        "Engine running, outdoors (never in a closed garage). " +
            "Confirm before recording."

    fun minSamples(): Int = SAMPLE_RATE_HZ * MIN_SECONDS
    fun maxSamples(): Int = SAMPLE_RATE_HZ * MAX_SECONDS

    /** Null when the captured length is submittable, else the user message. */
    fun validateSamples(count: Int): String? = when {
        count < minSamples() -> "Too short — keep recording until 5 s."
        count > maxSamples() -> "Too long — clips stop automatically at 8 s."
        else -> null
    }
}

/** Pure 16-bit PCM → WAV writer (unit-tested on the JVM). */
object WavWriter {
    fun pcmToWav(
        samples: ShortArray,
        sampleRate: Int = EngineSoundSpec.SAMPLE_RATE_HZ,
        channels: Int = EngineSoundSpec.CHANNELS,
    ): ByteArray {
        val dataBytes = samples.size * 2
        val out = ByteArray(44 + dataBytes)
        fun putInt(off: Int, v: Int) {
            out[off] = (v and 0xFF).toByte()
            out[off + 1] = ((v shr 8) and 0xFF).toByte()
            out[off + 2] = ((v shr 16) and 0xFF).toByte()
            out[off + 3] = ((v shr 24) and 0xFF).toByte()
        }
        fun putShort(off: Int, v: Int) {
            out[off] = (v and 0xFF).toByte()
            out[off + 1] = ((v shr 8) and 0xFF).toByte()
        }
        fun putText(off: Int, s: String) {
            s.toByteArray(Charsets.US_ASCII).copyInto(out, off)
        }
        putText(0, "RIFF")
        putInt(4, 36 + dataBytes)
        putText(8, "WAVE")
        putText(12, "fmt ")
        putInt(16, 16)
        putShort(20, 1) // PCM
        putShort(22, channels)
        putInt(24, sampleRate)
        putInt(28, sampleRate * channels * 2)
        putShort(32, channels * 2)
        putShort(34, 16)
        putText(36, "data")
        putInt(40, dataBytes)
        var p = 44
        for (s in samples) {
            putShort(p, s.toInt())
            p += 2
        }
        return out
    }
}

/** Thin AudioRecord source, swappable in tests. */
interface AudioSource {
    /** Returns false when the microphone cannot be opened. */
    fun open(): Boolean
    fun read(buf: ShortArray, off: Int, len: Int): Int
    fun close()
}

private class RealAudioSource : AudioSource {
    private var record: AudioRecord? = null

    override fun open(): Boolean {
        val minBuf = AudioRecord.getMinBufferSize(
            EngineSoundSpec.SAMPLE_RATE_HZ,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuf <= 0) return false
        return try {
            val rec = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                EngineSoundSpec.SAMPLE_RATE_HZ,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBuf * 2, EngineSoundSpec.SAMPLE_RATE_HZ * 2),
            )
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                rec.release()
                return false
            }
            rec.startRecording()
            record = rec
            true
        } catch (e: Exception) {
            false
        }
    }

    override fun read(buf: ShortArray, off: Int, len: Int): Int =
        record?.read(buf, off, len) ?: AudioRecord.ERROR

    override fun close() {
        try {
            record?.stop()
        } catch (e: Exception) {
            // Already stopped — fall through to release.
        }
        record?.release()
        record = null
    }
}

/**
 * 5–8 s engine-sound capture on a background thread.
 *
 * [start] opens the mic and returns immediately; [stop] blocks for the
 * reader thread, validates the length and returns WAV bytes. Recording
 * auto-stops at [EngineSoundSpec.MAX_SECONDS].
 */
class EngineSoundRecorder(private val source: AudioSource = RealAudioSource()) {
    private val lock = Object()
    @Volatile private var running = false
    private var reader: Thread? = null
    private val captured = mutableListOf<Short>()
    private var startMs = 0L

    val isRecording: Boolean get() = running

    fun elapsedSec(): Int =
        if (!running) 0 else ((System.currentTimeMillis() - startMs) / 1000).toInt()

    fun start() {
        synchronized(lock) {
            if (running) return
            if (!source.open()) throw IllegalStateException("Microphone unavailable.")
            captured.clear()
            running = true
            startMs = System.currentTimeMillis()
            reader = Thread {
                val buf = ShortArray(EngineSoundSpec.SAMPLE_RATE_HZ / 2)
                while (running) {
                    val n = try {
                        source.read(buf, 0, buf.size)
                    } catch (e: Exception) {
                        break
                    }
                    if (n > 0) {
                        synchronized(lock) {
                            for (i in 0 until n) captured.add(buf[i])
                            if (captured.size >= EngineSoundSpec.maxSamples()) {
                                running = false
                            }
                        }
                    } else if (n == 0) {
                        try {
                            Thread.sleep(20)
                        } catch (e: InterruptedException) {
                            break
                        }
                    } else {
                        break
                    }
                }
            }.also { it.isDaemon = true; it.start() }
        }
    }

    fun stop(): ByteArray {
        running = false
        try {
            reader?.join(3000)
        } catch (e: InterruptedException) {
            // Fall through with whatever was captured.
        }
        source.close()
        val samples: ShortArray
        synchronized(lock) {
            samples = captured.toShortArray()
        }
        // Clamp over-long captures (auto-stop races) to 8 s.
        val trimmed = if (samples.size > EngineSoundSpec.maxSamples()) {
            samples.copyOf(EngineSoundSpec.maxSamples())
        } else {
            samples
        }
        EngineSoundSpec.validateSamples(trimmed.size)?.let { throw IllegalStateException(it) }
        return WavWriter.pcmToWav(trimmed)
    }
}
