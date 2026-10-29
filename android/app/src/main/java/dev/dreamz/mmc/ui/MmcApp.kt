package dev.dreamz.mmc.ui

import android.content.Context
import android.os.Handler
import android.os.Looper
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import dev.dreamz.mmc.adapter.SimTransport
import dev.dreamz.mmc.adapter.Transport
import dev.dreamz.mmc.elm.Elm327Driver
import dev.dreamz.mmc.elm.Elm327Sim
import dev.dreamz.mmc.elm.ElmResult
import dev.dreamz.mmc.elm.ErrorKind
import dev.dreamz.mmc.elm.NoObdDataException
import dev.dreamz.mmc.elm.Parsers
import dev.dreamz.mmc.elm.Pids
import dev.dreamz.mmc.elm.Scenario
import dev.dreamz.mmc.session.BackendUrls
import dev.dreamz.mmc.session.Recorder
import dev.dreamz.mmc.session.SessionClient
import dev.dreamz.mmc.session.TransportKind
import dev.dreamz.mmc.session.TransportOptions
import dev.dreamz.mmc.voice.VoiceAudioSpec
import dev.dreamz.mmc.voice.VoiceCommands
import dev.dreamz.mmc.voice.VoiceConfirm
import dev.dreamz.mmc.voice.VoiceIntent
import dev.dreamz.mmc.voice.VoiceTalkSession
import dev.dreamz.mmc.voice.VoiceUiState
import okhttp3.WebSocket
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

enum class Screen { SETTINGS, TRANSPORT, LIVE, TEST, REPORT, UPLOAD }

/** Exact-bytes recording wrapper so the .mmrec transcript is the real exchange. */
private class RecordingTransport(private val inner: Transport, private val rec: Recorder) : Transport {
    override fun transact(line: String, timeoutMs: Int): String {
        rec.recordTx(line)
        val reply = inner.transact(line, timeoutMs)
        rec.recordRx(reply)
        return reply
    }
}

private fun measuredTemplate(testRef: String): String = when (testRef) {
    "trim_idle_vs_2500" -> """{"idle_trim_pct": 0.0, "cruise_trim_pct": 0.0}"""
    "maf_pid_plausibility" -> """{"maf_gs": 0.0, "rpm": 750.0, "disp_l": 2.0}"""
    "iat_plausibility" -> """{"iat_c": 25.0}"""
    "ect_warmup_curve" -> """{"ect_series_c": [20.0, 45.0, 70.0, 88.0, 93.0, 95.0, 95.0]}"""
    "dtc_read" -> """{"dtcs": []}"""
    "mode06_misfire_counts" -> """{"misfire_counts": {"1": 0, "2": 0, "3": 0, "4": 0}}"""
    "battery_voltage_rest_running" -> """{"voltage_v": 14.2, "engine_on": true}"""
    "o2_response_check" -> """{"o2_samples_v": [0.12, 0.82, 0.15, 0.85, 0.11, 0.88]}"""
    "fuel_pressure_test" -> """{"fuel_kpa": 350.0}"""
    else -> """{"outcome": ""}"""
}

/** Human-readable simulator scenario labels for the transport picker. */
fun scenarioLabel(s: Scenario): String = when (s) {
    Scenario.HEALTHY -> "Healthy (no fault)"
    Scenario.MAF_UNPLUGGED -> "MAF unplugged (P0102)"
    Scenario.IAT_UNPLUGGED -> "IAT unplugged (P0113)"
    Scenario.VACUUM_LEAK -> "Vacuum leak (lean at idle)"
    Scenario.FUEL_FUSE_REMOVED -> "Fuel fuse removed (no start)"
    Scenario.COIL_UNPLUGGED -> "Coil unplugged (P030n misfire)"
    Scenario.THERMOSTAT_STUCK_OPEN -> "Thermostat stuck open (P0128)"
}

/** Short description of what the app reads live for each auto-measured test. */
fun obdTestSummary(ref: String): String = when (ref) {
    "dtc_read" -> "stored trouble codes (Mode 03)"
    "maf_pid_plausibility" -> "MAF + RPM (PIDs 10, 0C)"
    "iat_plausibility" -> "intake-air temperature (PID 0F)"
    "trim_idle_vs_2500" -> "fuel trims at idle, then at 2,500 rpm"
    "ect_warmup_curve" -> "coolant temperature samples (PID 05)"
    "mode06_misfire_counts" -> "Mode 06 misfire counts"
    "o2_response_check" -> "oxygen sensor samples (PID 14)"
    else -> "live OBD data"
}

/** M4 gate-flow controller: settings → transport → live → test → report. */
class MmcController(context: Context, private val buildDefaultUrl: String) {
    val appCtx = context.applicationContext
    private val prefs = appCtx.getSharedPreferences(BackendUrls.PREFS, Context.MODE_PRIVATE)
    private val main = Handler(Looper.getMainLooper())

    var screen by mutableStateOf(Screen.SETTINGS)
    var baseUrl by mutableStateOf(resolvedUrl())
    var urlField by mutableStateOf(resolvedUrl())
    var userLevel by mutableStateOf("L1")
    val tools = mutableStateListOf<String>()
    var transportKind by mutableStateOf(TransportOptions.default)
    var simScenario by mutableStateOf(Scenario.HEALTHY)
    var status by mutableStateOf("")
    var profileText by mutableStateOf("")
    var pidText by mutableStateOf("")
    var stateJson by mutableStateOf<JSONObject?>(null)
    var sessionId by mutableStateOf<String?>(null)
    var measuredField by mutableStateOf("")
    var resultText by mutableStateOf("")
    var reportJson by mutableStateOf<JSONObject?>(null)
    var blobRef by mutableStateOf("")

    // Test-run flow state (bug 2): OBD auto-measure vs manual outcome form.
    var activeTestRef by mutableStateOf<String?>(null)
    var outcomeOptions by mutableStateOf<List<String>>(emptyList())
    var manualOutcome by mutableStateOf("")
    var showRawJson by mutableStateOf(false)
    var measuring by mutableStateOf(false)
    var measurePrompt by mutableStateOf("")
    var countdownSec by mutableStateOf(0)
    var pendingIdleTrim by mutableStateOf<Double?>(null)

    /** Live adapter handle; set on connect/resume, null when not connected. */
    private var driver: Elm327Driver? = null

    // -- M6 §1 tools questionnaire + §2 gate checkboxes ----------------------
    // Served from GET /meta/levels when reachable; the SafetyGates defaults
    // keep onboarding usable offline.
    var toolOptions by mutableStateOf(SafetyGates.TOOL_OPTIONS)
    var levelLabels by mutableStateOf(SafetyGates.LEVEL_LABELS)
    var gateChecks by mutableStateOf(mapOf<String, Set<String>>())

    // -- M6 §6 budget badge (read-only line, same numbers as the web badge).
    var budgetLine by mutableStateOf("")

