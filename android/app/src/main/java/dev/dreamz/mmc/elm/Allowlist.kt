package dev.dreamz.mmc.elm

/** Read-only safety allowlist (AGENTS.md hard constraint 1, SPEC §6). */
object Allowlist {
    /** Allowed OBD service first-bytes (hex, uppercase, no spaces). */
    val ALLOW_FIRST_BYTES: Set<String> = setOf("01", "02", "03", "05", "06", "07", "09", "0A")

    /** Always-blocked UDS/service first-bytes. */
    val BLOCKED_FIRST_BYTES: Set<String> = setOf(
        "10", "11", "14", "27", "28", "2E", "2F", "31",
        "34", "35", "36", "37", "3D", "85"
    )

    /**
     * Adapter commands the driver must never transmit. Kept as data (not a
     * source-text grep target) so the "never send" rule is enforced on the
     * actual command strings the driver emits — see Elm327DriverTest.
     * "AT TP A0" locks up some clones; the driver uses "ATSP0" instead.
     */
    val NEVER_SEND_COMMANDS: Set<String> = setOf("AT TP A0")

    /**
     * Gate an outbound request.
     *
     * @param service normalized request head: "01".."0A", "22", "04", or an
     *   "AT..." / raw adapter command such as "ATMA".
     * @param did hex DID for mode 0x22 requests (e.g. "F190"), else null.
     * @param profile fingerprinted adapter profile.
     * @param allowMode22 env/config flag (ALLOW_MODE22).
     * @param mode22Allowlist DIDs from config/mode22_allowlist.yaml.
     * @param token single-use token for mode 0x04; consumed on success.
     *   Must carry the backend-issued token string for [sessionId] and be
     *   unexpired — a locally-minted token is rejected (M6 finding #4).
     * @param sessionId backend session the Mode 04 token must be bound to.
     * @param nowMs clock for the token expiry check (injectable for tests).
     * @throws BlockedRequestException when the request is not allowed.
     */
    fun check(
        service: String,
        did: String? = null,
        profile: AdapterProfile,
        allowMode22: Boolean = false,
        mode22Allowlist: Set<String> = emptySet(),
        token: ClearToken? = null,
        sessionId: String? = null,
        nowMs: Long = System.currentTimeMillis(),
    ) {
        val head = service.trim().uppercase()
        if (head in NEVER_SEND_COMMANDS) throw BlockedRequestException("never-send command: $service")
        if (head == "ATMA") throw BlockedRequestException("raw ATMA monitor is blocked in consumer mode")
        if (head.startsWith("AT") || head == "STI") return // benign adapter commands pass

        val firstByte = head.take(2)
        if (firstByte in BLOCKED_FIRST_BYTES) throw BlockedRequestException("blocked service: $service")
        if (head == "04") {
            if (token == null) throw BlockedRequestException("mode 04 requires a ClearToken")
            if (token.token.isBlank()) throw BlockedRequestException("mode 04 token is not backend-issued")
            if (sessionId == null || token.sessionId != sessionId) {
                throw BlockedRequestException("mode 04 token bound to another session")
            }
            if (token.isExpired(nowMs)) throw BlockedRequestException("mode 04 token expired")
            token.consume()
            return
        }
        if (head == "22") {
            val normalizedDid = did?.trim()?.uppercase()
            if (!allowMode22) throw BlockedRequestException("mode 22 disabled by ALLOW_MODE22=false")
            if (!profile.mode22) throw BlockedRequestException("mode 22 not supported by adapter profile")
            if (normalizedDid == null || normalizedDid !in mode22Allowlist) {
                throw BlockedRequestException("DID $did not in mode22 allowlist")
            }
            return
        }
        if (firstByte !in ALLOW_FIRST_BYTES) throw BlockedRequestException("service not in allowlist: $service")
    }
}

class BlockedRequestException(message: String) : IllegalArgumentException(message)
