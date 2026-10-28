package dev.dreamz.mmc.ui

import androidx.lifecycle.SavedStateHandle
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Restore logic: snapshot round-trips through the SavedStateHandle. */
class MmcViewModelTest {
    private fun liveSnapshot() = MmcSessionSnapshot(
        screen = "LIVE",
        sessionId = "sess123",
        baseUrl = "http://100.64.0.1:8000",
        urlField = "http://100.64.0.1:8000",
        userLevel = "L2",
        tools = listOf("multimeter"),
        transportKind = "SIM",
        simScenario = "VACUUM_LEAK",
        status = "Live: 3 observations streamed.",
        profileText = "genuine proto=ISO 15765-4 CAN 11/500 multi=true",
        pidText = "rpm=750 ect=93C maf=3.54g/s",
        stateJson = """{"session_id":"sess123","tests_log":[]}""",
        reportJson = null,
        blobRef = "",
        activeTestRef = "dtc_read",
        manualOutcome = "",
        showRawJson = false,
    )

    @Test
    fun emptyHandleRestoresNull() {
        val vm = MmcViewModel(SavedStateHandle())
        assertNull(vm.savedSnapshot())
        assertFalse(vm.hasSavedSession())
    }

    @Test
    fun saveRestoreRoundTrip() {
        val handle = SavedStateHandle()
        MmcViewModel(handle).persistSnapshot(liveSnapshot())
        assertEquals(liveSnapshot(), MmcViewModel(handle).savedSnapshot())
    }

    @Test
    fun savedSessionIdMarksResumable() {
        val withSession = MmcViewModel(SavedStateHandle())
        withSession.persistSnapshot(liveSnapshot())
        assertTrue(withSession.hasSavedSession())

        val fresh = MmcViewModel(SavedStateHandle())
        fresh.persistSnapshot(liveSnapshot().copy(sessionId = null, screen = "SETTINGS"))
        assertFalse(fresh.hasSavedSession())
    }

    @Test
    fun stateJsonAndScenarioSurvive() {
        val handle = SavedStateHandle()
        MmcViewModel(handle).persistSnapshot(liveSnapshot())
        val restored = MmcViewModel(handle).savedSnapshot()!!
        assertEquals("""{"session_id":"sess123","tests_log":[]}""", restored.stateJson)
        assertEquals("VACUUM_LEAK", restored.simScenario)
        assertEquals(listOf("multimeter"), restored.tools)
        assertEquals("dtc_read", restored.activeTestRef)
    }
}