    // -- M7 voice hands-free (Android-only; replay site has no mic path).
    // RECORD_AUDIO is requested only on first voice tap; spoken replies use
    // on-device TextToSpeech (Voice Transcribe has no TTS). Voice never
    // triggers clear-codes (no intent maps there).
    // End to end: mic PCM -> /sessions/{id}/asr -> live captions -> parsed
    // pending intent -> Confirm POSTs the real test-result route and reports
    // the real outcome (or the real error — never a claimed success).
    val voiceUi = VoiceUiState()
    var voiceStatus by mutableStateOf("")
    var voiceMicRationale by mutableStateOf(VoiceAudioSpec.MIC_EXPLANATION)
    var voiceRecording by mutableStateOf(false)
    var voiceStagedTrims by mutableStateOf(mapOf<String, Double>())
    private var voiceTalk: VoiceTalkSession? = null

    /** Mic -> /asr -> captions. First tap explains the mic use, then asks. */
    fun startVoiceTalk() {
        val sid = sessionId ?: run {
            voiceStatus = "Connect first — voice needs a session."
            return
        }
        try {
            voiceTalk?.close()
        } catch (e: Exception) {
            // Stale socket: fall through to the fresh turn.
        }
        voiceStatus = "Listening… say “next test” or “log 12.4 volts”."
        voiceRecording = true
        voiceTalk = VoiceTalkSession(baseUrl, sid, object : VoiceTalkSession.Events {
            override fun onPartial(caption: String) {
                main.post { voiceUi.onPartial(caption) }
            }

            override fun onFinal(caption: String) {
                main.post { onVoiceFinal(caption) }
            }

            override fun onError(message: String) {
                main.post {
                    voiceRecording = false
                    voiceStatus = "Voice error: $message"
                }
            }

            override fun onClosed() {
                main.post { voiceRecording = false }
            }
        }).also { it.start() }
    }

    /** Mic off; the socket stays open for the closing final. */
    fun stopVoiceTalk() {
        voiceRecording = false
        voiceStatus = "Stopped — waiting for the final transcript."
        bg { voiceTalk?.stopTalk() }
    }

    private fun onVoiceFinal(caption: String) {
        val rankOne = rankOneTest()?.optString("kp_test_ref")?.takeIf { it.isNotEmpty() }
            ?: "the top test"
        voiceUi.onFinal(caption, rankOneTitle = rankOne)
        val pending = voiceUi.pending
        if (pending is VoiceIntent.LogTrim) {
            voiceStagedTrims = VoiceConfirm.stageTrim(voiceStagedTrims, pending)
        }
        // A spoken "next test" advances to the rank-1 test card.
        if (VoiceCommands.parse(caption) is VoiceIntent.NextTest) {
            rankOneTest()?.optString("kp_test_ref")?.takeIf { it.isNotEmpty() }?.let { beginTest(it) }
        }
        voiceTalk?.close()
        voiceRecording = false
    }

    /** Confirm the pending card through the real test-result route. */
    fun confirmVoicePending() {
        val pending = voiceUi.pending
        val submission = VoiceConfirm.submissionFor(pending, voiceStagedTrims)
        if (submission == null) {
            voiceStatus = if (pending is VoiceIntent.LogTrim) {
                "Say both the idle and the 2500 rpm trims first."
            } else {
                "Nothing confirmable — dictate a reading first."
            }
            return
        }
        val sid = sessionId ?: run {
            voiceStatus = "Confirm failed: no session."
            return
        }
        voiceStatus = "Confirming ${submission.testRef}…"
        bg {
            try {
                val measured = JSONObject()
                for ((k, v) in submission.measured) measured.put(k, v)
                val resp = client().postTestResult(sid, submission.testRef, measured)
                val outcome = resp.getJSONObject("test_result").optString("outcome")
                main.post {
                    voiceUi.clearPending()
                    voiceStagedTrims = emptyMap()
                    voiceStatus = "Logged ${submission.testRef}: $outcome."
                }
                postState(client().getState(sid))
            } catch (e: Exception) {
                // The pending card is kept: success is reported only on HTTP 200.
                main.post { voiceStatus = "Confirm failed: ${e.message}" }
            }
        }
    }

    fun cancelVoicePending() {
        voiceUi.clearPending()
        voiceStatus = "Discarded."
    }

    fun refreshBudget() {
        bg {
            try {
                val body = client().getBudget()
                val meta = body.optJSONObject("meta")
                val baseten = body.optJSONObject("baseten")
                val line = "Meta \$${meta?.optDouble("spend_usd")}/\$${meta?.optDouble("hard_stop_usd")}" +
                    " · Baseten \$${baseten?.optDouble("spend_usd")}/\$${baseten?.optDouble("hard_stop_usd")}"
                main.post { budgetLine = line }
            } catch (_: Exception) {
                main.post { budgetLine = "Budget unavailable offline." }
            }
        }
    }

    fun refreshLevels() {
        bg {
            try {
                val meta = client().getLevels()
                val levels = meta.optJSONObject("levels")
                val toolsArr = meta.optJSONArray("tools")
                main.post {
                    if (levels != null) {
                        levelLabels = mapOf(
                            "L1" to levels.optString("L1", levelLabels["L1"] ?: "L1"),
                            "L2" to levels.optString("L2", levelLabels["L2"] ?: "L2"),
                            "L3" to levels.optString("L3", levelLabels["L3"] ?: "L3"),
                        )
                    }
                    if (toolsArr != null && toolsArr.length() > 0) {
                        toolOptions = List(toolsArr.length()) { i -> toolsArr.optString(i) }
                    }
                }
            } catch (_: Exception) {
                // Offline: keep the baked-in questionnaire.
            }
        }
    }

    /** State entry for [testRef], ranked first so acks resolve there. */
    fun testEntry(testRef: String): JSONObject? {
        val state = stateJson ?: return null
        for (key in listOf("next_tests", "blocked_tests")) {
            val arr = state.optJSONArray(key) ?: continue
            for (i in 0 until arr.length()) {
                val entry = arr.optJSONObject(i) ?: continue
                if (entry.optString("kp_test_ref") == testRef) return entry
            }
        }
        return null
    }

    fun gateForTest(testRef: String): String =
        testEntry(testRef)?.optString("safety_gate").orEmpty()

    /** Backend checklist for [testRef], or null when the test needs no gate. */
    fun gateChecklistFor(testRef: String): SafetyGates.Checklist? {
        val entry = testEntry(testRef) ?: return null
        val gate = entry.optString("safety_gate")
        val check = entry.optJSONObject("gate_checklist") ?: return SafetyGates.parse(gate, "", emptyList(), emptyList())
        val items = check.optJSONArray("items") ?: return null
        val extra = check.optJSONArray("l1_extra")
        return SafetyGates.parse(
            gate = gate,
            title = check.optString("title"),
            items = List(items.length()) { i -> items.optString(i) },
            l1Extra = if (extra == null) emptyList() else List(extra.length()) { i -> extra.optString(i) },
        )
    }

    fun gateItemsFor(testRef: String): List<String> {
        val checklist = gateChecklistFor(testRef) ?: return emptyList()
        return SafetyGates.displayItems(checklist, userLevel)
    }

    fun toggleGateItem(gate: String, item: String) {
        val cur = gateChecks[gate].orEmpty()
        gateChecks = gateChecks + (gate to if (item in cur) cur - item else cur + item)
    }

