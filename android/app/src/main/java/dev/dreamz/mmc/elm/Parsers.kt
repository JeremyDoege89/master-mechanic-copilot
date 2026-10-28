package dev.dreamz.mmc.elm

/** Typed adapter/OBD results (SPEC §6 response handling). */
sealed interface ElmResult {
    data class Data(val ecus: List<EcuLine>) : ElmResult
    data class TypedError(val kind: ErrorKind) : ElmResult
}

data class EcuLine(val canId: String?, val bytes: ByteArray) {
    override fun equals(other: Any?): Boolean =
        other is EcuLine && canId == other.canId && bytes.contentEquals(other.bytes)
    override fun hashCode(): Int = 31 * (canId?.hashCode() ?: 0) + bytes.contentHashCode()
}

enum class ErrorKind {
    NO_DATA, CAN_ERROR, BUS_INIT_ERROR, BUFFER_FULL, STOPPED, UNABLE_TO_CONNECT, UNKNOWN_COMMAND
}

object Parsers {
    private val ERROR_STRINGS: List<Pair<String, ErrorKind>> = listOf(
        "BUS INIT: ...ERROR" to ErrorKind.BUS_INIT_ERROR,
        "BUS INIT: ERROR" to ErrorKind.BUS_INIT_ERROR,
        "UNABLE TO CONNECT" to ErrorKind.UNABLE_TO_CONNECT,
        "CAN ERROR" to ErrorKind.CAN_ERROR,
        "BUFFER FULL" to ErrorKind.BUFFER_FULL,
        "NO DATA" to ErrorKind.NO_DATA,
        "STOPPED" to ErrorKind.STOPPED,
        "?" to ErrorKind.UNKNOWN_COMMAND,
    )

    /**
     * Strip adapter text framing: cut at the first '>' prompt, drop carriage
     * returns, and return the first non-blank line. Single-line AT replies
     * such as ATDPN arrive as "A6\r\r>"; without this the raw framing leaks
     * into labels and stored profiles.
     */
    fun cleanAtResponse(raw: String): String =
        raw.substringBefore(">").replace("\r", "\n").lines()
            .map { it.trim() }.firstOrNull { it.isNotEmpty() }.orEmpty()

    /**
     * Parse one raw adapter reply (prompt '>' framing).
     * Strips SEARCHING... lines, maps error strings to [ErrorKind],
     * and splits data lines into per-ECU byte payloads.
     */
    fun frame(raw: String): ElmResult {
        val noPrompt = raw.substringBefore(">").replace("\r", "\n")
        val lines = noPrompt.lines().map { it.trim() }.filter { it.isNotEmpty() && it != "SEARCHING..." }
        if (lines.isEmpty()) return ElmResult.TypedError(ErrorKind.NO_DATA)
        for (line in lines) {
            for ((text, kind) in ERROR_STRINGS) {
                if (line.equals(text, ignoreCase = true)) return ElmResult.TypedError(kind)
            }
        }
        return ElmResult.Data(lines.map { parseDataLine(it) })
    }

    private fun parseDataLine(line: String): EcuLine {
        val tokens = line.split(Regex("\\s+")).filter { it.isNotEmpty() }
        if (tokens.size >= 2 && tokens[0].length == 3 && tokens[0].all { it.isDigit() || it in 'A'..'F' }) {
            val rest = tokens.drop(1).joinToString(" ")
            val bytes = hexBytes(rest)
            // First byte is the PCI/length byte when headers are on; keep payload after it
            // if it plausibly counts the remaining bytes.
            val payload = if (bytes.isNotEmpty() && (bytes[0].toInt() and 0xFF) == bytes.size - 1) bytes.drop(1).toByteArray() else bytes
            return EcuLine(tokens[0], payload)
        }
        return EcuLine(null, hexBytes(line))
    }

