package dev.dreamz.mmc.session

import java.io.File
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

/**
 * Records an adapter session to a `.mmrec` zip (SPEC §6 "recorded sessions"):
 * raw transcript with millisecond offsets, events.jsonl, meta.json and a
 * manifest.json of sha256 hashes. Readable by backend/mmc/replay/mmrec.py.
 */
class Recorder {
    data class Row(val offsetMs: Long, val dir: String, val data: String)

    private val startNs = System.nanoTime()
    private val rows = mutableListOf<Row>()

    fun recordTx(line: String) {
        rows.add(Row(elapsed(), "tx", line))
    }

    fun recordRx(reply: String) {
        rows.add(Row(elapsed(), "rx", reply))
        // Field visibility: every raw adapter response line goes to logcat
        // tag MMC (VIN-masked). Runs for all transports because the app's
        // RecordingTransport funnels every exchange through here.
        for (line in reply.split('\r', '\n')) {
            val trimmed = line.trim()
            if (trimmed.isNotEmpty() && trimmed != ">") trafficLog(maskVins(trimmed))
        }
    }

    /**
     * Marker row for a failed live flow, so a flushed recording shows why it
     * stopped. ReplayTransport skips non-tx/rx rows, so replay is unaffected.
     */
    fun recordError(message: String) {
        rows.add(Row(elapsed(), "error", message))
    }

    /**
     * Flush the transcript so far plus a failure marker to [file]: field
     * failures leave a recording instead of vanishing. Call from the live
     * flow's catch block with the failure message.
     */
    fun writeFailureMmrec(
        file: File,
        metaJson: String,
        error: String,
        eventsJsonl: String = "",
    ): File {
        recordError(error)
        writeMmrec(file, metaJson, eventsJsonl)
        return file
    }

    private fun elapsed(): Long = (System.nanoTime() - startNs) / 1_000_000

    fun transcriptJsonl(): String = rows.joinToString("\n") {
        """{"t":${it.offsetMs},"dir":"${it.dir}","data":"${escape(it.data)}"}"""
    }.plus(if (rows.isEmpty()) "" else "\n")

    fun writeMmrec(file: File, metaJson: String, eventsJsonl: String = "") {
        val members = mapOf(
            "transcript.jsonl" to transcriptJsonl().toByteArray(Charsets.UTF_8),
            "events.jsonl" to eventsJsonl.toByteArray(Charsets.UTF_8),
            "meta.json" to metaJson.toByteArray(Charsets.UTF_8),
        )
        val manifest = buildString {
            append("""{"format":"mmrec/1","files":{""")
            append(members.entries.joinToString(",") { (name, bytes) -> """"$name":"${sha256(bytes)}"""" })
            append("}}")
        }
        ZipOutputStream(file.outputStream().buffered()).use { zip ->
            for ((name, bytes) in members) {
                zip.putNextEntry(ZipEntry(name))
                zip.write(bytes)
                zip.closeEntry()
            }
            zip.putNextEntry(ZipEntry("manifest.json"))
            zip.write(manifest.toByteArray(Charsets.UTF_8))
            zip.closeEntry()
        }
    }

    companion object {
        /**
         * Per-line adapter-traffic sink (logcat tag MMC on device). Mutable
         * for JVM tests; [resetTrafficLog] restores the logcat default.
         */
        var trafficLog: (String) -> Unit = { line ->
            try {
                android.util.Log.i("MMC", line)
            } catch (_: RuntimeException) {
                // JVM unit-test harness: android.jar methods are stubs.
            }
        }

        fun resetTrafficLog() {
            trafficLog = { line ->
                try {
                    android.util.Log.i("MMC", line)
                } catch (_: RuntimeException) {
                    // JVM unit-test harness: android.jar methods are stubs.
                }
            }
        }

        private val VIN_LIKE = Regex("(?<![A-Za-z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Za-z0-9])", RegexOption.IGNORE_CASE)

        /**
         * Mask any 17-char VIN to its last 6 ("····123456"). A standalone run
         * of packed hex can also be 17 VIN-alphabet chars, so a candidate is
         * only masked when it carries a non-hex letter (G–Z): real VINs
         * always do outside pathological cases, while packed OBD bytes never
         * do. Boundary guards keep longer hex runs intact.
         */
        fun maskVins(s: String): String =
            VIN_LIKE.replace(s) { m ->
                val upper = m.value.uppercase()
                if (upper.any { it in 'G'..'Z' }) "····" + upper.takeLast(6) else m.value
            }

        fun escape(s: String): String = buildString {
            for (c in s) {
                when (c) {
                    '\\' -> append("\\\\")
                    '"' -> append("\\\"")
                    '\r' -> append("\\r")
                    '\n' -> append("\\n")
                    else -> append(c)
                }
            }
        }

        fun sha256(bytes: ByteArray): String {
            val digest = MessageDigest.getInstance("SHA-256").digest(bytes)
            return digest.joinToString("") { "%02x".format(it) }
        }

        fun readMember(file: File, name: String): ByteArray {
            ZipFile(file).use { zip ->
                val entry = zip.getEntry(name) ?: error("missing member $name")
                return zip.getInputStream(entry).readBytes()
            }
        }
    }
}
