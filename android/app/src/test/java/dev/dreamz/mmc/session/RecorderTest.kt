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
    fun test_records_while_streaming() {
        // Interleaved record + stream, as the Live screen does: the .mmrec
        // keeps growing while rows are still being appended.
        val rec = Recorder()
        val file = java.io.File.createTempFile("recorder-streaming", ".mmrec")
        try {
            for (i in 1..20) {
                rec.recordTx("010C")
                rec.recordRx("7E8 04 41 0C 1A F8\r\r>")
                if (i % 5 == 0) {
                    rec.writeMmrec(
                        file,
                        metaJson = """{"format":"mmrec/1","rows":$i}""",
                    )
                    val text = Recorder.readMember(file, "transcript.jsonl")
                        .toString(Charsets.UTF_8)
                    val rows = text.lines().filter { it.isNotBlank() }
                    assertEquals(2 * i, rows.size)
                }
            }
            val text = Recorder.readMember(file, "transcript.jsonl").toString(Charsets.UTF_8)
            assertTrue(text.contains("\"dir\":\"tx\""))
            assertTrue(text.contains("\"dir\":\"rx\""))
            val manifest = Recorder.readMember(file, "manifest.json").toString(Charsets.UTF_8)
            assertTrue(manifest.contains(Recorder.sha256(
                Recorder.readMember(file, "transcript.jsonl"))))
        } finally {
            file.delete()
        }
    }

    @Test
    fun failureFlushKeepsTranscriptAndErrorMarker() {
        val rec = Recorder()
        rec.recordTx("010C")
        rec.recordRx("7E804410C1AF8\r\r>")
        val file = java.io.File.createTempFile("recorder-failure", ".mmrec")
        try {
            rec.writeFailureMmrec(
                file,
                metaJson = """{"format":"mmrec/1","failed":true}""",
                error = "live flow failed: no data for PID 0c",
            )
            val text = Recorder.readMember(file, "transcript.jsonl").toString(Charsets.UTF_8)
            assertTrue(text.contains("\"dir\":\"tx\""))
            assertTrue(text.contains("\"dir\":\"rx\""))
            assertTrue(text.contains("\"dir\":\"error\""))
            assertTrue(text.contains("live flow failed"))
            val manifest = Recorder.readMember(file, "manifest.json").toString(Charsets.UTF_8)
            assertTrue(manifest.contains(Recorder.sha256(Recorder.readMember(file, "transcript.jsonl"))))
            // Replay still feeds the tx/rx pairs, skipping the error row.
            val events = ReplayTransport.parseTranscriptJsonl(text)
            assertEquals(1, events.size)
            assertEquals("010C", events[0].request)
        } finally {
            file.delete()
        }
    }

    @Test
    fun rxLinesLogWithVinMaskedToLast6() {
        val seen = mutableListOf<String>()
        Recorder.trafficLog = { seen.add(it) }
        try {
            val rec = Recorder()
            rec.recordTx("0902") // commands carry no VIN: nothing logged
            rec.recordRx("7E810144902014A4D31\r7E821424D394D373253\r\r>")
            rec.recordRx("VIN JM1BM9M72S1000001 ok\r\r>")
        } finally {
            Recorder.resetTrafficLog()
        }
        assertTrue(seen.any { it.startsWith("7E8") })
        assertTrue("no raw VIN may reach the log: $seen", seen.none { it.contains("JM1BM9M72S1000001") })
        assertTrue("last 6 kept: $seen", seen.any { it.contains("····000001") })
    }

    @Test
    fun vinMaskingKeepsLast6AndLeavesHexAlone() {
        assertEquals("····000001", Recorder.maskVins("JM1BM9M72S1000001"))
        assertEquals("VIN ····000001 ok", Recorder.maskVins("VIN JM1BM9M72S1000001 ok"))
        // Packed hex runs are never masked (no non-hex letter / bounded).
        assertEquals("7E804410C1AF8", Recorder.maskVins("7E804410C1AF8"))
        assertEquals("7E810144902014A4D31", Recorder.maskVins("7E810144902014A4D31"))
        assertEquals("014A4D31424D394D3", Recorder.maskVins("014A4D31424D394D3"))
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
