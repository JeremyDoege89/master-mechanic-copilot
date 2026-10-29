package dev.dreamz.mmc.voice

/**
 * M7 deterministic voice grammar mirror (JVM side).
 *
 * The backend (`backend/mmc/voice_commands.py`) is the single source of
 * truth and re-parses on confirm; this mirror only shapes the pending card
 * and the spoken reply from the same fixture strings. Voice never triggers
 * clear-codes: no intent maps to the clear-codes route.
 */

sealed interface VoiceIntent {
    data object NextTest : VoiceIntent
    data class LogVoltage(val volts: Double, val testRef: String = "battery_voltage_rest_running") : VoiceIntent
    data class LogTrim(val part: String?, val value: Double) : VoiceIntent
    data object Repeat : VoiceIntent
    data object Risk : VoiceIntent
    data class Level(val level: String) : VoiceIntent
    data object Confirm : VoiceIntent
    data object Cancel : VoiceIntent
    data class Unknown(val text: String) : VoiceIntent
}

object VoiceCommands {
    private val numberWords = mapOf(
        "zero" to 0.0, "one" to 1.0, "two" to 2.0, "three" to 3.0,
        "four" to 4.0, "five" to 5.0, "six" to 6.0, "seven" to 7.0,
        "eight" to 8.0, "nine" to 9.0, "ten" to 10.0, "eleven" to 11.0,
        "twelve" to 12.0, "thirteen" to 13.0, "fourteen" to 14.0,
        "fifteen" to 15.0, "sixteen" to 16.0, "seventeen" to 17.0,
        "eighteen" to 18.0, "nineteen" to 19.0, "twenty" to 20.0,
        "thirty" to 30.0, "forty" to 40.0, "fifty" to 50.0,
    )

    fun normalize(text: String): String {
        val sb = StringBuilder()
        for (ch in text.lowercase()) {
            if (ch.isLetterOrDigit() || ch == '+' || ch == '-' || ch == '.' || ch.isWhitespace()) sb.append(ch)
            else sb.append(' ')
        }
        return sb.toString().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
    }

    private fun numberFrom(tokens: List<String>): Double? {
        val kept = tokens.filter { it in numberWords || it in setOf("point", "dot", "plus", "minus") || it.toDoubleOrNull() != null }
        if (kept.isEmpty()) return null
        kept.firstOrNull()?.toDoubleOrNull()?.let { if (kept.size == 1) return it }
        var sign = 1.0
        var words = kept
        if (words.first() == "plus") words = words.drop(1)
        if (words.firstOrNull() == "minus") {
            sign = -1.0
            words = words.drop(1)
        }
        val sep = words.indexOfFirst { it == "point" || it == "dot" }
        if (sep >= 0) {
            val intPart = wordsToInt(words.subList(0, sep)) ?: return null
            var frac = ""
            for (w in words.subList(sep + 1, words.size)) {
                val v = wordsToInt(listOf(w)) ?: return null
                if (v > 9) return null
                frac += v.toInt().toString()
            }
            if (frac.isEmpty()) return null
            return sign * "${intPart.toInt()}.$frac".toDouble()
        }
        return wordsToInt(words)?.let { sign * it }
    }

    private fun wordsToInt(words: List<String>): Double? {
        if (words.isEmpty()) return null
        var total = 0.0
        var seen = false
        for (w in words) {
            if (w == "and") continue
            val v = numberWords[w] ?: w.toDoubleOrNull() ?: return null
            seen = true
            total += v
        }
        return if (seen) total else null
    }

    /** Parse one transcript to a typed intent (never throws). */
    fun parse(text: String, focusedTestRef: String? = null): VoiceIntent {
        val norm = normalize(text)
        if (norm.isEmpty()) return VoiceIntent.Unknown(text)
        val tokens = norm.split(" ")
        val joined = tokens.joinToString(" ")
        if (joined in setOf("yes", "yeah", "yep", "confirm", "confirmed", "do it", "log it")) return VoiceIntent.Confirm
        if (joined in setOf("no", "nope", "cancel", "discard", "never mind", "stop")) return VoiceIntent.Cancel
        if ("next test" in joined) return VoiceIntent.NextTest
        if (joined in setOf("repeat", "read it again", "say again", "repeat that") || "repeat" in joined) {
            if (joined.contains("repeat") || joined == "read it again" || joined == "say again") return VoiceIntent.Repeat
        }
        if ("risk" in tokens) return VoiceIntent.Risk
        if ("level" in tokens) {
            for (t in tokens) {
                when (t) {
                    "one", "1" -> return VoiceIntent.Level("L1")
                    "two", "2" -> return VoiceIntent.Level("L2")
                    "three", "3" -> return VoiceIntent.Level("L3")
                }
            }
            return VoiceIntent.Unknown(text)
        }
        if ("trim" in tokens && "log" in tokens) {
            val part = when {
                "idle" in tokens -> "idle"
                "cruise" in tokens || "2500" in joined -> "cruise"
                else -> null
            }
            val value = numberFrom(tokens) ?: return VoiceIntent.Unknown(text)
            return VoiceIntent.LogTrim(part, value)
        }
        if (joined.contains("volt") && "log" in tokens) {
            val value = numberFrom(tokens) ?: return VoiceIntent.Unknown(text)
            return VoiceIntent.LogVoltage(value, focusedTestRef ?: "battery_voltage_rest_running")
        }
        return VoiceIntent.Unknown(text)
    }

    /** Spoken reply (Android TTS, on-device) for a parsed intent. */
    fun ttsText(intent: VoiceIntent, rankOneTitle: String = "the top test"): String = when (intent) {
        is VoiceIntent.NextTest -> "Next test: $rankOneTitle."
        is VoiceIntent.LogVoltage -> "Heard ${intent.volts} volts for ${intent.testRef}. Say confirm to log it, or cancel."
        is VoiceIntent.LogTrim -> if (intent.part == "idle") {
            "Heard idle trim ${intent.value}. Now tell me the 2500 rpm value."
        } else {
            "Heard trim ${intent.value}. Say confirm to log it, or cancel."
        }
        is VoiceIntent.Repeat -> "Repeating the last card."
        is VoiceIntent.Risk -> "Reading the safety checklist for $rankOneTitle."
        is VoiceIntent.Level -> "Showing ${intent.level} instructions."
        is VoiceIntent.Confirm -> "Confirmed."
        is VoiceIntent.Cancel -> "Discarded."
        is VoiceIntent.Unknown -> "Didn't catch that. Say next test, or log a reading."
    }
}

/** Hands-free voice panel state (captions + pending confirm + toggle). */
class VoiceUiState {
    var handsFree: Boolean = false
        private set
    var caption: String = ""
        private set
    var pending: VoiceIntent? = null
        private set
    var lastSpoken: String = ""
        private set

    fun setHandsFree(on: Boolean) {
        handsFree = on
    }

    fun onPartial(text: String) {
        caption = text
    }

    fun onFinal(text: String, focusedTestRef: String? = null, rankOneTitle: String = "the top test") {
        caption = text
        val intent = VoiceCommands.parse(text, focusedTestRef)
        lastSpoken = VoiceCommands.ttsText(intent, rankOneTitle)
        pending = when (intent) {
            is VoiceIntent.LogVoltage, is VoiceIntent.LogTrim -> intent
            else -> null
        }
        if (intent is VoiceIntent.Repeat) {
            // Re-read: keep the pending card, speak again via lastSpoken.
        }
    }

    fun clearPending() {
        pending = null
    }
}
