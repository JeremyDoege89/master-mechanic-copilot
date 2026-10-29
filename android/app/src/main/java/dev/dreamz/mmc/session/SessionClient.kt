package dev.dreamz.mmc.session

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Backend HTTP + WebSocket client for the M4 gate flow (M4).
 *
 * Blocking calls: invoke from a background thread. Returns parsed
 * [JSONObject]s whose shapes match the backend routes and packages/schemas.
 */
class SessionClient(baseUrl: String) {
    private val base = baseUrl.trimEnd('/')
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private fun post(path: String, body: JSONObject): JSONObject {
        val req = Request.Builder()
            .url(base + path)
            .post(body.toString().toRequestBody("application/json".toMediaType()))
            .build()
        http.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) error("POST $path -> ${resp.code}: $text")
            return JSONObject(text)
        }
    }

    private fun get(path: String): JSONObject {
        val req = Request.Builder().url(base + path).get().build()
        http.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) error("GET $path -> ${resp.code}: $text")
            return JSONObject(text)
        }
    }

    fun createSession(
        userLevel: String,
        tools: List<String>,
        mode: String,
        vehicle: JSONObject? = null,
    ): String {
        val body = JSONObject()
            .put("user_level", userLevel)
            .put("tools", org.json.JSONArray(tools))
            .put("mode", mode)
        if (vehicle != null) body.put("vehicle", vehicle)
        return post("/sessions", body).getString("session_id")
    }

    fun postEvent(sessionId: String, type: String, payload: JSONObject): JSONObject =
        post("/sessions/$sessionId/events", JSONObject().put("type", type).put("payload", payload))

    fun ackGate(sessionId: String, gate: String): JSONObject =
        postEvent(sessionId, "safety_ack", JSONObject().put("gate", gate))

    fun getState(sessionId: String): JSONObject = get("/sessions/$sessionId/state")

    /** Level labels + tools questionnaire (M6 §1); falls back offline. */
    fun getLevels(): JSONObject = get("/meta/levels")

    /** Spend ledgers for the read-only Settings line (M6 §6, no keys). */
    fun getBudget(): JSONObject = get("/budget")

    fun postTestResult(
        sessionId: String,
        testRef: String,
        measured: JSONObject,
        conditions: JSONObject = JSONObject(),
        evidenceIds: List<String> = emptyList(),
    ): JSONObject {
        val body = JSONObject()
            .put("measured", measured)
            .put("conditions", conditions)
            .put("evidence_ids", org.json.JSONArray(evidenceIds))
        return post("/sessions/$sessionId/tests/$testRef/result", body)
    }

    /** Build (don't send) the M5U upload multipart request — pure, unit-tested. */
    fun buildUploadRequest(
        sessionId: String,
        filename: String,
        bytes: ByteArray,
        mime: String,
        kind: String,
    ): Request {
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", filename, bytes.toRequestBody(mime.toMediaType()))
            .addFormDataPart("kind", kind)
            .build()
        return Request.Builder().url("$base/sessions/$sessionId/uploads").post(multipart).build()
    }

    private fun execute(request: Request, what: String): JSONObject {
        http.newCall(request).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) error("$what -> ${resp.code}: $text")
            return JSONObject(text)
        }
    }

    /** M5U upload: manual/scan PDF, CSV log, photo or video. Returns
     * {blob_ref, sha256, evidence_id, kind}. Photos continue into the
     * existing vision/keyframe confirm flow. */
    fun uploadFile(
        sessionId: String,
        filename: String,
        bytes: ByteArray,
        mime: String,
        kind: String,
    ): JSONObject = execute(buildUploadRequest(sessionId, filename, bytes, mime, kind), "POST uploads")

    fun extractPdf(sessionId: String, blobRef: String): JSONObject =
        post("/sessions/$sessionId/uploads/pdf/extract", JSONObject().put("blob_ref", blobRef))

    fun parseCsv(sessionId: String, blobRef: String): JSONObject =
        post("/sessions/$sessionId/uploads/csv/parse", JSONObject().put("blob_ref", blobRef))

    fun analyzeVideo(sessionId: String, blobRef: String): JSONObject =
        post("/sessions/$sessionId/uploads/video/analyze", JSONObject().put("blob_ref", blobRef))

    /** Build (don't send) a generic blob upload request — pure, unit-tested. */
    fun buildBlobRequest(
        sessionId: String,
        filename: String,
        bytes: ByteArray,
        mime: String,
    ): Request {
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", filename, bytes.toRequestBody(mime.toMediaType()))
            .build()
        return Request.Builder().url("$base/sessions/$sessionId/blobs").post(multipart).build()
    }

    /** Upload raw bytes (engine-sound WAV) as a blob; returns {blob_ref, sha256}. */
    fun uploadBlob(
        sessionId: String,
        filename: String,
        bytes: ByteArray,
        mime: String,
    ): JSONObject = execute(buildBlobRequest(sessionId, filename, bytes, mime), "POST blobs")

    /** Build (don't send) the engine-sound analysis body — pure JVM map, unit-tested. */
    fun buildEngineSoundBody(blobRef: String, rpm: Double, cylinders: Int?): Map<String, Any> {
        val body = mutableMapOf<String, Any>("blob_ref" to blobRef, "rpm" to rpm)
        if (cylinders != null) body["cylinders"] = cylinders
        return body
    }

    /** M5O engine sound: DSP screening of an uploaded WAV (no LLM call). */
    fun analyzeEngineSound(
        sessionId: String,
        blobRef: String,
        rpm: Double,
        cylinders: Int?,
    ): JSONObject =
        post("/sessions/$sessionId/vision/engine-sound",
            JSONObject(buildEngineSoundBody(blobRef, rpm, cylinders) as Map<*, *>))

    /** Upload a recorded .mmrec through store/blobs.py; returns the blob_ref. */
    fun uploadMmrec(sessionId: String, file: File): String {
        val multipart = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("file", file.name, file.asRequestBody("application/zip".toMediaType()))
            .build()
        val req = Request.Builder().url("$base/sessions/$sessionId/blobs").post(multipart).build()
        http.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) error("POST blobs -> ${resp.code}: $text")
            return JSONObject(text).getString("blob_ref")
        }
    }

    fun buildReport(sessionId: String): JSONObject = post("/sessions/$sessionId/report", JSONObject())

    fun getReport(url: String): JSONObject {
        val full = if (url.startsWith("http")) url else base + url
        val req = Request.Builder().url(full).get().build()
        http.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) error("GET $url -> ${resp.code}: $text")
            return JSONObject(text)
        }
    }

    /** Open the state socket; the server pushes full state after every event.
     * Reconnect to resume (the server sends current state on connect). */
    fun openStateSocket(
        sessionId: String,
        onState: (JSONObject) -> Unit,
        onFailure: (String) -> Unit,
    ): WebSocket {
        val wsUrl = base.replaceFirst("http", "ws") + "/sessions/$sessionId/ws"
        val req = Request.Builder().url(wsUrl).build()
        return http.newWebSocket(req, object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                onState(JSONObject(text))
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                onState(JSONObject(bytes.utf8()))
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: okhttp3.Response?) {
                onFailure(t.message ?: "websocket failure")
            }
        })
    }
}