    fun hexBytes(hex: String): ByteArray {
        val clean = hex.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }
        return ByteArray(clean.size) { i -> clean[i].toInt(16).toByte() }
    }

    fun hexOf(bytes: ByteArray): String = bytes.joinToString(" ") { "%02X".format(it) }

    /**
     * Reassemble an ISO-TP multi-frame payload from "N:" numbered lines
     * (first "0:" line carries a 1-byte total-length prefix) or from raw
     * PCI frames ("10 LL ..", "21 ..", "22 .."). [lines] are data-line
     * strings without any CAN-id prefix.
     */
    fun reassembleIsoTp(lines: List<String>): ByteArray {
        require(lines.isNotEmpty()) { "no frames" }
        val first = lines[0].trim()
        return if (first.matches(Regex("[0-9A-Fa-f]:.*"))) {
            val chunks = lines.map { ln ->
                val t = ln.trim()
                hexBytes(t.substringAfter(":"))
            }
            val total = chunks[0][0].toInt() and 0xFF
            val all = chunks.flatMapIndexed { i, c -> if (i == 0) c.drop(1) else c.toList() }.toByteArray()
            require(all.size >= total) { "short ISO-TP payload" }
            all.copyOf(total)
        } else {
            // PCI framing: first byte 0x10 | len-high, second byte len-low.
            val firstBytes = hexBytes(first)
            require(firstBytes[0].toInt() and 0xF0 == 0x10) { "not a first frame" }
            val total = ((firstBytes[0].toInt() and 0x0F) shl 8) or (firstBytes[1].toInt() and 0xFF)
            val rest = firstBytes.drop(2).toByteArray()
            val cont = lines.drop(1).flatMap { hexBytes(it.trim()).drop(1).toList() }.toByteArray()
            (rest + cont).copyOf(total)
        }
    }

    /** Supported-PID bitmap: 4 data bytes MSB-first, PID [pid] supported? */
    fun isPidSupported(bitmap: ByteArray, pid: Int): Boolean {
        require(bitmap.size >= 4)
        val bit = pid - 1
        return (bitmap[bit / 8].toInt() shr (7 - (bit % 8)) and 1) == 1
    }

    /** Mode 01 PID 01: number of stored DTCs (bits 0-6 of first data byte). */
    fun dtcCount(firstDataByte: Byte): Int = firstDataByte.toInt() and 0x7F

    /**
     * Decode DTCs from Mode 03/07/0A payload bytes (2 bytes per code,
     * P/C/B/U from the top two bits). [payload] excludes the mode byte.
     */
    fun decodeDtcs(payload: ByteArray): List<String> {
        val table = charArrayOf('P', 'C', 'B', 'U')
        require(payload.size % 2 == 0) { "odd DTC payload" }
        return payload.toList().chunked(2).map { (b0, b1) ->
            val u0 = b0.toInt() and 0xFF
            val u1 = b1.toInt() and 0xFF
            "" + table[(u0 shr 6) and 0x03] +
                ('0' + ((u0 shr 4) and 0x03)) +
                Integer.toHexString(u0 and 0x0F).uppercase() +
                Integer.toHexString((u1 shr 4) and 0x0F).uppercase() +
                Integer.toHexString(u1 and 0x0F).uppercase()
        }
    }

    data class Mode06Entry(val mid: Int, val tid: Int, val value: Int, val min: Int, val max: Int)

    /** Parse Mode 06 payload (excludes mode byte) as 8-byte MID/TID/value/min/max entries. */
    fun parseMode06(payload: ByteArray): List<Mode06Entry> {
        require(payload.size % 8 == 0) { "mode06 payload not a multiple of 8" }
        return payload.toList().chunked(8).map { c ->
            Mode06Entry(
                mid = c[0].toInt() and 0xFF,
                tid = c[1].toInt() and 0xFF,
                value = ((c[2].toInt() and 0xFF) shl 8) or (c[3].toInt() and 0xFF),
                min = ((c[4].toInt() and 0xFF) shl 8) or (c[5].toInt() and 0xFF),
                max = ((c[6].toInt() and 0xFF) shl 8) or (c[7].toInt() and 0xFF),
            )
        }
    }

    /** VIN from a reassembled Mode 09 PID 02 payload (49 02 01 + 17 ASCII bytes). */
    fun parseVin(payload: ByteArray): String {
        require(payload.size >= 20 && payload[0] == 0x49.toByte() && payload[1] == 0x02.toByte()) {
            "not a VIN payload"
        }
        return payload.copyOfRange(3, 20).toString(Charsets.US_ASCII)
    }

    private operator fun ByteArray.plus(other: ByteArray): ByteArray {
        val out = ByteArray(size + other.size)
        copyInto(out)
        other.copyInto(out, size)
        return out
    }
}
