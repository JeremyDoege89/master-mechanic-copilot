package dev.dreamz.mmc.session

import okhttp3.MultipartBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Engine-sound upload + analysis request shapes — offline, no network. */
class SessionClientSoundTest {
    private val client = SessionClient("http://example.invalid:8000/")

    @Test
    fun buildBlobRequest_postsWavFile() {
        val wav = ByteArray(44 + 44100 * 6 * 2)
        val req = client.buildBlobRequest("sess1", "engine_sound.wav", wav, "audio/wav")

        assertEquals("http://example.invalid:8000/sessions/sess1/blobs", req.url.toString())
        assertEquals("POST", req.method)
        val body = req.body as MultipartBody
        assertEquals(1, body.size)
        val disposition = body.part(0).headers?.get("Content-Disposition").orEmpty()
        assertTrue(disposition.contains("name=\"file\""))
        assertTrue(disposition.contains("filename=\"engine_sound.wav\""))
    }

    @Test
    fun buildEngineSoundBody_carriesRpmAndCylinders() {
        val body = client.buildEngineSoundBody("blob123", 750.0, 6)

        assertEquals("blob123", body["blob_ref"])
        assertEquals(750.0, body["rpm"] as Double, 0.0)
        assertEquals(6, body["cylinders"])
    }

    @Test
    fun buildEngineSoundBody_omitsBlankCylinders() {
        val body = client.buildEngineSoundBody("blob123", 750.0, null)

        assertFalse(body.containsKey("cylinders"))
        assertEquals("blob123", body["blob_ref"])
    }
}
