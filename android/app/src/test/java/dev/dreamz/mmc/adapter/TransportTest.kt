package dev.dreamz.mmc.adapter

import dev.dreamz.mmc.elm.Elm327Sim
import dev.dreamz.mmc.elm.Personality
import dev.dreamz.mmc.elm.Scenario
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class TransportTest {
    private class FakeSocket(val replies: Map<String, String> = emptyMap()) : ByteSocket {
        val received = mutableListOf<String>()
        var closed = false
        override fun exchange(request: String): String {
            received.add(request)
            return replies[request] ?: "OK\r\r>"
        }

        override fun close() {
            closed = true
        }
    }

    @Test
    fun allFiveImplementTheInterface() {
        val transports: List<Transport> = listOf(
            SimTransport(),
            ReplayTransport(listOf(ReplayTransport.ReplayEvent(0, "ATZ", "ELM327 v1.5\r\r>"))),
            ClassicRfcommTransport { FakeSocket() },
            BleTransport({ FakeSocket() }),
            WifiTcpTransport({ _, _ -> FakeSocket() }),
        )
        assertEquals(5, transports.size)
    }

    @Test
    fun simTransportDelegatesToSim() {
        val t = SimTransport(Elm327Sim(Personality.GENUINE, Scenario.HEALTHY))
        assertTrue(t.transact("ATZ", 300).contains("ELM327"))
    }

    @Test
    fun replayTimingMatchesTranscriptDeltas() {
        val events = listOf(
            ReplayTransport.ReplayEvent(0, "ATZ", "ELM327 v1.5\r\r>"),
            ReplayTransport.ReplayEvent(2000, "010C", "7E8 04 41 0C 1A F8\r\r>"),
        )
        val replay = ReplayTransport(events, timeScale = 0.05) // 2000 ms -> ~100 ms
        assertEquals("ELM327 v1.5\r\r>", replay.transact("ATZ", 300))
        val start = System.nanoTime()
        assertEquals("7E8 04 41 0C 1A F8\r\r>", replay.transact("010C", 300))
        val elapsedMs = (System.nanoTime() - start) / 1_000_000.0
        assertTrue("replay gap $elapsedMs ms", elapsedMs in 80.0..120.0)
        try {
            replay.transact("010C", 300)
            fail("expected exhaustion")
        } catch (e: IllegalStateException) {
            // ok
        }
    }

    @Test
    fun replayDivergenceThrows() {
        val replay = ReplayTransport(listOf(ReplayTransport.ReplayEvent(0, "ATZ", "ELM327")))
        try {
            replay.transact("010C", 300)
            fail("expected divergence")
        } catch (e: IllegalStateException) {
            assertTrue(e.message!!.contains("diverged"))
        }
    }

    @Test
    fun classicConnectsViaFakeWithSppUuid() {
        assertEquals("00001101-0000-1000-8000-00805F9B34FB", ClassicRfcommTransport.SPP_UUID)
        val fake = FakeSocket(mapOf("ATZ" to "ELM327 v1.5\r\r>"))
        val t = ClassicRfcommTransport { fake }
        t.connect()
        assertEquals("ELM327 v1.5\r\r>", t.transact("ATZ", 300))
        assertEquals(listOf("ATZ"), fake.received)
        t.close()
        assertTrue(fake.closed)
    }

    @Test
    fun bleConnectsWithBothServiceUuids() {
        val seen = mutableListOf<String>()
        for (uuid in listOf(BleTransport.PRIMARY_SERVICE, BleTransport.ALT_SERVICE)) {
            val t = BleTransport({ svc -> seen.add(svc); FakeSocket() }, uuid)
            t.connect()
            t.transact("ATI", 300)
            t.close()
        }
        assertEquals(listOf("FFE0", "FFF0"), seen)
        assertEquals(BleTransport.PRIMARY_CHAR, "FFE1")
        assertEquals(BleTransport.ALT_CHAR, "FFF1")
    }

    @Test
    fun wifiConnectsToDefaultEndpoint() {
        assertEquals("192.168.0.10", WifiTcpTransport.DEFAULT_HOST)
        assertEquals(35000, WifiTcpTransport.DEFAULT_PORT)
        var seenHost = ""
        var seenPort = 0
        val t = WifiTcpTransport({ h, p -> seenHost = h; seenPort = p; FakeSocket() })
        t.connect()
        t.transact("ATRV", 300)
        assertEquals("192.168.0.10", seenHost)
        assertEquals(35000, seenPort)
    }
}
