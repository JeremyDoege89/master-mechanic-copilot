package dev.dreamz.mmc.session

import dev.dreamz.mmc.adapter.ReplayTransport
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RecorderTest {
    @Test
    fun mmrecContainsSpecMembersWithVerifyingManifest() {
        val rec = Recorder()
        rec.recordTx("ATZ")
        rec.recordRx("ELM327 v1.5\r\r>")
        rec.recordTx("010C")
        rec.recordRx("7E8 04 41 0C 1A F8\r\r>")

        val file = java.io.File.createTempFile("recorder-test", ".mmrec")
        try {
            rec.writeMmrec(
                file,
                metaJson = """{"format":"mmrec/1","scenario":"healthy"}""",
                eventsJsonl = """{"seq":1,"type":"session_started"}""" + "\n",
            )
            val manifest = Recorder.readMember(file, "manifest.json").toString(Charsets.UTF_8)
            for (name in listOf("transcript.jsonl", "events.jsonl", "meta.json")) {
                val bytes = Recorder.readMember(file, name)
                assertTrue("$name hash missing from manifest", manifest.contains(Recorder.sha256(bytes)))
            }
            // Offsets are non-decreasing.
            val text = Recorder.readMember(file, "transcript.jsonl").toString(Charsets.UTF_8)
            val offsets = text.lines().filter { it.isNotBlank() }
                .map { it.substringAfter("\"t\":").substringBefore(",").toLong() }
            assertEquals(offsets.sorted(), offsets)
        } finally {
            file.delete()
        }
    }

    @Test
    fun replayTransportRefeedReproducesTranscript() {
        val rec = Recorder()
        val script = listOf(
            "ATZ" to "ELM327 v1.5\r\r>",
            "010C" to "7E8 04 41 0C 1A F8\r\r>",
            "03" to "NO DATA\r\r>",
        )
        for ((tx, rx) in script) {
            rec.recordTx(tx)
            rec.recordRx(rx)
        }
        val events = ReplayTransport.parseTranscriptJsonl(rec.transcriptJsonl())
        assertEquals(3, events.size)
        val replay = ReplayTransport(events, timeScale = 0.001)
        for ((tx, rx) in script) {
            assertEquals(rx, replay.transact(tx, 300))
        }
    }
}
