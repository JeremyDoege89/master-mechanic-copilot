package dev.dreamz.mmc.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * M7 review #5: the Python/Kotlin grammar parses one shared corpus.
 *
 * The canonical corpus is `backend/tests/fixtures/voice_corpus.json`; the
 * copy under `src/test/resources` is byte-checked against it by the
 * backend suite (`test_android_corpus_mirror_matches`). This test prefers
 * the canonical file (found via the module working dir) and falls back to
 * the resource copy, so both parsers assert the same transcripts.
 *
 * No org.json: local JVM unit tests run against android.jar stubs, so the
 * corpus (a controlled flat shape) is parsed with the minimal reader below.
 */
class VoiceCorpusTest {

    // -- minimal JSON reader (flat objects/arrays/strings/numbers only) --

    private class Reader(val s: String) {
        var i = 0
        fun ws() { while (i < s.length && s[i].isWhitespace()) i++ }
        fun parse(): Any? {
            ws()
            return when (s[i]) {
                '{' -> obj()
                '[' -> arr()
                '"' -> str()
                't' -> { i += 4; true }
                'f' -> { i += 5; false }
                'n' -> { i += 4; null }
                else -> num()
            }
        }
        fun obj(): Map<String, Any?> {
            val m = LinkedHashMap<String, Any?>()
            i++ // {
            ws()
            if (s[i] == '}') { i++; return m }
            while (true) {
                ws(); val k = str() as String
                ws(); check(s[i] == ':') { "want : at $i" }; i++
                m[k] = parse()
                ws()
                if (s[i] == ',') { i++; continue }
                check(s[i] == '}') { "want } at $i" }; i++; return m
            }
        }
        fun arr(): List<Any?> {
            val l = mutableListOf<Any?>()
            i++ // [
            ws()
            if (s[i] == ']') { i++; return l }
            while (true) {
                l.add(parse()); ws()
                if (s[i] == ',') { i++; continue }
                check(s[i] == ']') { "want ] at $i" }; i++; return l
            }
        }
        fun str(): String {
            check(s[i] == '"'); i++
            val sb = StringBuilder()
            while (s[i] != '"') {
                if (s[i] == '\\') { i++; sb.append(s[i]); i++ } else { sb.append(s[i]); i++ }
            }
            i++
            return sb.toString()
        }
        fun num(): Double {
            val j = i
            while (i < s.length && (s[i].isDigit() || s[i] in "+-eE.")) i++
            return s.substring(j, i).toDouble()
        }
    }

    private fun corpusText(): String {
        val candidates = listOf(
            File("../../backend/tests/fixtures/voice_corpus.json"),
            File("../backend/tests/fixtures/voice_corpus.json"),
            File("backend/tests/fixtures/voice_corpus.json"),
        )
        for (f in candidates) {
            if (f.isFile) return f.readText()
        }
        val stream = javaClass.getResourceAsStream("/voice_corpus.json")
            ?: error("voice_corpus.json not found (tried $candidates + test resources)")
        return stream.bufferedReader().readText()
    }

    @Test
    fun sharedCorpusParsesIdentically() {
        @Suppress("UNCHECKED_CAST")
        val root = Reader(corpusText()).parse() as Map<String, Any?>
        @Suppress("UNCHECKED_CAST")
        val cases = root["cases"] as List<Map<String, Any?>>
        assertTrue("corpus must not be empty", cases.isNotEmpty())
        for (case in cases) {
            val transcript = case["transcript"] as String
            @Suppress("UNCHECKED_CAST")
            val expect = case["expect"] as Map<String, Any?>
            val intent = VoiceCommands.parse(transcript)
            assertEquals(transcript, expect["intent"], intentName(intent))
            when (expect["intent"]) {
                "log_trim" -> {
                    val trim = intent as VoiceIntent.LogTrim
                    assertEquals(transcript, expect["part"] as String?, trim.part)
                    assertEquals(transcript, (expect["value"] as Double), trim.value, 1e-9)
                }
                "log_voltage" -> {
                    val volts = intent as VoiceIntent.LogVoltage
                    assertEquals(transcript, (expect["voltage_v"] as Double), volts.volts, 1e-9)
                }
                "level" -> {
                    val level = intent as VoiceIntent.Level
                    assertEquals(transcript, expect["level"], level.level)
                }
            }
        }
    }

    private fun intentName(intent: VoiceIntent): String = when (intent) {
        is VoiceIntent.NextTest -> "next_test"
        is VoiceIntent.LogVoltage -> "log_voltage"
        is VoiceIntent.LogTrim -> "log_trim"
        is VoiceIntent.Repeat -> "repeat"
        is VoiceIntent.Risk -> "risk"
        is VoiceIntent.Level -> "level"
        is VoiceIntent.Confirm -> "confirm"
        is VoiceIntent.Cancel -> "cancel"
        is VoiceIntent.Unknown -> "unknown"
    }
}
