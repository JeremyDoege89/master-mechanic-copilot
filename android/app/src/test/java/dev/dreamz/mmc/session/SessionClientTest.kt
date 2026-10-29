package dev.dreamz.mmc.session

import okhttp3.MultipartBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** M5U upload request shape — offline, no network. */
class SessionClientTest {
    private val client = SessionClient("http://example.invalid:8000/")

    @Test
    fun buildUploadRequest_postsMultipartWithKindAndFile() {
        val bytes = "%PDF-1.4 fake".toByteArray()
        val req = client.buildUploadRequest("sess123", "manual.pdf", bytes, "application/pdf", "manual_pdf")

        assertEquals("http://example.invalid:8000/sessions/sess123/uploads", req.url.toString())
        assertEquals("POST", req.method)
        val body = req.body as MultipartBody
        assertEquals(2, body.size)

        var sawFile = false
        var sawKind = false
        for (i in 0 until body.size) {
            val disposition = body.part(i).headers?.get("Content-Disposition").orEmpty()
            if (disposition.contains("name=\"file\"")) {
                sawFile = true
                assertTrue(disposition.contains("filename=\"manual.pdf\""))
            }
            if (disposition.contains("name=\"kind\"")) {
                sawKind = true
                val sink = okio.Buffer()
                body.part(i).body.writeTo(sink)
                assertEquals("manual_pdf", sink.readUtf8())
            }
        }
        assertTrue("multipart carries the file part", sawFile)
        assertTrue("multipart carries the kind field", sawKind)
    }

    @Test
    fun buildUploadRequest_csvKind() {
        val req = client.buildUploadRequest("s", "log.csv", "a,b".toByteArray(), "text/csv", "csv")
        assertTrue(req.url.toString().endsWith("/sessions/s/uploads"))
    }

    @Test
    fun clearCodesRequestBodyCarriesSnapshot() {
        val body = client.buildClearCodesRequestBody(
            listOf("P0303"),
            mapOf("ect_c" to 90),
            emptyMap(),
        )
        assertEquals(listOf("P0303"), body["dtcs"])
        assertEquals(90, (body["freeze_frame"] as Map<*, *>)["ect_c"])
    }

    @Test
    fun clearCodesConfirmBodyTypesClearCodesExactly() {
        val body = client.buildClearCodesConfirmBody("tok123", true)
        assertEquals("tok123", body["token"])
        assertEquals("CLEAR CODES", body["typed_confirmation"])
        assertEquals(true, body["engine_off_key_on"])
    }
}
