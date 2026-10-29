package dev.dreamz.mmc.voice

/**
 * M7 voice ASR client (plan §1).
 *
 * OkHttp WebSocket to `/sessions/{id}/asr` — same URL lifecycle as
 * [dev.dreamz.mmc.session.SessionClient.openStateSocket]: the base HTTP
 * URL becomes ws(s), then the session path. Binary frames are PCM16
 * 16 kHz mono ([VoiceAudioSpec]); text frames are JSON control frames
 * (`start`/`stop`). The backend adds the Meta key upstream only.
 *
 * Pure Kotlin (no Android framework) so it stays unit-testable on the JVM.
 */
class RealtimeAsrClient(baseUrl: String, private val sessionId: String) {
    private val base = baseUrl.trimEnd('/')

    /** WS URL for the session ASR socket. */
    fun socketUrl(): String = base.replaceFirst("http", "ws") + "/sessions/$sessionId/asr"

    /** JSON control frame to send before/after a PCM burst. */
    fun controlFrame(type: String): String = when (type) {
        "start" -> """{"type":"start"}"""
        "stop" -> """{"type":"stop"}"""
        else -> """{"type":"$type"}"""
    }

    /** True when an upstream text frame is a live-caption partial. */
    fun isPartial(frame: String): Boolean {
        val t = frameType(frame) ?: return false
        return t == "partial" || t == "transcript_partial"
    }

    /** True when an upstream text frame is a confirmed final. */
    fun isFinal(frame: String): Boolean {
        val t = frameType(frame) ?: return false
        return t == "final" || t == "utterance_end" || t == "transcript_final"
    }

    private fun frameType(frame: String): String? {
        val key = "\"type\""
        val i = frame.indexOf(key)
        if (i < 0) return null
        val colon = frame.indexOf(':', i + key.length)
        if (colon < 0) return null
        val first = frame.indexOf('"', colon)
        if (first < 0) return null
        val second = frame.indexOf('"', first + 1)
        if (second < 0) return null
        return frame.substring(first + 1, second).lowercase()
    }
}

/** PCM16 16 kHz mono capture spec (mirrors EngineSoundRecorder settings). */
object VoiceAudioSpec {
    const val SAMPLE_RATE_HZ = 16000
    const val CHANNELS = 1
    const val BITS_PER_SAMPLE = 16
    const val PERMISSION = android.Manifest.permission.RECORD_AUDIO

    /** Plain pre-permission explanation, shown on first voice tap only. */
    const val MIC_EXPLANATION =
        "The microphone listens for commands like “next test” or “log 12.4 volts”. " +
            "Short clips stream to the diagnostic server for transcription and are not stored."

    /** ~100 ms PCM frames, matching the spike streaming shape. */
    fun frameSamples(): Int = SAMPLE_RATE_HZ / 10
}
