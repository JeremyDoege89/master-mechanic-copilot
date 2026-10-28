package dev.dreamz.mmc.adapter

/**
 * One ELM327 line transaction. Blocking; implementations apply [timeoutMs].
 * (SPEC says `send(line) → Flow<bytes>`; the blocking form keeps the driver
 * free of a coroutines dependency and unit-testable on the JVM.)
 */
interface Transport {
    fun transact(line: String, timeoutMs: Int = 300): String
}

/** Injectable socket behind the Classic/BLE/Wi-Fi transports (fakes in tests). */
interface ByteSocket {
    fun exchange(request: String): String
    fun close()
}