    /** True when [testRef] may run: no gate, or every box ticked. */
    fun gateReady(testRef: String): Boolean {
        val gate = gateForTest(testRef)
        if (gate.isEmpty() || gate == "none") return true
        val items = gateItemsFor(testRef)
        if (items.isEmpty()) return false
        return SafetyGates.allChecked(items, gateChecks[gate].orEmpty())
    }

    /** POST the safety_ack once every box is ticked. */
    fun confirmGate(testRef: String) {
        val gate = gateForTest(testRef)
        if (gate.isEmpty() || gate == "none" || !gateReady(testRef)) return
        if (testRef == "engine_sound_screen") {
            confirmSoundGate()
            return
        }
        ackGate(gate)
    }

    val recorder = Recorder()
    private var socket: WebSocket? = null

    companion object {
        /** Backend test kinds the app measures itself over OBD. */
        val OBD_KINDS: Set<String> = setOf("obd_read", "obd_calc", "obd_procedure")

        /** Fallback when the state entry carries no kind (offline cache, old backend). */
        val OBD_FALLBACK: Set<String> = setOf(
            "dtc_read", "maf_pid_plausibility", "iat_plausibility",
            "trim_idle_vs_2500", "ect_warmup_curve", "mode06_misfire_counts",
            "o2_response_check",
        )

        /** Stop rule mirror of backend/mmc/engine/stoprule.py. */
        const val STOP_TOP_P = 0.85
        const val STOP_EIG_BITS = 0.05
    }

    fun resolvedUrl(): String =
        BackendUrls.resolve(prefs.getString(BackendUrls.KEY_BACKEND_URL, null), buildDefaultUrl)

    fun saveUrl() {
        prefs.edit().putString(BackendUrls.KEY_BACKEND_URL, urlField.trim()).apply()
        baseUrl = resolvedUrl()
        status = "Backend URL saved."
        refreshLevels()
    }

    private fun client() = SessionClient(baseUrl)

    private fun bg(work: () -> Unit) {
        Thread {
            try {
                work()
            } catch (e: Exception) {
                main.post { status = "Error: ${e.message}" }
            }
        }.start()
    }

    private fun postState(obj: JSONObject) {
        main.post { stateJson = obj }
    }

    /** Connect through the picked transport (Sim runs the full flow, no car). */
    fun connect() {
        status = "Connecting (${transportKind.title})…"
        bg {
            val transport: Transport = when (transportKind) {
                TransportKind.SIM -> RecordingTransport(
                    SimTransport(Elm327Sim(scenario = simScenario)), recorder
                )
                else -> error("${transportKind.title} pairing lands with the on-vehicle pass; pick Simulator for now.")
            }
            val c = client()
            val mode = if (transportKind == TransportKind.SIM) "sim" else "live"
            val sid = c.createSession(userLevel, tools.toList(), mode)
            main.post { sessionId = sid }

            val d = Elm327Driver(transport)
            driver = d
            val profile = d.init()
            main.post { profileText = "${profile.kind} proto=${profile.protocol} multi=${profile.supportsMultiPid}" }

            // Live strip: one fast + one slow read, posted as rule observations.
            val rpm = readPid(d, 0x0C)
            val ect = readPid(d, 0x05)
            val maf = readPid(d, 0x10)
            main.post { pidText = "rpm=%.0f ect=%.0fC maf=%.2fg/s".format(rpm, ect, maf) }
            var n = 0
            for ((feature, value, units) in listOf(
                Triple("rpm", rpm, "rpm"), Triple("ect_c", ect, "degC"), Triple("maf_gs", maf, "g/s")
            )) {
                n += 1
                c.postEvent(sid, "observation_added", JSONObject()
                    .put("observation_id", "obs_live_$feature")
                    .put("feature", feature)
                    .put("value", value)
                    .put("units", units)
                    .put("conditions", JSONObject().put("rpm", rpm).put("ect_c", ect))
                    .put("confidence", 0.9)
                    .put("extractor", "rule"))
            }
            postState(c.getState(sid))
            socket?.cancel()
            socket = c.openStateSocket(sid, ::postState) { err ->
                main.post { status = "State socket: $err (reconnects on Connect)" }
            }
            main.post {
                status = "Live: $n observations streamed."
                screen = Screen.LIVE
            }
        }
    }

    /**
     * Resume an existing backend session after the activity was recreated
     * (bug 5): no new session is created. The live driver is rebuilt for the
     * simulator transport; other transports need a fresh Connect.
     */
    fun resumeSession() {
        val sid = sessionId ?: return
        status = "Resuming session…"
        bg {
            try {
                if (transportKind == TransportKind.SIM) {
                    val t = RecordingTransport(
                        SimTransport(Elm327Sim(scenario = simScenario)), recorder
                    )
                    driver = Elm327Driver(t).also { it.init() }
                } else {
                    driver = null
                }
                val c = client()
                postState(c.getState(sid))
                socket?.cancel()
                socket = c.openStateSocket(sid, ::postState) { err ->
                    main.post { status = "State socket: $err (reconnects on Connect)" }
                }
                main.post { status = "Session resumed." }
            } catch (e: Exception) {
                main.post { status = "Resume failed: ${e.message}" }
            }
        }
    }

    private fun readPid(driver: Elm327Driver, pid: Int): Double {
        val res = driver.query("01%02X".format(pid))
        val data = (res as? ElmResult.Data)?.ecus?.firstOrNull()?.bytes
            ?: error("no data for PID ${pid.toString(16)}")
        return Pids.decode(pid, data.drop(2).toByteArray())
    }

    fun refreshState() {
        val sid = sessionId ?: return
        bg { postState(client().getState(sid)) }
    }

    fun ackGate(gate: String) {
        val sid = sessionId ?: return
        bg {
            client().ackGate(sid, gate)
            postState(client().getState(sid))
        }
    }

    // -- test classification (bug 2) --------------------------------------

    /** Backend kind for [ref] from the ranked state, or "" when unknown. */
    fun kindOf(testRef: String): String {
        val arr = stateJson?.optJSONArray("next_tests") ?: return ""
        for (i in 0 until arr.length()) {
            val entry = arr.getJSONObject(i)
            if (entry.optString("kp_test_ref") == testRef) return entry.optString("kind")
        }
        return ""
    }

    /** True when the app can measure this test itself over OBD. */
    fun isObdTest(testRef: String): Boolean {
        val kind = kindOf(testRef)
        if (kind.isNotEmpty()) return kind in OBD_KINDS
        return testRef in OBD_FALLBACK
    }

    /** Discrete outcome choices for a manual test, from its expected outcomes. */
    fun outcomeOptionsFor(testRef: String): List<String> {
        val arr = stateJson?.optJSONArray("next_tests") ?: return emptyList()
        for (i in 0 until arr.length()) {
            val entry = arr.getJSONObject(i)
            if (entry.optString("kp_test_ref") == testRef) {
                val exp = entry.optJSONArray("expected_outcomes") ?: return emptyList()
                return List(exp.length()) { j -> exp.getJSONObject(j).optString("outcome") }
            }
        }
        return emptyList()
    }

