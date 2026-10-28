package dev.dreamz.mmc.elm

/**
 * Single-use Mode 04 (clear codes) authorization.
 * Issued by the clear-codes flow (backend request/confirm lands in M4/M6);
 * the driver consumes it on the first Mode 04 request and rejects reuse.
 */
class ClearToken {
    private var consumed = false

    @Synchronized
    fun consume() {
        if (consumed) throw BlockedRequestException("ClearToken already used")
        consumed = true
    }

    val isConsumed: Boolean get() = consumed
}
