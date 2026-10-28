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
import dev.dreamz.mmc.BuildConfig
import dev.dreamz.mmc.adapter.SimTransport
import dev.dreamz.mmc.adapter.Transport
import dev.dreamz.mmc.elm.Elm327Driver
import dev.dreamz.mmc.elm.ElmResult
import dev.dreamz.mmc.elm.Pids
import dev.dreamz.mmc.session.BackendUrls
import dev.dreamz.mmc.session.Recorder
import dev.dreamz.mmc.session.SessionClient
import dev.dreamz.mmc.session.TransportKind
import dev.dreamz.mmc.session.TransportOptions
import okhttp3.WebSocket
import org.json.JSONObject
import java.io.File

enum class Screen { SETTINGS, TRANSPORT, LIVE, TEST, REPORT }

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

/** M4 gate-flow controller: settings → transport → live → test → report. */
class MmcController(context: Context, private val buildDefaultUrl: String) {
    private val appCtx = context.applicationContext
    private val prefs = appCtx.getSharedPreferences(BackendUrls.PREFS, Context.MODE_PRIVATE)
    private val main = Handler(Looper.getMainLooper())

    var screen by mutableStateOf(Screen.SETTINGS)
    var baseUrl by mutableStateOf(resolvedUrl())
    var urlField by mutableStateOf(resolvedUrl())
    var userLevel by mutableStateOf("L1")
    val tools = mutableStateListOf<String>()
    var transportKind by mutableStateOf(TransportOptions.default)
    var status by mutableStateOf("")
    var profileText by mutableStateOf("")
    var pidText by mutableStateOf("")
    var stateJson by mutableStateOf<JSONObject?>(null)
    var sessionId by mutableStateOf<String?>(null)
    var measuredField by mutableStateOf("")
    var resultText by mutableStateOf("")
    var reportJson by mutableStateOf<JSONObject?>(null)
    var blobRef by mutableStateOf("")

    val recorder = Recorder()
    private var socket: WebSocket? = null

    fun resolvedUrl(): String =
        BackendUrls.resolve(prefs.getString(BackendUrls.KEY_BACKEND_URL, null), buildDefaultUrl)

    fun saveUrl() {
        prefs.edit().putString(BackendUrls.KEY_BACKEND_URL, urlField.trim()).apply()
        baseUrl = resolvedUrl()
        status = "Backend URL saved."
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
                TransportKind.SIM -> RecordingTransport(SimTransport(), recorder)
                else -> error("${transportKind.title} pairing lands with the on-vehicle pass; pick Simulator for now.")
            }
            val c = client()
            val mode = if (transportKind == TransportKind.SIM) "sim" else "live"
            val sid = c.createSession(userLevel, tools.toList(), mode)
            main.post { sessionId = sid }

            val driver = Elm327Driver(transport)
            val profile = driver.init()
            main.post { profileText = "${profile.kind} proto=${profile.protocol} multi=${profile.supportsMultiPid}" }

            // Live strip: one fast + one slow read, posted as rule observations.
            val rpm = readPid(driver, 0x0C)
            val ect = readPid(driver, 0x05)
            val maf = readPid(driver, 0x10)
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

    fun beginTest(testRef: String) {
        measuredField = measuredTemplate(testRef)
        resultText = ""
        screen = Screen.TEST
    }

    fun submitResult(testRef: String) {
        val sid = sessionId ?: return
        bg {
            val resp = client().postTestResult(sid, testRef, JSONObject(measuredField))
            val outcome = resp.getJSONObject("test_result").optString("outcome")
            main.post { resultText = "Outcome: $outcome" }
            postState(client().getState(sid))
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
    Text("Level: ${c.userLevel}")
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        for (level in listOf("L1", "L2", "L3")) {
            Button(onClick = { c.userLevel = level }) { Text(level) }
        }
    }
    Text("Tools on hand (planner filters on these):")
    for (tool in listOf("multimeter", "smoke machine", "fuel pressure gauge", "vacuum gauge")) {
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
    Text("Top hypothesis: ${c.topHypothesis()}")
    val next = c.rankOneTest()
    if (next != null) {
        Text("Next test: ${next.optString("kp_test_ref")}")
        Text(next.optString("why"))
        val gate = next.optString("safety_gate")
        if (gate.isNotEmpty() && gate != "none") {
            Button(onClick = { c.ackGate(gate) }) { Text("Acknowledge safety: $gate") }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { c.refreshState() }) { Text("Refresh") }
            Button(onClick = { c.beginTest(next.optString("kp_test_ref")) }) { Text("Run test") }
            Button(onClick = { c.fetchReport() }) { Text("Report") }
        }
    } else {
        Text("No runnable tests — adjust level/tools in Settings.")
    }
}

@Composable
private fun TestScreen(c: MmcController) {
    Text("4 · Record outcome", style = MaterialTheme.typography.titleMedium)
    Text("Top hypothesis: ${c.topHypothesis()}")
    OutlinedTextField(
        value = c.measuredField, onValueChange = { c.measuredField = it },
        label = { Text("Measured JSON") }, modifier = Modifier.fillMaxWidth(), minLines = 3,
    )
    if (c.resultText.isNotEmpty()) Text(c.resultText)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { c.screen = Screen.LIVE }) { Text("← Live") }
        Button(onClick = {
            val ref = c.rankOneTest()?.optString("kp_test_ref") ?: return@Button
            c.submitResult(ref)
        }) { Text("Submit") }
        Button(onClick = { c.fetchReport() }) { Text("Report") }
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

/** Activity entry: builds the controller with the gradle-pinned default URL. */
fun controllerFor(context: Context): MmcController =
    MmcController(context, BuildConfig.DEFAULT_BACKEND_URL)