    fun beginTest(testRef: String) {
        activeTestRef = testRef
        manualOutcome = ""
        outcomeOptions = outcomeOptionsFor(testRef)
        // Fresh tick-boxes per test run; the backend remembers the ack itself.
        gateChecks = gateChecks - gateForTest(testRef)
        resultText = ""
        measuring = false
        measurePrompt = ""
        countdownSec = 0
        pendingIdleTrim = null
        soundGateAcked = false
        soundRecording = false
        soundElapsedSec = 0
        soundStatus = ""
        soundOutcome = null
        soundEvidenceId = null
        soundRpm = null
        soundBusy = false
        measuredField = if (showRawJson) measuredTemplate(testRef) else ""
        screen = Screen.TEST
    }

    // -- OBD auto-measure (bug 2) ------------------------------------------

    private fun readPidNow(pid: Int): Double {
        val d = driver ?: error("Connect first — no live adapter.")
        val res = d.query("01%02X".format(pid))
        val data = (res as? ElmResult.Data)?.ecus?.firstOrNull()?.bytes
            ?: throw NoObdDataException("no data for PID ${pid.toString(16)}")
        return Pids.decode(pid, data.drop(2).toByteArray())
    }

    private fun readDtcsNow(d: Elm327Driver): List<String> {
        return when (val r = d.query("03")) {
            is ElmResult.Data -> {
                val bytes = r.ecus.firstOrNull()?.bytes ?: return emptyList()
                if (bytes.size < 3) emptyList()
                else Parsers.decodeDtcs(bytes.copyOfRange(1, bytes.size))
            }
            is ElmResult.TypedError ->
                if (r.kind == ErrorKind.NO_DATA) emptyList()
                else throw NoObdDataException("DTC read: ${r.kind}")
        }
    }

    private fun readTrimTotalNow(): Double = readPidNow(0x06) + readPidNow(0x07)

    /** Build the measured payload for one OBD test from the live connection. */
    private fun measureOnce(testRef: String): JSONObject {
        val d = driver ?: error("Connect first — no live adapter.")
        return when (testRef) {
            "dtc_read" -> JSONObject().put("dtcs", JSONArray(readDtcsNow(d)))
            "maf_pid_plausibility" -> {
                val rpm = readPidNow(0x0C)
                JSONObject().put("maf_gs", readPidNow(0x10)).put("rpm", rpm).put("disp_l", 2.0)
            }
            "iat_plausibility" -> JSONObject().put("iat_c", readPidNow(0x0F))
            "ect_warmup_curve" -> {
                // Live samples only: the evaluator judges the plateau from the
                // tail of the series, so three real readings are sufficient.
                val series = JSONArray()
                repeat(3) { i ->
                    if (i > 0) Thread.sleep(1500)
                    series.put(readPidNow(0x05))
                }
                JSONObject().put("ect_series_c", series)
            }
            "mode06_misfire_counts" -> {
                val entries = d.queryMode06()
                val misfire = entries.filter { it.mid == 0x0B }.ifEmpty { entries }
                val counts = JSONObject()
                for (e in misfire) counts.put(e.tid.toString(), e.value)
                JSONObject().put("misfire_counts", counts)
            }
            "o2_response_check" -> {
                val samples = JSONArray()
                repeat(6) { i ->
                    if (i > 0) Thread.sleep(800)
                    samples.put(readPidNow(0x14))
                }
                JSONObject().put("o2_samples_v", samples)
            }
            else -> error("No live measurement for $testRef")
        }
    }

    private fun submitMeasuredNow(
        testRef: String,
        measured: JSONObject,
        evidenceIds: List<String> = emptyList(),
    ) {
        val sid = sessionId ?: error("no session")
        try {
            val resp = client().postTestResult(sid, testRef, measured, evidenceIds = evidenceIds)
            val outcome = resp.getJSONObject("test_result").optString("outcome")
            main.post {
                measuredField = measured.toString(2)
                resultText = "Outcome: $outcome"
                measuring = false
                measurePrompt = ""
                countdownSec = 0
                pendingIdleTrim = null
            }
            postState(client().getState(sid))
        } catch (e: Exception) {
            main.post {
                measuring = false
                measurePrompt = ""
                countdownSec = 0
                status = "Submit failed: ${e.message}"
            }
        }
    }

    /** Single-shot OBD tests: measure live, then submit. */
    fun measureAndSubmit(testRef: String) {
        if (testRef == "trim_idle_vs_2500") {
            captureIdleTrim()
            return
        }
        measuring = true
        measurePrompt = "Measuring ${obdTestSummary(testRef)}…"
        bg {
            try {
                submitMeasuredNow(testRef, measureOnce(testRef))
            } catch (e: Exception) {
                main.post {
                    measuring = false
                    measurePrompt = ""
                    status = "Measure failed: ${e.message}"
                }
            }
        }
    }

    /** First half of the trim split test: capture idle trims, then prompt. */
    fun captureIdleTrim() {
        if (driver == null) {
            status = "Connect first — no live adapter."
            return
        }
        measuring = true
        measurePrompt = "Reading idle trims…"
        bg {
            try {
                val idle = readTrimTotalNow()
                main.post {
                    pendingIdleTrim = idle
                    measurePrompt = "Hold 2,500 rpm now"
                    startCountdown(5) { captureCruiseAndSubmit() }
                }
            } catch (e: Exception) {
                main.post {
                    measuring = false
                    measurePrompt = ""
                    status = "Measure failed: ${e.message}"
                }
            }
        }
    }

    private fun startCountdown(sec: Int, onDone: () -> Unit) {
        countdownSec = sec
        if (sec <= 0) {
            onDone()
            return
        }
        main.postDelayed({ startCountdown(sec - 1, onDone) }, 1000)
    }

    /** Second half of the trim split test: capture cruise trims, submit both. */
    fun captureCruiseAndSubmit() {
        val idle = pendingIdleTrim
        val ref = activeTestRef
        if (idle == null || ref == null) {
            measuring = false
            measurePrompt = ""
            return
        }
        measurePrompt = "Reading cruise trims…"
        bg {
            try {
                val cruise = readTrimTotalNow()
                submitMeasuredNow(
                    ref,
                    JSONObject().put("idle_trim_pct", idle).put("cruise_trim_pct", cruise),
                )
            } catch (e: Exception) {
                main.post {
                    measuring = false
                    measurePrompt = ""
                    countdownSec = 0
                    status = "Measure failed: ${e.message}"
                }
            }
        }
    }

    // -- engine-sound screen (M5O phone addition) ---------------------------
    // Record button lives on the engine_sound_screen test card: 5–8 s of
    // 16-bit mono PCM WAV at 44.1 kHz, uploaded with the live OBD rpm and
    // the cylinder count, submitted through the normal test-result path.
    // RECORD_AUDIO is requested only when the user taps record (the
    // composable shows EngineSoundSpec.MIC_EXPLANATION first); the
    // engine_running_outdoors checklist still comes first on the card.
    var soundGateAcked by mutableStateOf(false)
    var soundRecording by mutableStateOf(false)
    var soundElapsedSec by mutableStateOf(0)
    var soundStatus by mutableStateOf("")
    var cylindersField by mutableStateOf("")
    var soundOutcome by mutableStateOf<String?>(null)
    var soundEvidenceId by mutableStateOf<String?>(null)
    var soundRpm by mutableStateOf<Double?>(null)
    var soundBusy by mutableStateOf(false)
    private var soundRecorder: dev.dreamz.mmc.audio.EngineSoundRecorder? = null
    private var soundWav: ByteArray? = null

