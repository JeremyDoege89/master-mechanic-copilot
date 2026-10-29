package dev.dreamz.mmc.adapter

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** On-vehicle connection logic that needs no radio (BtSetup pure helpers). */
class LiveConnectTest {
    @Test
    fun runtimePermissionOnlyAboveApi30() {
        assertFalse(BtSetup.needsRuntimeBtConnect(29))
        assertFalse(BtSetup.needsRuntimeBtConnect(30))
        assertTrue(BtSetup.needsRuntimeBtConnect(31))
        assertTrue(BtSetup.needsRuntimeBtConnect(33))
        assertTrue(BtSetup.needsRuntimeBtConnect(34))
    }

    @Test
    fun sppUuidMatchesClassicTransport() {
        assertEquals(ClassicRfcommTransport.SPP_UUID, BtSetup.SPP_UUID)
        assertEquals("00001101-0000-1000-8000-00805F9B34FB", BtSetup.SPP_UUID)
    }

    @Test
    fun fallbackOrderIsSecureThenInsecureThenChannel1() {
        assertEquals(
            listOf(BtSetup.BtAttempt.SECURE, BtSetup.BtAttempt.INSECURE, BtSetup.BtAttempt.REFLECT_CHANNEL_1),
            BtSetup.ATTEMPT_ORDER,
        )
    }

    @Test
    fun firstSuccessWinsAndLaterAttemptsNeverRun() {
        val ran = mutableListOf<String>()
        val result = BtSetup.connectFirstOf(
            listOf(
                BtSetup.BtAttempt.SECURE to { ran.add("secure"); throw java.io.IOException("refused") },
                BtSetup.BtAttempt.INSECURE to { ran.add("insecure"); "connected" },
                BtSetup.BtAttempt.REFLECT_CHANNEL_1 to { ran.add("ch1"); "shadow" },
            ),
        )
        assertEquals("connected", result)
        assertEquals(listOf("secure", "insecure"), ran)
    }

    @Test
    fun allFailuresThrowWithAttemptTrail() {
        try {
            BtSetup.connectFirstOf(
                listOf(
                    BtSetup.BtAttempt.SECURE to { throw java.io.IOException("refused") },
                    BtSetup.BtAttempt.INSECURE to { throw java.io.IOException("timeout") },
                    BtSetup.BtAttempt.REFLECT_CHANNEL_1 to { throw java.io.IOException("busy") },
                ),
            )
            fail("expected connect failure")
        } catch (e: java.io.IOException) {
            assertTrue(e.message!!.contains("SECURE"))
            assertTrue(e.message!!.contains("INSECURE"))
            assertTrue(e.message!!.contains("REFLECT_CHANNEL_1"))
        }
    }

    @Test
    fun emptyAttemptsRejected() {
        try {
            BtSetup.connectFirstOf<String>(emptyList())
            fail("expected rejection")
        } catch (e: IllegalArgumentException) {
            // ok
        }
    }

    @Test
    fun friendlyErrorsAreActionable() {
        assertTrue(
            BtSetup.friendlyConnectError(SecurityException("permission")).contains("permission"),
        )
        val refused = BtSetup.friendlyConnectError(java.io.IOException("Connection refused"))
        assertTrue(refused.contains("not paired"))
        assertTrue(refused.contains("in use"))
        val timeout = BtSetup.friendlyConnectError(java.io.IOException("connect timed out after 15000ms"))
        assertTrue(timeout.contains("out of range"))
        // No MACs or hosts leak into the message.
        assertFalse(refused.contains("AA:BB"))
    }

    @Test
    fun bondedConnectNeedsOnlyConnectPermission() {
        // Field case (moto g, API 34): CONNECT granted, SCAN denied ->
        // the bonded connect may proceed, but discovery must stay untouched.
        assertTrue(BtSetup.mayConnectClassic(34, connectGranted = true))
        assertFalse(BtSetup.mayConnectClassic(34, connectGranted = false))
        assertFalse(BtSetup.mayTouchDiscovery(34, scanGranted = false))
        assertTrue(BtSetup.mayTouchDiscovery(34, scanGranted = true))
        // Legacy APIs need no runtime grant for either.
        assertTrue(BtSetup.mayConnectClassic(30, connectGranted = false))
        assertTrue(BtSetup.mayTouchDiscovery(30, scanGranted = false))
    }

    @Test
    fun failureReportNamesStepAndRealCause() {
        val denied = BtSetup.describeFailure(
            BtSetup.FailStep.PERMISSION, SecurityException("Need BLUETOOTH_SCAN permission"),
        )
        assertTrue(denied.contains("permission"))
        assertTrue(denied.contains("SecurityException"))
        assertTrue(denied.contains("Need BLUETOOTH_SCAN permission"))

        val attempt = BtSetup.describeFailure(
            BtSetup.FailStep.CONNECT_ATTEMPT,
            java.io.IOException("Connection refused"),
            BtSetup.BtAttempt.SECURE,
        )
        assertTrue(attempt.contains("SECURE"))
        assertTrue(attempt.contains("IOException"))
        assertTrue(attempt.contains("Connection refused"))

        val atz = BtSetup.describeFailure(
            BtSetup.FailStep.ATZ, java.io.IOException("connect timed out after 15000ms"),
        )
        assertTrue(atz.contains("ATZ"))
        assertTrue(atz.contains("IOException"))
    }

    @Test
    fun attemptTrailNamesIndexAndExceptionClass() {
        try {
            BtSetup.connectFirstOf(
                listOf(
                    BtSetup.BtAttempt.SECURE to { throw java.io.IOException("refused") },
                    BtSetup.BtAttempt.INSECURE to { throw java.io.IOException("timeout") },
                    BtSetup.BtAttempt.REFLECT_CHANNEL_1 to { throw java.io.IOException("busy") },
                ),
            )
            fail("expected connect failure")
        } catch (e: java.io.IOException) {
            assertTrue(e.message!!.contains("1/3"))
            assertTrue(e.message!!.contains("IOException"))
        }
    }

    @Test
    fun explanationAndBleTextAreHonest() {
        assertTrue(BtSetup.BT_EXPLANATION.contains("paired"))
        assertTrue(BtSetup.BT_DENIED.contains("Settings"))
        assertTrue(BtSetup.BLE_COMING_SOON.contains("coming soon"))
        assertFalse(BtSetup.BLE_COMING_SOON.lowercase().contains("error"))
    }
}
