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
        "sixty" to 60.0, "seventy" to 70.0, "eighty" to 80.0, "ninety" to 90.0,
    )

    private val numberVocab: Set<String> =
        numberWords.keys + setOf("point", "dot", "plus", "minus", "negative", "+", "-", "and")

    /** M7 review #5: 2500-rpm condition tokens — never the measurement. */
    private val rpmTokens = setOf("2500", "rpm", "rpms", "revved", "revs", "rev")

    private val unitTokens = setOf("volt", "volts", "voltage", "v")

    fun normalize(text: String): String {
        val sb = StringBuilder()
        for (ch in text.lowercase()) {
            if (ch.isLetterOrDigit() || ch == '+' || ch == '-' || ch == '.' || ch.isWhitespace()) sb.append(ch)
            else sb.append(' ')
        }
        return sb.toString().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ")
    }

    private fun numberTokens(tokens: List<String>): List<String> =
        tokens.filter { it in numberVocab || it.toDoubleOrNull() != null }

    private fun numberFrom(tokens: List<String>): Double? {
        if (tokens.isEmpty()) return null
        for (tok in tokens) {
            tok.toDoubleOrNull()?.let { return it }
        }
        var sign = 1.0
        var words = tokens
        if (words.first() in setOf("plus", "minus", "negative", "+", "-")) {
            if (words.first() in setOf("minus", "negative", "-")) sign = -1.0
            words = words.drop(1)
        }
        if (words.isEmpty()) return null
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

    /** Number run immediately left of the last unit token (mirrors backend). */
    private fun valueNearUnit(tokens: List<String>): Double? {
        val unitAt = tokens.indexOfLast { it in unitTokens }
        if (unitAt < 0) return null
        val run = mutableListOf<String>()
        for (i in unitAt - 1 downTo 0) {
            val t = tokens[i]
            if (t in numberVocab || t.toDoubleOrNull() != null) run.add(0, t) else break
        }
        if (run.isEmpty()) return null
        return numberFrom(run)
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
            // M7 review #5: drop the rpm-condition tokens before extraction.
            val candidates = tokens.filter {
                it !in setOf("log", "idle", "cruise", "trim", "trims") && it !in rpmTokens
            }
            val value = numberFrom(numberTokens(candidates)) ?: return VoiceIntent.Unknown(text)
            return VoiceIntent.LogTrim(part, value)
        }
        if (joined.contains("volt") && "log" in tokens) {
            // M7 review #5: prefer the number next to the unit.
            val value = valueNearUnit(tokens)
                ?: numberFrom(numberTokens(tokens.filter { it !in rpmTokens }))
                ?: return VoiceIntent.Unknown(text)
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

/**
 * Pure confirm-path helper (JVM-testable): pending intent -> test submission.
 *
 * The backend re-parses on confirm (single source of truth); this shapes the
 * exact measured payload the app will POST to the test-result route, or null
 * when more speech is needed (trim needs both idle and cruise halves).
 */
object VoiceConfirm {
    data class Submission(val testRef: String, val measured: Map<String, Any>)

    /** Fold a trim intent into staged halves (mirrors backend stage_trim). */
    fun stageTrim(staged: Map<String, Double>, intent: VoiceIntent.LogTrim): Map<String, Double> {
        val out = staged.toMutableMap()
        when (intent.part) {
            "idle" -> out["idle_trim_pct"] = intent.value
            "cruise" -> out["cruise_trim_pct"] = intent.value
            else -> if (!out.containsKey("idle_trim_pct")) out["idle_trim_pct"] = intent.value
            else out["cruise_trim_pct"] = intent.value
        }
        return out
    }

    fun submissionFor(pending: VoiceIntent?, staged: Map<String, Double>): Submission? = when (pending) {
        is VoiceIntent.LogVoltage -> Submission(
            pending.testRef,
            // Voice logging happens with the engine available on the Live
            // screen; the pending card shows this assumption verbatim.
            mapOf("voltage_v" to pending.volts, "engine_on" to true),
        )
        is VoiceIntent.LogTrim -> {
            val idle = staged["idle_trim_pct"]
            val cruise = staged["cruise_trim_pct"]
            if (idle == null || cruise == null) null
            else Submission(
                "trim_idle_vs_2500",
                mapOf("idle_trim_pct" to idle, "cruise_trim_pct" to cruise),
            )
        }
        else -> null
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