    fun hasRecordPermission(): Boolean =
        appCtx.checkSelfPermission(dev.dreamz.mmc.audio.EngineSoundSpec.PERMISSION) ==
            android.content.pm.PackageManager.PERMISSION_GRANTED

    /** Safety checklist first: the planner gate, confirmed on this card. */
    fun confirmSoundGate() {
        soundGateAcked = true
        soundStatus = "Safety confirmed: engine running, outdoors."
        ackGate("engine_running_outdoors")
    }

    fun startSoundCapture() {
        if (!soundGateAcked) {
            soundStatus = "Confirm the safety checklist first."
            return
        }
        if (!hasRecordPermission()) {
            soundStatus = "Microphone permission is needed for this test."
            return
        }
        if (soundRecording) return
        val rec = dev.dreamz.mmc.audio.EngineSoundRecorder()
        try {
            rec.start()
        } catch (e: Exception) {
            soundStatus = "Record failed: ${e.message}"
            return
        }
        soundRecorder = rec
        soundRecording = true
        soundElapsedSec = 0
        soundOutcome = null
        soundEvidenceId = null
        soundWav = null
        soundStatus = "Recording engine sound…"
        tickSoundClock()
    }

    private fun tickSoundClock() {
        val rec = soundRecorder ?: return
        if (!soundRecording) return
        soundElapsedSec = rec.elapsedSec()
        if (soundElapsedSec >= dev.dreamz.mmc.audio.EngineSoundSpec.MAX_SECONDS) {
            stopSoundCapture()
            return
        }
        main.postDelayed({ tickSoundClock() }, 1000)
    }

    fun stopSoundCapture() {
        val rec = soundRecorder ?: return
        soundRecorder = null
        soundRecording = false
        soundBusy = true
        bg {
            try {
                val wav = rec.stop()
                val rpm = try {
                    readPidNow(0x0C)
                } catch (e: Exception) {
                    null
                }
                main.post {
                    soundWav = wav
                    soundRpm = rpm
                    soundBusy = false
                    soundStatus = if (rpm == null) {
                        "Captured ${(wav.size - 44) / 2 / 44100}s, but no live rpm — reconnect the adapter."
                    } else {
                        "Captured ${(wav.size - 44) / 2 / 44100}s at ${"%.0f".format(rpm)} rpm — analyze it."
                    }
                }
            } catch (e: Exception) {
                main.post {
                    soundBusy = false
                    soundStatus = "Record failed: ${e.message}"
                }
            }
        }
    }

    private fun soundCylinders(): Int? =
        cylindersField.trim().toIntOrNull()?.takeIf { it in 2..16 }

    /** Upload the WAV, run backend DSP, keep the outcome for explicit submit. */
    fun analyzeSound() {
        val wav = soundWav
        val rpm = soundRpm
        val sid = sessionId
        if (wav == null || rpm == null || sid == null) {
            soundStatus = "Record a clip with live rpm first."
            return
        }
        soundBusy = true
        soundStatus = "Uploading engine sound…"
        bg {
            try {
                val c = client()
                val up = c.uploadBlob(sid, "engine_sound.wav", wav, "audio/wav")
                val analysis = c.analyzeEngineSound(
                    sid, up.getString("blob_ref"), rpm, soundCylinders()
                )
                main.post {
                    soundBusy = false
                    soundOutcome = analysis.getString("outcome")
                    soundEvidenceId = analysis.getString("evidence_id")
                    soundStatus = "Heard: ${analysis.getString("outcome")} " +
                        "(confidence ${analysis.optDouble("confidence", 0.0)}) — submit it."
                }
                postState(c.getState(sid))
            } catch (e: Exception) {
                main.post {
                    soundBusy = false
                    soundStatus = "Analyze failed: ${e.message}"
                }
            }
        }
    }

    /** Submit the DSP outcome through the normal test-result path. */
    fun submitSound(testRef: String) {
        val outcome = soundOutcome
        val evidence = soundEvidenceId
        if (outcome.isNullOrEmpty() || evidence.isNullOrEmpty()) {
            soundStatus = "Analyze the clip first."
            return
        }
        measuring = true
        bg {
            try {
                submitMeasuredNow(
                    testRef,
                    JSONObject().put("outcome", outcome),
                    evidenceIds = listOf(evidence),
                )
                main.post { soundStatus = "Submitted: $outcome" }
            } catch (e: Exception) {
                main.post {
                    measuring = false
                    soundStatus = "Submit failed: ${e.message}"
                }
            }
        }
    }

    /** Manual tests: submit the outcome the user picked from the form. */
    fun submitManual(testRef: String) {
        if (manualOutcome.isEmpty()) {
            status = "Pick the observed outcome first."
            return
        }
        measuring = true
        bg {
            try {
                submitMeasuredNow(testRef, JSONObject().put("outcome", manualOutcome))
            } catch (e: Exception) {
                main.post {
                    measuring = false
                    status = "Submit failed: ${e.message}"
                }
            }
        }
    }

    /** Debug path only: submit hand-edited JSON. */
    fun submitRaw(testRef: String) {
        measuring = true
        bg {
            try {
                submitMeasuredNow(testRef, JSONObject(measuredField))
            } catch (e: Exception) {
                main.post {
                    measuring = false
                    status = "Submit failed: ${e.message}"
                }
            }
        }
    }

    // -- stop-rule wording (bug 3) ------------------------------------------

    /** Client-side mirror of the backend stop rule, from the ranked state. */
    fun hasConcluded(): Boolean {
        val s = stateJson ?: return false
        val hyps = s.optJSONArray("hypotheses") ?: return false
        if (hyps.length() == 0) return false
        val top = hyps.getJSONObject(0).optDouble("current", 0.0)
        val next = s.optJSONArray("next_tests")
        val best = if (next != null && next.length() > 0) {
            next.getJSONObject(0).optDouble("eig_bits", Double.NaN)
        } else null
        if (best == null || best.isNaN() || best <= 0) return true
        return top >= STOP_TOP_P && best < STOP_EIG_BITS
    }

    fun hypothesisHeader(): String =
        if (hasConcluded()) "Top hypothesis:" else "Not enough evidence yet — current suspects:"

    fun suspectLine(): String {
        val arr = stateJson?.optJSONArray("hypotheses") ?: return "—"
        if (arr.length() == 0) return "—"
        return (0 until minOf(3, arr.length())).joinToString(", ") { i ->
            val h = arr.getJSONObject(i)
            "%s p=%.2f".format(h.optString("hypothesis_id"), h.optDouble("current"))
        }
    }

