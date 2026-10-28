package dev.dreamz.mmc.ui

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import dev.dreamz.mmc.elm.Scenario
import dev.dreamz.mmc.session.TransportKind
import org.json.JSONObject

/**
 * UI/session snapshot that survives activity recreation (bug 5).
 *
 * Pure data (no Context): saved into the [SavedStateHandle] on pause and
 * reapplied to a fresh controller after a restart. When the snapshot carries
 * a session id, the controller resumes that backend session instead of
 * starting over.
 */
data class MmcSessionSnapshot(
    val screen: String = "SETTINGS",
    val sessionId: String? = null,
    val baseUrl: String = "",
    val urlField: String = "",
    val userLevel: String = "L1",
    val tools: List<String> = emptyList(),
    val transportKind: String = "SIM",
    val simScenario: String = "HEALTHY",
    val status: String = "",
    val profileText: String = "",
    val pidText: String = "",
    val stateJson: String? = null,
    val reportJson: String? = null,
    val blobRef: String = "",
    val activeTestRef: String? = null,
    val manualOutcome: String = "",
    val showRawJson: Boolean = false,
) {
    companion object {
        fun from(c: MmcController): MmcSessionSnapshot = MmcSessionSnapshot(
            screen = c.screen.name,
            sessionId = c.sessionId,
            baseUrl = c.baseUrl,
            urlField = c.urlField,
            userLevel = c.userLevel,
            tools = c.tools.toList(),
            transportKind = c.transportKind.name,
            simScenario = c.simScenario.name,
            status = c.status,
            profileText = c.profileText,
            pidText = c.pidText,
            stateJson = c.stateJson?.toString(),
            reportJson = c.reportJson?.toString(),
            blobRef = c.blobRef,
            activeTestRef = c.activeTestRef,
            manualOutcome = c.manualOutcome,
            showRawJson = c.showRawJson,
        )
    }

    fun applyTo(c: MmcController) {
        c.screen = runCatching { Screen.valueOf(screen) }.getOrDefault(Screen.SETTINGS)
        c.sessionId = sessionId
        if (baseUrl.isNotEmpty()) c.baseUrl = baseUrl
        if (urlField.isNotEmpty()) c.urlField = urlField
        c.userLevel = userLevel
        c.tools.clear()
        c.tools.addAll(tools)
        c.transportKind = runCatching { TransportKind.valueOf(transportKind) }
            .getOrDefault(TransportKind.SIM)
        c.simScenario = runCatching { Scenario.valueOf(simScenario) }
            .getOrDefault(Scenario.HEALTHY)
        c.status = status
        c.profileText = profileText
        c.pidText = pidText
        c.stateJson = stateJson?.let { runCatching { JSONObject(it) }.getOrNull() }
        c.reportJson = reportJson?.let { runCatching { JSONObject(it) }.getOrNull() }
        c.blobRef = blobRef
        c.activeTestRef = activeTestRef
        c.manualOutcome = manualOutcome
        c.showRawJson = showRawJson
        if (activeTestRef != null) {
            c.outcomeOptions = c.outcomeOptionsFor(activeTestRef)
        }
    }
}

/** SavedStateHandle persistence for [MmcSessionSnapshot]. */
object MmcStateStore {
    private const val P = "mmc."
    private const val K_SCREEN = "mmc.screen"

    fun save(handle: SavedStateHandle, snap: MmcSessionSnapshot) {
        handle.set(K_SCREEN, snap.screen)
        handle.set("${P}sessionId", snap.sessionId)
        handle.set("${P}baseUrl", snap.baseUrl)
        handle.set("${P}urlField", snap.urlField)
        handle.set("${P}userLevel", snap.userLevel)
        handle.set("${P}tools", ArrayList(snap.tools))
        handle.set("${P}transportKind", snap.transportKind)
        handle.set("${P}simScenario", snap.simScenario)
        handle.set("${P}status", snap.status)
        handle.set("${P}profileText", snap.profileText)
        handle.set("${P}pidText", snap.pidText)
        handle.set("${P}stateJson", snap.stateJson)
        handle.set("${P}reportJson", snap.reportJson)
        handle.set("${P}blobRef", snap.blobRef)
        handle.set("${P}activeTestRef", snap.activeTestRef)
        handle.set("${P}manualOutcome", snap.manualOutcome)
        handle.set("${P}showRawJson", snap.showRawJson)
    }

    /** Null when nothing was ever saved (fresh start). */
    fun load(handle: SavedStateHandle): MmcSessionSnapshot? {
        if (!handle.contains(K_SCREEN)) return null
        @Suppress("UNCHECKED_CAST")
        val tools = (handle.get<ArrayList<String>>("${P}tools") as? List<String>) ?: emptyList()
        return MmcSessionSnapshot(
            screen = handle.get<String>(K_SCREEN) ?: "SETTINGS",
            sessionId = handle.get<String>("${P}sessionId"),
            baseUrl = handle.get<String>("${P}baseUrl") ?: "",
            urlField = handle.get<String>("${P}urlField") ?: "",
            userLevel = handle.get<String>("${P}userLevel") ?: "L1",
            tools = tools,
            transportKind = handle.get<String>("${P}transportKind") ?: "SIM",
            simScenario = handle.get<String>("${P}simScenario") ?: "HEALTHY",
            status = handle.get<String>("${P}status") ?: "",
            profileText = handle.get<String>("${P}profileText") ?: "",
            pidText = handle.get<String>("${P}pidText") ?: "",
            stateJson = handle.get<String>("${P}stateJson"),
            reportJson = handle.get<String>("${P}reportJson"),
            blobRef = handle.get<String>("${P}blobRef") ?: "",
            activeTestRef = handle.get<String>("${P}activeTestRef"),
            manualOutcome = handle.get<String>("${P}manualOutcome") ?: "",
            showRawJson = handle.get<Boolean>("${P}showRawJson") ?: false,
        )
    }
}

/**
 * Retains the [MmcController] across activity recreation and resumes the
 * backend session from the [SavedStateHandle] snapshot after process death.
 */
class MmcViewModel(private val savedState: SavedStateHandle) : ViewModel() {
    var controller: MmcController? = null

    fun persist(c: MmcController) {
        MmcStateStore.save(savedState, MmcSessionSnapshot.from(c))
    }

    /**
     * Apply the saved snapshot to [c]. Returns true when a snapshot existed;
     * resumes the saved backend session instead of starting over.
     */
    fun restoreInto(c: MmcController): Boolean {
        val snap = MmcStateStore.load(savedState) ?: return false
        snap.applyTo(c)
        if (snap.sessionId != null) c.resumeSession()
        return true
    }

    /** Test seam: the currently saved snapshot, if any. */
    fun savedSnapshot(): MmcSessionSnapshot? = MmcStateStore.load(savedState)

    /** True when the saved snapshot carries a session worth resuming. */
    fun hasSavedSession(): Boolean = savedSnapshot()?.sessionId != null

    /** Persist an explicit snapshot (used by tests; the app passes controllers). */
    fun persistSnapshot(snap: MmcSessionSnapshot) {
        MmcStateStore.save(savedState, snap)
    }
}
