package dev.dreamz.mmc.elm

import dev.dreamz.mmc.adapter.Transport

class ElmInitException(message: String) : IllegalStateException(message)

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

    /** Gated single request: "01".."0A" PIDs, "03" DTCs, "22" DID reads, "04" with token. */
    fun query(service: String, did: String? = null, token: ClearToken? = null): ElmResult {
        val p = profile ?: throw IllegalStateException("init() first")
        Allowlist.check(service, did, p, allowMode22, mode22Allowlist, token)
        val line = if (service == "22") "22$did" else service
        return Parsers.frame(send(line, TIMEOUT_STEADY_MS))
    }

    /** Multi-PID Mode 01 (up to 6 PIDs) only when the profile allows it. */
    fun queryMultiPid(pids: List<Int>): ElmResult {
        val p = profile ?: throw IllegalStateException("init() first")
        require(pids.size in 2..6) { "multi-PID needs 2..6 PIDs" }
        if (!p.supportsMultiPid) throw BlockedRequestException("adapter profile forbids multi-PID")
        Allowlist.check("01", profile = p)
        val line = "01" + pids.joinToString("") { "%02X".format(it) }
        return Parsers.frame(send(line, TIMEOUT_STEADY_MS))
    }
}