    fun topHypothesis(): String {
        val arr = stateJson?.optJSONArray("hypotheses") ?: return "—"
        if (arr.length() == 0) return "—"
        val top = arr.getJSONObject(0)
        return "%s (p=%.2f)".format(top.optString("hypothesis_id"), top.optDouble("current"))
    }

    fun rankOneTest(): JSONObject? {
        val arr = stateJson?.optJSONArray("next_tests") ?: return null
        return if (arr.length() > 0) arr.getJSONObject(0) else null
    }

    /** Stop-and-see-a-pro banner text, or null when no trigger fired. */
    fun proBanner(): String? =
        stateJson?.optJSONObject("stop_and_see_pro")?.optString("banner")?.takeIf { it.isNotEmpty() }

    fun stopTriggers(): List<String> {
        val arr = stateJson?.optJSONObject("stop_and_see_pro")?.optJSONArray("triggers") ?: return emptyList()
        return List(arr.length()) { i -> arr.optString(i) }
    }

    /** Blocked tests with their reasons — rendered, never hidden (M6 §1). */
    fun blockedTests(): List<Pair<String, String>> {
        val arr = stateJson?.optJSONArray("blocked_tests") ?: return emptyList()
        return List(arr.length()) { i ->
            val b = arr.getJSONObject(i)
            val reasons = b.optJSONArray("blocked_reasons")
            val text = if (reasons == null) "" else List(reasons.length()) { j -> reasons.optString(j) }.joinToString("; ")
            b.optString("kp_test_ref") to text
        }
    }

    fun fetchReport() {
        val sid = sessionId ?: return
        bg {
            val c = client()
            val built = c.buildReport(sid)
            val report = c.getReport(built.getString("url"))
            main.post {
                reportJson = report
                screen = Screen.REPORT
            }
        }
    }

    // -- M5U upload mode (no new permissions: system picker only) --------

    var uploadText by mutableStateOf("")

    /** New backend session in upload mode (manual pages / CSV / photo / video). */
    fun createUploadSession() {
        bg {
            val sid = client().createSession(userLevel, tools.toList(), "upload")
            main.post {
                sessionId = sid
                uploadText = ""
                status = "Upload session ready."
            }
            postState(client().getState(sid))
        }
    }

    /** Upload picked bytes, then run the per-kind backend step. Photos stop
     * after the blob upload and continue in the keyframe confirm flow. */
    fun uploadPicked(filename: String, bytes: ByteArray, mime: String, kind: String) {
        val sid = sessionId ?: run {
            status = "Create an upload session first."
            return
        }
        uploadText = "Uploading $filename…"
        bg {
            try {
                val c = client()
                val up = c.uploadFile(sid, filename, bytes, mime, kind)
                val blob = up.getString("blob_ref")
                val out = when (kind) {
                    "manual_pdf", "scan_pdf" -> c.extractPdf(sid, blob)
                    "csv" -> c.parseCsv(sid, blob)
                    "video" -> c.analyzeVideo(sid, blob)
                    else -> JSONObject().put("blob_ref", blob)
                        .put("note", "Photo stored — continue in the keyframe confirm flow.")
                }
                main.post { uploadText = out.toString(2) }
                postState(c.getState(sid))
            } catch (e: Exception) {
                main.post {
                    uploadText = ""
                    status = "Upload failed: ${e.message}"
                }
            }
        }
    }

    fun uploadMmrec() {
        val sid = sessionId ?: return
        bg {
            val file = File(appCtx.cacheDir, "session.mmrec")
            recorder.writeMmrec(
                file,
                """{"format":"mmrec/1","session_id":"$sid"}""",
                "",
            )
            val ref = client().uploadMmrec(sid, file)
            main.post {
                blobRef = ref
                status = "Uploaded .mmrec → $ref"
            }
        }
    }
}

@Composable
fun MmcApp(controller: MmcController) {
    val scroll = rememberScrollState()
    Column(
        modifier = Modifier.fillMaxSize().verticalScroll(scroll).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Master Mechanic Copilot (M4 gate)", style = MaterialTheme.typography.titleLarge)
        if (controller.status.isNotEmpty()) Text(controller.status)
        when (controller.screen) {
            Screen.SETTINGS -> SettingsScreen(controller)
            Screen.TRANSPORT -> TransportScreen(controller)
            Screen.LIVE -> LiveScreen(controller)
            Screen.TEST -> TestScreen(controller)
            Screen.REPORT -> ReportScreen(controller)
            Screen.UPLOAD -> UploadScreen(controller)
        }
    }
}

@Composable
private fun SettingsScreen(c: MmcController) {
    Text("1 · Backend & profile", style = MaterialTheme.typography.titleMedium)
    OutlinedTextField(
        value = c.urlField, onValueChange = { c.urlField = it },
        label = { Text("Backend URL (Tailscale)") }, modifier = Modifier.fillMaxWidth(),
    )
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { c.saveUrl() }) { Text("Save URL") }
        Button(onClick = { c.screen = Screen.TRANSPORT }) { Text("Next →") }
    }
    Text("Level: ${c.userLevel} — ${c.levelLabels[c.userLevel] ?: ""}")
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        for (level in listOf("L1", "L2", "L3")) {
            Button(onClick = { c.userLevel = level }) { Text(level) }
        }
    }
    if (c.budgetLine.isNotEmpty()) Text(c.budgetLine)
    Button(onClick = { c.refreshBudget() }) { Text("Refresh budget") }
    Text("Tools on hand (planner filters on these):")
    for (tool in c.toolOptions) {
        Row(modifier = Modifier.selectable(
            selected = tool in c.tools,
            onClick = { if (tool in c.tools) c.tools.remove(tool) else c.tools.add(tool) },
        )) {
            RadioButton(selected = tool in c.tools, onClick = {
                if (tool in c.tools) c.tools.remove(tool) else c.tools.add(tool)
            })
            Text(tool, modifier = Modifier.padding(top = 12.dp))
        }
    }
}

@Composable
private fun TransportScreen(c: MmcController) {
    Text("2 · Transport (Sim runs the full flow, no car)", style = MaterialTheme.typography.titleMedium)
    for (kind in TransportOptions.all) {
        Row(modifier = Modifier.selectable(
            selected = c.transportKind == kind, onClick = { c.transportKind = kind },
        )) {
            RadioButton(selected = c.transportKind == kind, onClick = { c.transportKind = kind })
            Text(kind.title, modifier = Modifier.padding(top = 12.dp))
        }
    }
    if (c.transportKind == TransportKind.SIM) {
        Text("Simulator scenario (planted fault):")
        for (s in Scenario.entries) {
            Row(modifier = Modifier.selectable(
                selected = c.simScenario == s, onClick = { c.simScenario = s },
            )) {
                RadioButton(selected = c.simScenario == s, onClick = { c.simScenario = s })
                Text(scenarioLabel(s), modifier = Modifier.padding(top = 12.dp))
            }
        }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { c.screen = Screen.SETTINGS }) { Text("← Back") }
        Button(onClick = { c.connect() }) { Text("Connect") }
    }
}

