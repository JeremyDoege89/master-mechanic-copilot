package dev.dreamz.mmc.elm

/** Mode 01 PID decode formulas + SPEC §6 polling priority groups. */
object Pids {
    /** Fast group polled at ~5 Hz (rpm, load, trims, MAF, O2). */
    val FAST_GROUP: List<Int> = listOf(0x0C, 0x04, 0x06, 0x07, 0x10, 0x14)

    /** Slow group polled at ~0.5 Hz (ECT, IAT, voltage, BARO). */
    val SLOW_GROUP: List<Int> = listOf(0x05, 0x0F, 0x42, 0x33)

    private fun u(b: Byte): Int = b.toInt() and 0xFF

    /**
     * Decode a Mode 01 PID payload ([data] excludes the "41 pid" header).
     * Returns value in the PID's natural units (rpm, %, °C, km/h, g/s, kPa, V).
     */
    fun decode(pid: Int, data: ByteArray): Double = when (pid) {
        0x04 -> u(data[0]) * 100.0 / 255.0 // calculated load %
        0x05 -> (u(data[0]) - 40).toDouble() // ECT °C
        0x0C -> ((u(data[0]) shl 8) or u(data[1])) / 4.0 // rpm
        0x0D -> u(data[0]).toDouble() // VSS km/h
        0x0F -> (u(data[0]) - 40).toDouble() // IAT °C
        0x11 -> u(data[0]) * 100.0 / 255.0 // TPS %
        0x06, 0x07, 0x08, 0x09 -> u(data[0]) * 100.0 / 128.0 - 100.0 // fuel trims %
        0x10 -> ((u(data[0]) shl 8) or u(data[1])) / 100.0 // MAF g/s
        0x0B -> u(data[0]).toDouble() // MAP kPa
        0x33 -> u(data[0]).toDouble() // BARO kPa
        0x42 -> ((u(data[0]) shl 8) or u(data[1])) / 1000.0 // control module V
        0x46 -> (u(data[0]) - 40).toDouble() // ambient °C
        0x0E -> u(data[0]) / 2.0 - 64.0 // timing advance °
        0x14, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B -> u(data[0]) / 200.0 // O2 V
        else -> throw IllegalArgumentException("unsupported PID ${pid.toString(16)}")
    }

    /** Encode helpers used by the simulator (inverse of [decode]). */
    fun encodeRpm(rpm: Double): ByteArray {
        val v = (rpm * 4).toInt()
        return byteArrayOf((v shr 8).toByte(), v.toByte())
    }

    fun encodeTempC(c: Double): ByteArray = byteArrayOf((c.toInt() + 40).toByte())

    fun encodeTrim(pct: Double): ByteArray = byteArrayOf(((pct + 100) * 128 / 100).toInt().toByte())

    fun encodeMaf(gps: Double): ByteArray {
        val v = (gps * 100).toInt()
        return byteArrayOf((v shr 8).toByte(), v.toByte())
    }
}
