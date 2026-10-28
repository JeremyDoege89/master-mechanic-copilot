package dev.dreamz.mmc.elm

import dev.dreamz.mmc.adapter.Transport
import kotlin.math.exp
import kotlin.math.pow

enum class Personality { GENUINE, GOOD_CLONE, BAD_CLONE }

enum class Scenario {
    HEALTHY, MAF_UNPLUGGED, IAT_UNPLUGGED, VACUUM_LEAK,
    FUEL_FUSE_REMOVED, COIL_UNPLUGGED, THERMOSTAT_STUCK_OPEN,
}

/**
 * In-process ELM327 + vehicle simulator (SPEC §6). Implements [Transport] so
 * the real driver runs against it. Deterministic: no randomness.
 *
 * Vehicle model: ECT first-order warm-up (tau 300 s) to 95 °C, or a 70 °C
 * plateau with a stuck-open thermostat; MAF from the SPEC §5 rule of thumb;
 * trims from injected leak scalars; O2 switching around 0.45 V.
 */
class Elm327Sim(
    val personality: Personality = Personality.GENUINE,
    val scenario: Scenario = Scenario.HEALTHY,
    val misfireCyl: Int = 3,
    val displacementL: Double = 2.0,
) : Transport {
    companion object {
        const val AMBIENT_C = 20.0
        const val WARM_TARGET_C = 95.0
        const val STUCK_TARGET_C = 70.0
        const val WARM_TAU_S = 300.0
        const val IDLE_RPM = 750.0
        const val VIN = "1FATP8UH5P5123456"
    }

    var simTimeSec: Double = 1200.0
    var rpm: Double = IDLE_RPM
    var headersOn: Boolean = true

    fun advance(seconds: Double) {
        simTimeSec += seconds
    }

    fun ect(): Double {
        val target = if (scenario == Scenario.THERMOSTAT_STUCK_OPEN) STUCK_TARGET_C else WARM_TARGET_C
        return target - (target - AMBIENT_C) * exp(-simTimeSec / WARM_TAU_S)
    }

    fun maf(): Double = when {
        scenario == Scenario.FUEL_FUSE_REMOVED -> 0.0
        scenario == Scenario.MAF_UNPLUGGED -> 0.0
        rpm <= 0 -> 0.0
        else -> (rpm / 120.0) * displacementL * 0.8 * 1.18 * 0.3
    }

    /** Total trim (STFT+LTFT) at the current RPM. */
    fun totalTrim(): Double = when (scenario) {
        Scenario.VACUUM_LEAK -> 18.0 * (IDLE_RPM / rpm).pow(1.2)
        Scenario.MAF_UNPLUGGED -> 12.0
        Scenario.HEALTHY, Scenario.COIL_UNPLUGGED, Scenario.THERMOSTAT_STUCK_OPEN,
        Scenario.IAT_UNPLUGGED -> 1.5
        Scenario.FUEL_FUSE_REMOVED -> 0.0
    }

    fun iat(): Double = if (scenario == Scenario.IAT_UNPLUGGED) -40.0 else AMBIENT_C + 5.0

    fun storedDtcs(): List<String> = when (scenario) {
        Scenario.MAF_UNPLUGGED -> listOf("P0102")
        Scenario.IAT_UNPLUGGED -> listOf("P0113")
        Scenario.COIL_UNPLUGGED -> listOf("P030$misfireCyl")
        Scenario.THERMOSTAT_STUCK_OPEN -> listOf("P0128")
        else -> emptyList()
    }

    fun misfireCounts(): Map<Int, Int> =
        (1..4).associateWith { if (scenario == Scenario.COIL_UNPLUGGED && it == misfireCyl) 45 else 0 }

    private fun dtcToBytes(dtc: String): Pair<Byte, Byte> {
        val typeBits = mapOf('P' to 0, 'C' to 1, 'B' to 2, 'U' to 3)[dtc[0]]!!
        val b0 = (typeBits shl 6) or (dtc[1].digitToInt() shl 4) or dtc[2].digitToInt(16)
        val b1 = (dtc[3].digitToInt(16) shl 4) or dtc[4].digitToInt(16)
        return b0.toByte() to b1.toByte()
    }

    private fun ecu(payload: ByteArray): String {
        val body = payload.joinToString(" ") { "%02X".format(it) }
        val len = "%02X".format(payload.size)
        return if (headersOn) "7E8 $len $body\r\r>" else "$body\r\r>"
    }

    private fun pidLine(pid: Int, data: ByteArray): String {
        val mode = (0x40 + 1).toByte()
        return ecu(byteArrayOf(mode, pid.toByte()) + data)
    }

    override fun transact(line: String, timeoutMs: Int): String {
        val cmd = line.trim().uppercase()
        // --- AT commands ---
        if (cmd == "ATZ") return banner() + "\r\r>"
        if (cmd == "ATE0" || cmd == "ATL0") return "OK\r\r>"
        if (cmd == "ATS0" || cmd == "ATAT1" || cmd == "ATST32") {
            return if (personality == Personality.BAD_CLONE && (cmd == "ATS0")) "?\r\r>" else "OK\r\r>"
        }
        if (cmd == "ATH1") {
            headersOn = true
            return "OK\r\r>"
        }
        if (cmd == "ATH0") {
            headersOn = false
            return "OK\r\r>"
        }
        if (cmd == "ATSP0") return "OK\r\r>"
        if (cmd == "ATDPN") return "A6\r\r>"
        if (cmd == "ATI") return banner() + "\r\r>"
        if (cmd == "AT@1" || cmd == "STI") return "?\r\r>"
        if (cmd == "ATPPS") return if (personality == Personality.BAD_CLONE) "?\r\r>" else "OK\r\r>"
        if (cmd == "ATCRA 7E8") return if (personality == Personality.BAD_CLONE) "?\r\r>" else "OK\r\r>"
        if (cmd == "ATSH 7E0") return if (personality == Personality.BAD_CLONE) "?\r\r>" else "OK\r\r>"
        if (cmd == "ATRV") return "14.2V\r\r>"
        if (cmd == "ATR1" || cmd == "ATR0") return if (personality == Personality.BAD_CLONE) "?\r\r>" else "OK\r\r>"
        if (cmd.startsWith("AT") || cmd == "STI") return "OK\r\r>"

        // --- BAD_CLONE truncates commands longer than 2 bytes ---
        val hex = cmd.replace(" ", "")
        val isHex = hex.all { it.isDigit() || it in 'A'..'F' }
        if (personality == Personality.BAD_CLONE && isHex && hex.length > 4) return "?\r\r>"

        // --- OBD services ---
        if (cmd == "0100") return ecu(byteArrayOf(0x41, 0x00, 0xBE.toByte(), 0x3E, 0xB8.toByte(), 0x11))
        if (cmd.startsWith("01") && cmd.length == 4) return singlePid(cmd.substring(2).toInt(16))
        if (cmd.startsWith("01") && cmd.length > 4) {
            if (personality == Personality.BAD_CLONE) return "NO DATA\r\r>"
            return multiPid(cmd.substring(2))
        }
        if (cmd == "03") {
            val dtcs = storedDtcs()
            if (dtcs.isEmpty()) return "NO DATA\r\r>"
            val bytes = dtcs.flatMap { dtcToBytes(it).toList() }.toByteArray()
            return ecu(byteArrayOf(0x43) + bytes)
        }
        if (cmd == "07" || cmd == "0A") return "NO DATA\r\r>"
        if (cmd == "06") return mode06()
        if (cmd == "0902") return vinFrames()
        if (cmd.startsWith("22")) {
            if (personality != Personality.GENUINE) return "?\r\r>"
            if (cmd == "22F190") {
                val vinBytes = VIN.toByteArray(Charsets.US_ASCII)
                val data = byteArrayOf(0x62, 0xF1.toByte(), 0x90.toByte()) + vinBytes
                return ecu(data)
            }
            return "NO DATA\r\r>"
        }
        if (cmd == "04") return "OK\r\r>"
        return "?\r\r>"
    }

    private fun banner(): String = when (personality) {
        Personality.BAD_CLONE -> "ELM327 v2.1"
        else -> "ELM327 v1.5"
    }

    private fun singlePid(pid: Int): String {
        if (scenario == Scenario.FUEL_FUSE_REMOVED && pid != 0x01) return "NO DATA\r\r>"
        val data: ByteArray = when (pid) {
            0x01 -> byteArrayOf(if (storedDtcs().isEmpty()) 0x00 else 0x82.toByte(), 0x00, 0x00, 0x00)
            0x04 -> byteArrayOf(((30.0 * 255 / 100).toInt()).toByte())
            0x05 -> Pids.encodeTempC(ect())
            0x0C -> Pids.encodeRpm(if (scenario == Scenario.FUEL_FUSE_REMOVED) 0.0 else rpm)
            0x0D -> byteArrayOf(0x00)
            0x0F -> Pids.encodeTempC(iat())
            0x11 -> byteArrayOf(0x1F)
            0x06 -> Pids.encodeTrim(totalTrim() / 2)
            0x07 -> Pids.encodeTrim(totalTrim() / 2)
            0x10 -> Pids.encodeMaf(maf())
            0x0B -> byteArrayOf(0x1E)
            0x33 -> byteArrayOf(0x64)
            0x42 -> byteArrayOf(0x37, 0x78)
            0x14 -> byteArrayOf(0x70) // ~0.56 V mid-switch
            else -> return "NO DATA\r\r>"
        }
        return pidLine(pid, data)
    }

    private fun multiPid(hex: String): String {
        val pids = hex.chunked(2).map { it.toInt(16) }
        if (pids.size > 6) return "?\r\r>"
        val out = mutableListOf<Byte>()
        out.add(0x41)
        for (pid in pids) {
            val line = singlePid(pid)
            if (line.contains("NO DATA")) return "NO DATA\r\r>"
            // singlePid returns framed text; re-derive raw data bytes instead:
            out.add(pid.toByte())
            val data = pidDataForMulti(pid)
            out.addAll(data.toList())
        }
        return ecu(out.toByteArray())
    }

    private fun pidDataForMulti(pid: Int): ByteArray {
        val framed = Parsers.frame(singlePid(pid))
        require(framed is ElmResult.Data)
        return framed.ecus.single().bytes.copyOfRange(2, framed.ecus.single().bytes.size)
    }

    private fun mode06(): String {
        val out = mutableListOf<Byte>()
        out.add(0x46)
        for (cyl in 1..4) {
            val count = misfireCounts()[cyl]!!
            out.add(0x0B.toByte())
            out.add(cyl.toByte())
            out.add((count shr 8).toByte())
            out.add(count.toByte())
            out.add(0x00)
            out.add(0x00)
            out.add(0x00)
            out.add(0x0F)
        }
        val payload = out.toByteArray()
        // Numbered ISO-TP lines like the golden transcript style.
        val first = payload.copyOfRange(0, 9)
        val second = payload.copyOfRange(9, 20)
        val third = payload.copyOfRange(20, payload.size)
        fun hex(bs: ByteArray) = bs.joinToString(" ") { "%02X".format(it) }
        return "0: ${"%02X".format(payload.size)} ${hex(first)}\r" +
            "1: ${hex(second)}\r2: ${hex(third)}\r\r>"
    }

    private fun vinFrames(): String {
        val vinBytes = VIN.toByteArray(Charsets.US_ASCII)
        val data = byteArrayOf(0x49, 0x02, 0x01) + vinBytes
        val first = data.copyOfRange(0, 9)
        val second = data.copyOfRange(9, data.size)
        fun hex(bs: ByteArray) = bs.joinToString(" ") { "%02X".format(it) }
        return "0: ${"%02X".format(data.size)} ${hex(first)}\r1: ${hex(second)}\r\r>"
    }
}