@Composable
private fun LiveScreen(c: MmcController) {
    Text("3 · Live", style = MaterialTheme.typography.titleMedium)
    if (c.profileText.isNotEmpty()) Text("Adapter: ${c.profileText}")
    if (c.pidText.isNotEmpty()) Text("PIDs: ${c.pidText}")
    c.stateJson?.optJSONObject("vehicle")?.optString("vin_last6")?.takeIf { it.isNotEmpty() }?.let {
        Text("Vehicle ····$it")
    }
    Text(c.hypothesisHeader())
    Text(if (c.hasConcluded()) c.topHypothesis() else c.suspectLine())
    c.proBanner()?.let { banner ->
        Text("Stop — see a professional.")
        Text(banner)
        for (trigger in c.stopTriggers()) Text("• $trigger")
    }
    val next = c.rankOneTest()
    if (c.proBanner() == null && next != null) {
        Text("Next test: ${next.optString("kp_test_ref")}")
        Text(next.optString("why"))
        // M6 finding #8: no one-tap ack here — the checklist lives on the
        // test card, so acknowledging means opening the test first.
        val gate = next.optString("safety_gate")
        if (gate.isNotEmpty() && gate != "none") {
            Text("Safety gate '$gate' — open the test to confirm the checklist.")
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { c.refreshState() }) { Text("Refresh") }
            Button(onClick = { c.beginTest(next.optString("kp_test_ref")) }) { Text("Run test") }
            Button(onClick = { c.fetchReport() }) { Text("Report") }
            Button(onClick = { c.screen = Screen.UPLOAD }) { Text("Upload") }
        }
    } else if (c.proBanner() == null) {
        Text("No runnable tests — adjust level/tools in Settings.")
    }
    val blocked = c.blockedTests()
    if (blocked.isNotEmpty()) {
        Text("Blocked tests (not hidden — fix the reason to unlock):")
        for ((ref, reasons) in blocked) {
            Text("$ref — $reasons")
        }
    }
    VoicePanel(c)
}

@Composable
private fun VoicePanel(c: MmcController) {
    Text("Voice (Android-only hands-free)", style = MaterialTheme.typography.titleMedium)
    Text(c.voiceMicRationale)
    // Mic permission is requested only when the user taps record (the
    // rationale above shows first), mirroring the engine-sound card.
    val permissionLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) c.startVoiceTalk()
        else c.voiceStatus = "Microphone permission is needed for voice commands."
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { c.voiceUi.setHandsFree(!c.voiceUi.handsFree) }) {
            Text(if (c.voiceUi.handsFree) "Hands-free: on" else "Hands-free: off")
        }
        Button(onClick = {
            if (c.voiceRecording) c.stopVoiceTalk()
            else if (c.hasRecordPermission()) c.startVoiceTalk()
            else permissionLauncher.launch(VoiceAudioSpec.PERMISSION)
        }) { Text(if (c.voiceRecording) "Stop" else "Hold to talk") }
    }
    if (c.voiceUi.caption.isNotEmpty()) Text("Heard: ${c.voiceUi.caption}")
    if (c.voiceUi.lastSpoken.isNotEmpty()) Text("Spoken: ${c.voiceUi.lastSpoken}")
    val pending = c.voiceUi.pending
    if (pending != null) {
        Text("Pending voice log: $pending")
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            // Confirm POSTs the real test-result route: success appears only
            // on HTTP 200, errors report the real message, and the card is
            // kept on failure (never a claimed success).
            Button(onClick = { c.confirmVoicePending() }) { Text("Confirm") }
            Button(onClick = { c.cancelVoicePending() }) { Text("Cancel") }
        }
    }
    if (c.voiceStatus.isNotEmpty()) Text(c.voiceStatus)
}

@Composable
private fun TestScreen(c: MmcController) {
    val ref = c.activeTestRef
    Text("4 · Record outcome", style = MaterialTheme.typography.titleMedium)
    if (ref == null) {
        Text("No test selected.")
        Button(onClick = { c.screen = Screen.LIVE }) { Text("← Live") }
        return
    }
    Text("Test: $ref")
    SafetyGateCard(c, ref)
    Text(c.hypothesisHeader())
    Text(if (c.hasConcluded()) c.topHypothesis() else c.suspectLine())
    if (ref == "engine_sound_screen") {
        EngineSoundBody(c, ref)
    } else if (c.isObdTest(ref)) {
        ObdTestBody(c, ref)
    } else {
        ManualTestBody(c, ref)
    }
    if (c.resultText.isNotEmpty()) Text(c.resultText)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = {
            c.showRawJson = !c.showRawJson
            if (c.showRawJson && c.measuredField.isEmpty()) c.measuredField = measuredTemplate(ref)
        }) {
            Text(if (c.showRawJson) "Hide debug JSON" else "Debug JSON")
        }
    }
    if (c.showRawJson) {
        OutlinedTextField(
            value = c.measuredField, onValueChange = { c.measuredField = it },
            label = { Text("Measured JSON (debug)") }, modifier = Modifier.fillMaxWidth(), minLines = 3,
        )
        Button(onClick = { c.submitRaw(ref) }) { Text("Submit raw JSON") }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { c.screen = Screen.LIVE }) { Text("← Live") }
        Button(onClick = { c.fetchReport() }) { Text("Report") }
    }
}

@Composable
private fun ObdTestBody(c: MmcController, ref: String) {
    if (ref == "trim_idle_vs_2500") {
        Text("Reads live: ${obdTestSummary(ref)}.")
        c.pendingIdleTrim?.let { Text("Idle trims: %.1f%%".format(it)) }
        if (c.countdownSec > 0) {
            Text("Hold 2,500 rpm now… ${c.countdownSec}s", style = MaterialTheme.typography.titleMedium)
        }
        if (c.measurePrompt.isNotEmpty()) Text(c.measurePrompt)
        Button(onClick = { c.captureIdleTrim() }, enabled = !c.measuring && c.gateReady(ref)) {
            Text("Capture idle, then cruise")
        }
    } else {
        Text("Reads live from the adapter: ${obdTestSummary(ref)}.")
        if (c.measurePrompt.isNotEmpty()) Text(c.measurePrompt)
        Button(onClick = { c.measureAndSubmit(ref) }, enabled = !c.measuring && c.gateReady(ref)) {
            Text(if (c.measuring) "Measuring…" else "Measure & submit")
        }
    }
}

/**
 * One shared safety-gate card for every gated test (M6 §2): the engine-sound
 * flow's bespoke confirm button generalised. Renders nothing for ungated
 * tests. Run/Record below stay disabled until every box is ticked.
 */
