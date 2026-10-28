package dev.dreamz.mmc.elm

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ElmAllowlistTest {
    private val genuine = AdapterProfile(
        kind = "genuine", supportsMultiPid = true, supportsHeaders = true,
        supportsMode22 = true, supportsAtpps = true, supportsAtcra = true,
    )
    private val limited = AdapterProfile(
        kind = "limited_clone", supportsMultiPid = false, supportsHeaders = false,
        supportsMode22 = false, supportsAtpps = false, supportsAtcra = false,
    )
    private val allow22 = setOf("F190", "F191", "F192")

    private fun allows(block: () -> Unit) {
        try {
            block()
        } catch (e: BlockedRequestException) {
            fail("expected allow, got block: ${e.message}")
        }
    }

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
    fun allowedServicesPass() {
        for (svc in listOf("01", "02", "03", "05", "06", "07", "09", "0A")) {
            allows { Allowlist.check(svc, profile = genuine) }
        }
        allows { Allowlist.check("ATE0", profile = genuine) } // benign AT passes
    }

    @Test
    fun everyBlockedFirstByteThrows() {
        val blocked = listOf(
            "10", "11", "14", "27", "28", "2E", "2F", "31",
            "34", "35", "36", "37", "3D", "85",
        )
        assertEquals(14, blocked.size)
        for (svc in blocked) {
            blocks { Allowlist.check(svc, profile = genuine) }
        }
        blocks { Allowlist.check("ATMA", profile = genuine) }
    }

    @Test
    fun unknownServiceThrows() {
        blocks { Allowlist.check("0F", profile = genuine) }
    }

    @Test
    fun mode04NeedsSingleUseToken() {
        blocks { Allowlist.check("04", profile = genuine) } // no token
        val token = ClearToken()
        allows { Allowlist.check("04", profile = genuine, token = token) }
        assertTrue(token.isConsumed)
        blocks { Allowlist.check("04", profile = genuine, token = token) } // reuse throws
    }

    @Test
    fun mode22ConjunctionGate() {
        // positive: flag AND profile AND yaml DID
        allows { Allowlist.check("22", did = "F190", profile = genuine, allowMode22 = true, mode22Allowlist = allow22) }
        // negative 1: env flag off
        blocks { Allowlist.check("22", did = "F190", profile = genuine, allowMode22 = false, mode22Allowlist = allow22) }
        // negative 2: profile without mode22
        blocks { Allowlist.check("22", did = "F190", profile = limited, allowMode22 = true, mode22Allowlist = allow22) }
        // negative 3: DID not in yaml
        blocks { Allowlist.check("22", did = "F999", profile = genuine, allowMode22 = true, mode22Allowlist = allow22) }
        blocks { Allowlist.check("22", did = null, profile = genuine, allowMode22 = true, mode22Allowlist = allow22) }
    }

    @Test
    fun starterYamlParses() {
        val text = java.io.File("../../config/mode22_allowlist.yaml").readText()
        val dids = Mode22Allowlist.parseYaml(text)
        assertTrue(dids.contains("F190"))
        allows { Allowlist.check("22", did = "F190", profile = genuine, allowMode22 = true, mode22Allowlist = dids) }
    }
}
