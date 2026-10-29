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
     *
     * Accepts both spaced ("7E8 04 41 0C 1A F8") and space-less
     * ("7E804410C1AF8") responses: with ATS0 the adapter runs the bytes
     * together, and splitting a packed line on spaces yields one long token
     * that overflows Integer.parseInt (Mazda 3 field log).
     *
     * @param headersOn false when the adapter runs ATH0 (no CAN-id prefix).
     * @param headerChars 3 for 11-bit CAN ids, 8 for 29-bit (ATDPN A7).
     */
    fun frame(raw: String, headersOn: Boolean = true, headerChars: Int = 3): ElmResult {
        val noPrompt = raw.substringBefore(">").replace("\r", "\n")
        val lines = noPrompt.lines().map { it.trim() }.filter { it.isNotEmpty() && it != "SEARCHING..." }
        if (lines.isEmpty()) return ElmResult.TypedError(ErrorKind.NO_DATA)
        for (line in lines) {
            for ((text, kind) in ERROR_STRINGS) {
                if (line.equals(text, ignoreCase = true)) return ElmResult.TypedError(kind)
            }
        }
        return ElmResult.Data(lines.map { parseDataLine(it, headersOn, headerChars) })
    }

    private fun Char.isHexDigit(): Boolean =
        this in '0'..'9' || this in 'A'..'F' || this in 'a'..'f'

    private fun isCanId(token: String, headerChars: Int): Boolean =
        token.length == headerChars && token.all { it.isHexDigit() }

    private fun dropPciLength(bytes: ByteArray): ByteArray {
        // First byte is the PCI/length byte; keep payload after it if it
        // plausibly counts the remaining bytes.
        return if (bytes.isNotEmpty() && (bytes[0].toInt() and 0xFF) == bytes.size - 1) {
            bytes.drop(1).toByteArray()
        } else {
            bytes
        }
    }

    private fun parseDataLine(line: String, headersOn: Boolean, headerChars: Int): EcuLine {
        val trimmed = line.trim()
        // ELM "N:" numbered ISO-TP format ("0: 14 49 02 ..." / "0:144902..."):
        // no CAN id, payload is everything after the colon.
        val colon = trimmed.indexOf(':')
        if (colon == 1 && trimmed[0].isHexDigit()) {
            return EcuLine(null, dropPciLength(hexBytes(trimmed.substring(colon + 1))))
        }
        if (trimmed.any { it.isWhitespace() }) {
            val tokens = trimmed.split(Regex("\\s+")).filter { it.isNotEmpty() }
            if (headersOn && tokens.size >= 2 && isCanId(tokens[0], headerChars)) {
                return EcuLine(tokens[0].uppercase(), dropPciLength(hexBytes(tokens.drop(1).joinToString(""))))
            }
            return EcuLine(null, dropPciLength(hexBytes(trimmed)))
        }
        val compact = trimmed
        if (headersOn && compact.length > headerChars &&
            isCanId(compact.substring(0, headerChars), headerChars)
        ) {
            val rest = compact.substring(headerChars)
            if (rest.length >= 2 && rest.length % 2 == 0 && rest.all { it.isHexDigit() }) {
                return EcuLine(compact.substring(0, headerChars).uppercase(), dropPciLength(hexBytes(rest)))
            }
        }
        return EcuLine(null, dropPciLength(hexBytes(compact)))
    }

    /**
     * Remainder of one data line after its CAN-id header, for the ISO-TP
     * reassembly path ([Elm327Driver.queryMode06]/[queryVin]). Handles spaced
     * ("7E8 10 11 ..."), packed ("7E81011...") and header-less lines, and
     * passes ELM "N:" numbered lines through untouched.
     */
    fun stripCanHeader(line: String, headersOn: Boolean = true, headerChars: Int = 3): String {
        val trimmed = line.trim()
        if (!headersOn) return trimmed
        val colon = trimmed.indexOf(':')
        if (colon == 1 && trimmed[0].isHexDigit()) return trimmed
        val space = trimmed.indexOfFirst { it.isWhitespace() }
        if (space >= 0) {
            val head = trimmed.substring(0, space)
            if (isCanId(head, headerChars)) return trimmed.substring(space).trim()
            return trimmed
        }
        if (trimmed.length > headerChars && isCanId(trimmed.substring(0, headerChars), headerChars)) {
            val rest = trimmed.substring(headerChars)
            if (rest.length >= 2 && rest.length % 2 == 0 && rest.all { it.isHexDigit() }) return rest
        }
        return trimmed
    }

    fun hexBytes(hex: String): ByteArray {
        val compact = hex.replace(Regex("\\s+"), "")
        if (compact.isEmpty()) return ByteArray(0)
        require(compact.length % 2 == 0) { "odd hex length: $hex" }
        return ByteArray(compact.length / 2) { i -> compact.substring(i * 2, i * 2 + 2).toInt(16).toByte() }
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