@Composable
private fun SafetyGateCard(c: MmcController, ref: String) {
    val gate = c.gateForTest(ref)
    if (gate.isEmpty() || gate == "none") return
    val checklist = c.gateChecklistFor(ref)
    val items = c.gateItemsFor(ref)
    // M6 finding #8: the offline fallback is an explicit ack, never a
    // gateReady-gated confirm (which deadlocks on empty items).
    if (SafetyGates.ackPath(checklist != null && items.isNotEmpty())
        == SafetyGates.AckPath.OFFLINE_EXPLICIT
    ) {
        Text("Safety gate: $gate (checklist unavailable offline)")
        Button(onClick = { c.ackGate(gate) }) { Text("Acknowledge safety offline: $gate") }
        return
    }
    val card = checkNotNull(checklist)
    Text(card.title, style = MaterialTheme.typography.titleMedium)
    val checked = c.gateChecks[gate].orEmpty()
    for (item in items) {
        Row(modifier = Modifier.selectable(
            selected = item in checked,
            onClick = { c.toggleGateItem(gate, item) },
        )) {
            RadioButton(selected = item in checked, onClick = { c.toggleGateItem(gate, item) })
            Text(item, modifier = Modifier.padding(top = 12.dp))
        }
    }
    if (ref == "engine_sound_screen" && c.soundGateAcked) {
        Text("Safety confirmed: engine running, outdoors.")
    } else {
        Button(
            onClick = { c.confirmGate(ref) },
            enabled = SafetyGates.allChecked(items, checked),
        ) {
            Text("$gate — confirmed")
        }
    }
}

@Composable
private fun EngineSoundBody(c: MmcController, ref: String) {
    val spec = dev.dreamz.mmc.audio.EngineSoundSpec
    // Plain mic explanation, then runtime permission only on tap.
    Text(spec.MIC_EXPLANATION)
    OutlinedTextField(
        value = c.cylindersField,
        onValueChange = { c.cylindersField = it.filter(Char::isDigit).take(2) },
        label = { Text("Cylinders (blank = vehicle on file)") },
        modifier = Modifier.fillMaxWidth(),
        singleLine = true,
    )
    val permissionLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) c.startSoundCapture()
        else c.soundStatus = "Microphone permission is needed for this test."
    }
    if (c.soundRecording) {
        Text("Recording… ${c.soundElapsedSec}s / ${spec.MAX_SECONDS}s")
        Button(onClick = { c.stopSoundCapture() }) { Text("Stop") }
    } else {
        Button(
            onClick = {
                if (c.hasRecordPermission()) c.startSoundCapture()
                else permissionLauncher.launch(spec.PERMISSION)
            },
            enabled = c.soundGateAcked && !c.soundBusy,
        ) {
            Text(if (c.soundBusy) "Working…" else "Record 5–8 s")
        }
    }
    c.soundRpm?.let { Text("Engine speed: %.0f rpm".format(it)) }
    if (c.soundStatus.isNotEmpty()) Text(c.soundStatus)
    if (!c.soundRecording && c.soundOutcome == null && !c.soundBusy) {
        // Analyze appears once a clip with live rpm is captured.
        Button(
            onClick = { c.analyzeSound() },
            enabled = c.soundGateAcked,
        ) { Text("Analyze clip") }
    }
    c.soundOutcome?.let { outcome ->
        Text("Heard: $outcome")
        Button(
            onClick = { c.submitSound(ref) },
            enabled = !c.measuring,
        ) { Text(if (c.measuring) "Submitting…" else "Submit") }
    }
}

@Composable
private fun ManualTestBody(c: MmcController, ref: String) {
    Text("What did you observe?")
    if (c.outcomeOptions.isEmpty()) {
        Text("No outcome list for this test — enable Debug JSON to submit manually.")
    }
    for (opt in c.outcomeOptions) {
        Row(modifier = Modifier.selectable(
            selected = c.manualOutcome == opt, onClick = { c.manualOutcome = opt },
        )) {
            RadioButton(selected = c.manualOutcome == opt, onClick = { c.manualOutcome = opt })
            Text(opt, modifier = Modifier.padding(top = 12.dp))
        }
    }
    Button(
        onClick = { c.submitManual(ref) },
        enabled = !c.measuring && c.manualOutcome.isNotEmpty() && c.gateReady(ref),
    ) {
        Text(if (c.measuring) "Submitting…" else "Submit")
    }
}

@Composable
private fun ReportScreen(c: MmcController) {
    Text("5 · Report", style = MaterialTheme.typography.titleMedium)
    val report = c.reportJson
    if (report == null) {
        Text("No report yet.")
    } else {
        Text(report.optString("conclusion"))
        val hyps = report.optJSONArray("hypotheses_final")
        val concluded = !report.optString("conclusion").startsWith("Not enough evidence")
        Text(if (concluded) "Final ranking:" else "Current suspects:")
        if (hyps != null) {
            for (i in 0 until minOf(3, hyps.length())) {
                val h = hyps.getJSONObject(i)
                Text("%d. %s p=%.2f".format(i + 1, h.optString("hypothesis_id"), h.optDouble("current")))
            }
        }
    }
    if (c.blobRef.isNotEmpty()) Text("Bundle: ${c.blobRef}")
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { c.uploadMmrec() }) { Text("Upload .mmrec") }
        Button(onClick = { c.screen = Screen.LIVE }) { Text("← Live") }
    }
}

@Composable
private fun UploadScreen(c: MmcController) {
    Text("6 · Upload (PDF / CSV / photo / video)", style = MaterialTheme.typography.titleMedium)
    if (c.sessionId == null) {
        Button(onClick = { c.createUploadSession() }) { Text("New upload session") }
    } else {
        Text("Session: ${c.sessionId}")
    }
    var pendingKind by androidx.compose.runtime.remember { mutableStateOf("manual_pdf") }
    var pendingMime by androidx.compose.runtime.remember { mutableStateOf(arrayOf("application/pdf")) }
    val picker = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.OpenDocument()
    ) { uri ->
        if (uri != null) {
            val bytes = c.appCtx.contentResolver.openInputStream(uri)?.readBytes() ?: ByteArray(0)
            val name = uri.lastPathSegment?.substringAfterLast('/')?.takeIf { it.isNotEmpty() }
                ?: "upload.${if (pendingKind == "video") "mp4" else pendingKind.substringAfter('_')}"
            val mime = c.appCtx.contentResolver.getType(uri) ?: pendingMime.firstOrNull().orEmpty()
            c.uploadPicked(name, bytes, mime, pendingKind)
        }
    }
    // One button per kind: the system picker needs no storage permission.
    for ((kind, label, mime) in listOf(
        Triple("manual_pdf", "Manual PDF", arrayOf("application/pdf")),
        Triple("scan_pdf", "Scan-tool PDF", arrayOf("application/pdf")),
        Triple("csv", "CSV log", arrayOf("text/csv")),
        Triple("photo", "Photo", arrayOf("image/*")),
        Triple("video", "Video", arrayOf("video/mp4", "video/quicktime")),
    )) {
        Button(onClick = {
            pendingKind = kind
            pendingMime = mime
            picker.launch(mime)
        }) { Text(label) }
    }
    if (c.uploadText.isNotEmpty()) Text(c.uploadText)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { c.screen = Screen.LIVE }) { Text("← Live") }
        Button(onClick = { c.fetchReport() }) { Text("Report") }
    }
}

/** Activity entry: builds the controller with the gradle-pinned default URL. */
fun controllerFor(context: Context): MmcController =
    MmcController(context, context.getString(dev.dreamz.mmc.R.string.default_backend_url))
