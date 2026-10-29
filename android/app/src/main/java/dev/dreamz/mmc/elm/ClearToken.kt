package dev.dreamz.mmc.elm

/**
 * Single-use Mode 04 (clear codes) authorization, bound to the backend token.
 *
 * The backend mints a session-bound, 5-minute, single-use token
 * (`POST /sessions/{id}/clear-codes/request`, confirmed with typed
 * `CLEAR CODES` + engine-off attestation); the confirmed token string is
 * the only value that authorizes one driver-side Mode 04. A token minted
 * locally in-process — blank, wrong session, or expired — is rejected by
 * [Allowlist], so Mode 04 is unreachable without the gated backend flow.
 */
class ClearToken(
    val token: String,
    val sessionId: String,
    val expiresAtMs: Long,
) {
    private var consumed = false

    fun isExpired(nowMs: Long = System.currentTimeMillis()): Boolean = nowMs > expiresAtMs

    @Synchronized
    fun consume() {
        if (consumed) throw BlockedRequestException("ClearToken already used")
        consumed = true
    }

    val isConsumed: Boolean get() = consumed
}
