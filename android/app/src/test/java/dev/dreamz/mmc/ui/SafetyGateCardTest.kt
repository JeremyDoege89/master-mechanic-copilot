package dev.dreamz.mmc.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Pure-function tests for the M6 §2 safety-gate card logic. */
class SafetyGateCardTest {

    private val checklist = SafetyGates.parse(
        gate = "engine_running_outdoors",
        title = "Engine running — outdoors only",
        items = listOf("Outdoors only", "Brake set"),
        l1Extra = listOf("Keep kids away"),
    )!!

    @Test
    fun parseRejectsNoneAndEmpty() {
        assertNull(SafetyGates.parse("none", "T", listOf("x"), emptyList()))
        assertNull(SafetyGates.parse("", "T", listOf("x"), emptyList()))
        assertNull(SafetyGates.parse("engine_off", "", listOf("x"), emptyList()))
        assertNull(SafetyGates.parse("engine_off", "T", emptyList(), emptyList()))
    }

    @Test
    fun l1SeesExtraItems() {
        assertEquals(
            listOf("Outdoors only", "Brake set", "Keep kids away"),
            SafetyGates.displayItems(checklist, "L1"),
        )
        assertEquals(
            listOf("Outdoors only", "Brake set"),
            SafetyGates.displayItems(checklist, "L2"),
        )
        assertEquals(
            listOf("Outdoors only", "Brake set"),
            SafetyGates.displayItems(checklist, "L3"),
        )
    }

    @Test
    fun runUnlocksOnlyWhenEveryBoxTicked() {
        val required = SafetyGates.displayItems(checklist, "L1")
        assertFalse(SafetyGates.allChecked(required, emptySet()))
        assertFalse(SafetyGates.allChecked(required, setOf("Outdoors only")))
        assertFalse(
            SafetyGates.allChecked(required, setOf("Outdoors only", "Brake set")),
        )
        assertTrue(
            SafetyGates.allChecked(
                required,
                setOf("Outdoors only", "Brake set", "Keep kids away"),
            ),
        )
    }

    @Test
    fun toolOptionsMatchSpecQuestionnaire() {
        assertEquals(8, SafetyGates.TOOL_OPTIONS.size)
        assertTrue(SafetyGates.TOOL_OPTIONS.contains("multimeter"))
        assertTrue(SafetyGates.TOOL_OPTIONS.contains("smoke machine"))
        assertTrue(SafetyGates.TOOL_OPTIONS.contains("fuel pressure gauge"))
    }

    @Test
    fun levelLabelsDifferPerLevel() {
        assertEquals(3, SafetyGates.LEVEL_LABELS.size)
        assertTrue(SafetyGates.levelLabel("L1") != SafetyGates.levelLabel("L3"))
    }

    @Test
    fun presentChecklistTakesChecklistPath() {
        assertEquals(SafetyGates.AckPath.CHECKLIST, SafetyGates.ackPath(true))
    }

    @Test
    fun missingChecklistTakesExplicitOfflinePath() {
        // M6 finding #8: an empty/missing checklist must route to the
        // explicit offline ack, never to a gateReady-gated confirm (which
        // deadlocks because gateReady is false on empty items).
        assertEquals(SafetyGates.AckPath.OFFLINE_EXPLICIT, SafetyGates.ackPath(false))
    }
}
