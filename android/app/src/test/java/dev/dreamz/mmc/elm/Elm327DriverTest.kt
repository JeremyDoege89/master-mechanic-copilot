package dev.dreamz.mmc.elm

import dev.dreamz.mmc.adapter.Transport
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class Elm327DriverTest {
    private class ScriptTransport(val replies: Map<String, String>) : Transport {
        val seenTimeouts = mutableMapOf<String, Int>()
        override fun transact(line: String, timeoutMs: Int): String {
            seenTimeouts[line] = timeoutMs
            return replies[line] ?: "?"
        }
    }

    private fun genuineReplies(): Map<String, String> = mapOf(
        "ATZ" to "ELM327 v1.5",
        "ATE0" to "OK",
        "ATL0" to "OK",
        "ATS0" to "OK",
        "ATH1" to "OK",
        "ATAT1" to "OK",
        "ATST32" to "OK",
        "ATSP0" to "OK",
        "0100" to "SEARCHING...\n7E8 06 41 00 BE 3E B8 11",
        "ATDPN" to "A6",
        "ATI" to "ELM327 v1.5",
        "AT@1" to "?",
        "STI" to "?",
        "ATPPS" to "OK",
        "ATRV" to "14.2V",
        "ATCRA 7E8" to "OK",
        "ATSH 7E0" to "OK",
        "22F190" to "62F19001465A53444",
        "010C" to "7E8 04 41 0C 1A F8",
        "010C0D" to "7E8 06 41 0C 1A F8 0D 28",
    )

    @Test
    fun initSendsSpecSequenceWithSpecTimeouts() {
        val t = ScriptTransport(genuineReplies())
        val driver = Elm327Driver(t)
        val profile = driver.init()
        assertEquals("genuine", profile.kind)
        val sent = driver.commandsSent
        // SPEC §6 order through ATSP0.
        val head = sent.take(8)
        assertEquals(listOf("ATZ", "ATE0", "ATL0", "ATS0", "ATH1", "ATAT1", "ATST32", "ATSP0"), head)
        assertEquals(Elm327Driver.TIMEOUT_ATZ_MS, t.seenTimeouts["ATZ"])
        assertEquals(Elm327Driver.TIMEOUT_FIRST_0100_MS, t.seenTimeouts["0100"])
        assertEquals(Elm327Driver.TIMEOUT_STEADY_MS, t.seenTimeouts["010C"] ?: t.seenTimeouts["ATSP0"])
    }

    @Test
    fun driverNeverSendsBlockedCommandStrings() {
        // Correction 3: scan the commands the driver actually sends, not source text.
        // (The blocked literal legitimately appears in Allowlist.NEVER_SEND_COMMANDS.)
        val t = ScriptTransport(genuineReplies())
        val driver = Elm327Driver(t)
        driver.init()
        driver.query("010C")
        driver.queryMultiPid(listOf(0x0C, 0x0D, 0x05))
        for (sent in driver.commandsSent) {
            for (blocked in Allowlist.NEVER_SEND_COMMANDS) {
                assertTrue("driver sent blocked <$blocked> as <$sent>", sent != blocked)
            }
        }
        assertTrue(driver.commandsSent.contains("ATSP0"))
    }

    @Test
    fun optionalUnknownRepliesAreTolerated() {
        val replies = genuineReplies() + mapOf("ATAT1" to "?", "ATST32" to "?", "AT@1" to "?")
        val driver = Elm327Driver(ScriptTransport(replies))
        driver.init() // must not throw
    }

    @Test
    fun requiredInitFailureThrows() {
        val replies = genuineReplies() + mapOf("ATH1" to "?")
        try {
            Elm327Driver(ScriptTransport(replies)).init()
            fail("expected ElmInitException")
        } catch (e: ElmInitException) {
            assertTrue(e.message!!.contains("ATH1"))
        }
    }

    @Test
    fun multiPidGatedByProfileAndCount() {
        val driver = Elm327Driver(ScriptTransport(genuineReplies()))
        driver.init()
        val r = driver.queryMultiPid(listOf(0x0C, 0x0D))
        assertTrue(r is ElmResult.Data)
        try {
            driver.queryMultiPid(List(7) { 0x0C })
            fail("expected count rejection")
        } catch (e: IllegalArgumentException) {
            // ok
        }
    }

    @Test
    fun queryBeforeInitThrows() {
        try {
            Elm327Driver(ScriptTransport(genuineReplies())).query("010C")
            fail("expected IllegalStateException")
        } catch (e: IllegalStateException) {
            // ok
        }
    }
}
