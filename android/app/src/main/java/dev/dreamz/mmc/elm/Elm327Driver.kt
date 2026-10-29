package dev.dreamz.mmc.elm

import dev.dreamz.mmc.adapter.Transport

class ElmInitException(message: String) : IllegalStateException(message)

/** The ECU answered with a typed no-data condition (NO DATA, CAN ERROR, …). */
class NoObdDataException(message: String) : IllegalStateException(message)

/**
 * ELM327 driver (SPEC §6). Blocking single-flight calls; [Transport]
 * applies the per-command [timeoutMs]. Init uses ATSP0, never the
 * clone-locking alternate (see [Allowlist.NEVER_SEND_COMMANDS]).
 */
class Elm327Driver(
    private val transport: Transport,
    private val allowMode22: Boolean = false,
    private val mode22Allowlist: Set<String> = emptySet(),
) {
    companion object {
        const val TIMEOUT_ATZ_MS = 1500
        const val TIMEOUT_FIRST_0100_MS = 2000
        const val TIMEOUT_STEADY_MS = 300

        val INIT_SEQUENCE: List<String> = listOf(
            "ATZ", "ATE0", "ATL0", "ATS0", "ATH1", "ATAT1", "ATST32", "ATSP0",
        )

        /** Commands whose "?" reply is tolerated (clone tolerance). */
        val OPTIONAL_COMMANDS: Set<String> = setOf(
            "ATL0", "ATS0", "ATAT1", "ATST32", "AT@1", "STI", "ATPPS",
        )
        val POST_INIT_COMMANDS: List<String> = listOf("ATDPN", "ATI", "AT@1", "STI", "ATPPS", "ATRV")
    }

    private val lock = Any()

    /** Every command string actually handed to the transport, in order. */
    val commandsSent: MutableList<String> = mutableListOf()

    /** Timeout used for each sent command, parallel to [commandsSent]. */
    val timeoutsUsed: MutableList<Int> = mutableListOf()

    var profile: AdapterProfile? = null
        private set

    private fun send(cmd: String, timeoutMs: Int): String {
        if (cmd in Allowlist.NEVER_SEND_COMMANDS) {
            throw BlockedRequestException("driver must never send: $cmd")
        }
        synchronized(lock) {
            commandsSent.add(cmd)
            timeoutsUsed.add(timeoutMs)
            return transport.transact(cmd, timeoutMs)
        }
    }

    /** Run the SPEC §6 init sequence, then fingerprint. Returns the profile. */
    fun init(): AdapterProfile {
        for (cmd in INIT_SEQUENCE) {
            val timeout = if (cmd == "ATZ") TIMEOUT_ATZ_MS else TIMEOUT_STEADY_MS
            val reply = send(cmd, timeout)
            if (reply.contains("?") && cmd !in OPTIONAL_COMMANDS) {
                throw ElmInitException("required init command failed: $cmd")
            }
        }
        val first = send("0100", TIMEOUT_FIRST_0100_MS)
        if (first.contains("?")) throw ElmInitException("protocol search failed on 0100")
        for (cmd in POST_INIT_COMMANDS) {
            send(cmd, TIMEOUT_STEADY_MS) // replies feed fingerprint; "?" tolerated
        }
        val replies = mutableMapOf<String, String>()
        // Re-probe for the fingerprint matrix (cheap, idempotent AT reads).
        for (cmd in listOf("ATI", "ATPPS", "ATCRA 7E8", "ATS0", "ATAT1", "ATSH 7E0", "22F190", "ATDPN")) {
            replies[cmd] = send(cmd, TIMEOUT_STEADY_MS)
        }
        val p = Fingerprint.fingerprint { replies[it] ?: "?" }
        profile = p
        return p
    }

    /** CAN-id header width from the fingerprinted protocol (8 chars on 29-bit CAN). */
    private fun headerChars(): Int =
        if ((profile?.protocol ?: "").contains("29/")) 8 else 3

    /** Gated single request: "01".."0A" PIDs, "03" DTCs, "22" DID reads, "04" with token. */
    fun query(
        service: String,
        did: String? = null,
        token: ClearToken? = null,
        sessionId: String? = null,
        nowMs: Long = System.currentTimeMillis(),
    ): ElmResult {
        val p = profile ?: throw IllegalStateException("init() first")
        Allowlist.check(service, did, p, allowMode22, mode22Allowlist, token, sessionId, nowMs)
        val line = if (service == "22") "22$did" else service
        return Parsers.frame(send(line, TIMEOUT_STEADY_MS), headerChars = headerChars())
    }

    /**
     * Mode 06 monitor read. The reply arrives as numbered ISO-TP lines
     * ("0: ...", "1: ...") or PCI frames (spaced or space-less with ATS0),
     * which arrive one CAN frame per line, so this reassembles them here.
     * Still gated by [Allowlist].
     */
    fun queryMode06(): List<Parsers.Mode06Entry> {
        val p = profile ?: throw IllegalStateException("init() first")
        Allowlist.check("06", profile = p)
        val raw = send("06", TIMEOUT_STEADY_MS)
        val headerChars = headerChars()
        val lines = raw.substringBefore(">").replace("\r", "\n").lines()
            .map { it.trim() }
            .filter { it.isNotEmpty() && it != "SEARCHING..." }
            .map { Parsers.stripCanHeader(it, headerChars = headerChars) }
            .filter { it.isNotEmpty() }
        if (lines.isEmpty()) throw NoObdDataException("empty mode 06 reply")
        if (lines.any { it.matches(Regex("[0-9A-Fa-f]:.*")) } || isFirstFrame(lines)) {
            val payload = Parsers.reassembleIsoTp(lines)
            require(payload.isNotEmpty() && payload[0] == 0x46.toByte()) { "not a mode 06 payload" }
            return Parsers.parseMode06(payload.copyOfRange(1, payload.size))
        }
        // Single-frame reply: reuse the typed-error mapping.
        when (val framed = Parsers.frame(raw, headerChars = headerChars)) {
            is ElmResult.TypedError -> throw NoObdDataException(framed.kind.name)
            is ElmResult.Data -> {
                val bytes = framed.ecus.firstOrNull()?.bytes
                    ?: throw NoObdDataException("empty mode 06 reply")
                require(bytes.isNotEmpty() && bytes[0] == 0x46.toByte()) { "not a mode 06 payload" }
                return Parsers.parseMode06(bytes.copyOfRange(1, bytes.size))
            }
        }
    }

    /**
     * True when [stripped] lines form a multi-frame ISO-TP reply in raw PCI
     * framing: more than one line and the first opens with a 0x1N
     * first-frame byte (single-frame PCI length bytes are never ≥ 0x10 for
     * these short replies, and multi-ECU single frames never start with one).
     */
    private fun isFirstFrame(stripped: List<String>): Boolean {
        if (stripped.size <= 1) return false
        val first = try {
            Parsers.hexBytes(stripped[0])
        } catch (_: Exception) {
            return false
        }
        return first.isNotEmpty() && first[0].toInt() and 0xF0 == 0x10
    }

    /**
     * Mode 09 PID 02 VIN read (best effort: some vehicles answer NO DATA).
     * The reply arrives as numbered ISO-TP lines ("0: ...", "1: ...") or a
     * single frame, like Mode 06. Gated by [Allowlist] (09 is read-only);
     * the allowlist itself is unchanged. Throws [NoObdDataException] when
     * the vehicle has no VIN to give.
     */
    fun queryVin(): String {
        val p = profile ?: throw IllegalStateException("init() first")
        Allowlist.check("09", profile = p)
        val raw = send("0902", TIMEOUT_STEADY_MS)
        val headerChars = headerChars()
        val lines = raw.substringBefore(">").replace("\r", "\n").lines()
            .map { it.trim() }
            .filter { it.isNotEmpty() && it != "SEARCHING..." }
            .map { Parsers.stripCanHeader(it, headerChars = headerChars) }
            .filter { it.isNotEmpty() }
        if (lines.isEmpty()) throw NoObdDataException("empty mode 09 reply")
        if (lines.any { it.matches(Regex("[0-9A-Fa-f]:.*")) } || isFirstFrame(lines)) {
            val payload = Parsers.reassembleIsoTp(lines)
            return vinFrom(payload)
        }
        when (val framed = Parsers.frame(raw, headerChars = headerChars)) {
            is ElmResult.TypedError -> throw NoObdDataException(framed.kind.name)
            is ElmResult.Data -> {
                val bytes = framed.ecus.firstOrNull()?.bytes
                    ?: throw NoObdDataException("empty mode 09 reply")
                return vinFrom(bytes)
            }
        }
    }

    private fun vinFrom(payload: ByteArray): String {
        if (payload.size >= 3 && payload[0] == 0x49.toByte() && payload[1] == 0x02.toByte()) {
            return Parsers.parseVin(payload)
        }
        // Mode 09 type-2 extended form some adapters return bare.
        throw NoObdDataException("not a VIN payload")
    }

    /** Multi-PID Mode 01 (up to 6 PIDs) only when the profile allows it. */
    fun queryMultiPid(pids: List<Int>): ElmResult {
        val p = profile ?: throw IllegalStateException("init() first")
        require(pids.size in 2..6) { "multi-PID needs 2..6 PIDs" }
        if (!p.supportsMultiPid) throw BlockedRequestException("adapter profile forbids multi-PID")
        Allowlist.check("01", profile = p)
        val line = "01" + pids.joinToString("") { "%02X".format(it) }
        return Parsers.frame(send(line, TIMEOUT_STEADY_MS), headerChars = headerChars())
    }
}
