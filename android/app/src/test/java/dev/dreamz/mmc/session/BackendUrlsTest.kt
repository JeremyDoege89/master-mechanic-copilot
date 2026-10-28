package dev.dreamz.mmc.session

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class BackendUrlsTest {
    @Test
    fun savedUrlWinsOverBuildDefault() {
        assertEquals(
            "http://100.99.0.7:8000",
            BackendUrls.resolve("http://100.99.0.7:8000", "http://100.64.0.1:8000"),
        )
    }

    @Test
    fun blankSavedFallsBackToBuildDefault() {
        assertEquals("http://100.64.0.1:8000", BackendUrls.resolve(null, "http://100.64.0.1:8000"))
        assertEquals("http://100.64.0.1:8000", BackendUrls.resolve("  ", "http://100.64.0.1:8000"))
    }

    @Test
    fun simStaysSelectableAsDefault() {
        assertTrue(TransportKind.SIM in TransportOptions.all)
        assertEquals(TransportKind.SIM, TransportOptions.default)
    }
}
