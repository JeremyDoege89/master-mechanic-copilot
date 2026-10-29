package dev.dreamz.mmc.ui

/**
 * Safety-gate checklist logic (M6 §2). Pure Kotlin with no Android or
 * org.json dependency so the JVM unit tests can exercise it directly.
 *
 * Checklist content itself comes from the backend (`gate_checklist` on each
 * gated next_test/blocked_test, served from `mmc/safety.py`); [parse] maps
 * one entry onto [Checklist]. The Run/Record buttons stay disabled until
 * [allChecked] over [displayItems], then the app POSTs the `safety_ack`.
 */
object SafetyGates {

    data class Checklist(
        val gate: String,
        val title: String,
        val items: List<String>,
        val l1Extra: List<String>,
    )

    /** Canonical tools questionnaire (SPEC §3); "none" is the empty selection. */
    val TOOL_OPTIONS: List<String> = listOf(
        "multimeter",
        "test light",
        "jack + stands",
        "vacuum gauge",
        "smoke machine",
        "fuel pressure gauge",
        "scope",
        "enhanced scan tool",
    )

    /** Experience-level labels served at GET /meta/levels. */
    val LEVEL_LABELS: Map<String, String> = mapOf(
        "L1" to "I drive it — never opened the hood",
        "L2" to "I do my own oil changes / basic repairs",
        "L3" to "Professional technician",
    )

    fun levelLabel(level: String): String = LEVEL_LABELS[level] ?: level

    /**
     * Map one backend `gate_checklist` onto a [Checklist].
     * Returns null for "none"/unknown gates or an empty checklist.
     */
    fun parse(
        gate: String,
        title: String,
        items: List<String>,
        l1Extra: List<String>,
    ): Checklist? {
        if (gate.isEmpty() || gate == "none") return null
        if (title.isEmpty() || items.isEmpty()) return null
        return Checklist(gate = gate, title = title, items = items, l1Extra = l1Extra)
    }

    /** L1 users see the extra plainer-worded items; L2/L3 see the base list. */
    fun displayItems(checklist: Checklist, level: String): List<String> =
        if (level == "L1") checklist.items + checklist.l1Extra else checklist.items

    /** True once every displayed item is ticked. */
    fun allChecked(required: List<String>, checked: Set<String>): Boolean =
        required.all { it in checked }

    /** Which ack path a test card takes (M6 finding #8). */
    enum class AckPath { CHECKLIST, OFFLINE_EXPLICIT }

    /**
     * Cards with a backend checklist tick boxes and confirm through
     * `confirmGate` (which requires every box). Without a checklist the
     * only path is an explicit offline ack — never a `gateReady`-gated
     * confirm, which deadlocks on empty items.
     */
    fun ackPath(hasChecklist: Boolean): AckPath =
        if (hasChecklist) AckPath.CHECKLIST else AckPath.OFFLINE_EXPLICIT
}
