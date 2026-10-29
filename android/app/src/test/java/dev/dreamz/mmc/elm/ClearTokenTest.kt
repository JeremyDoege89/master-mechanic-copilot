package dev.dreamz.mmc.elm

import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** M6 finding #4: only a backend-issued, session-bound, unexpired token clears codes. */
class ClearTokenTest {
    private val genuine = AdapterProfile(
        kind = "genuine", supportsMultiPid = true, supportsHeaders = true,
        supportsMode22 = true, supportsAtpps = true, supportsAtcra = true,
    )
    private val now = 1_700_000_000_000L

    private fun blocks(block: () -> Unit): String {
        try {
            block()
        } catch (e: BlockedRequestException) {
            return e.message ?: ""
        }
        fail("expected BlockedRequestException")
        return ""
    }

    @Test
    fun locallyMintedBlankTokenIsRejected() {
        val local = ClearToken("", "sess1", now + 300_000)
        val msg = blocks {
            Allowlist.check("04", profile = genuine, token = local, sessionId = "sess1", nowMs = now)
        }
        assertTrue(msg.contains("backend-issued"))
    }

    @Test
    fun tokenBoundToAnotherSessionIsRejected() {
        val other = ClearToken("backend-token", "sess-other", now + 300_000)
        val msg = blocks {
            Allowlist.check("04", profile = genuine, token = other, sessionId = "sess1", nowMs = now)
        }
        assertTrue(msg.contains("another session"))
    }

    @Test
    fun tokenWithoutSessionContextIsRejected() {
        val token = ClearToken("backend-token", "sess1", now + 300_000)
        blocks {
            Allowlist.check("04", profile = genuine, token = token, sessionId = null, nowMs = now)
        }
    }

    @Test
    fun expiredTokenIsRejected() {
        val stale = ClearToken("backend-token", "sess1", now - 1)
        val msg = blocks {
            Allowlist.check("04", profile = genuine, token = stale, sessionId = "sess1", nowMs = now)
        }
        assertTrue(msg.contains("expired"))
    }

    @Test
    fun validBackendTokenPassesOnce() {
        val token = ClearToken("backend-token", "sess1", now + 300_000)
        try {
            Allowlist.check("04", profile = genuine, token = token, sessionId = "sess1", nowMs = now)
        } catch (e: BlockedRequestException) {
            fail("expected allow, got block: ${e.message}")
        }
        assertTrue(token.isConsumed)
        blocks {
            Allowlist.check("04", profile = genuine, token = token, sessionId = "sess1", nowMs = now)
        }
    }
}
