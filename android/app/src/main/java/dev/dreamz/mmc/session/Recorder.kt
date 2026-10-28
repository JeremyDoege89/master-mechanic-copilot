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
