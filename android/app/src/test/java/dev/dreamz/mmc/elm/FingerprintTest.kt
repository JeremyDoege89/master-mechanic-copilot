package dev.dreamz.mmc.elm

import org.junit.Assert.assertEquals
import org.junit.Test

/** Each probe personality maps to the expected kind/supports flags. */
class FingerprintTest {
    private fun probeOf(replies: Map<String, String>): (String) -> String =
        { cmd -> replies[cmd] ?: "?" }

    private val genuineReplies = mapOf(
        "ATI" to "ELM327 v1.5",
        "ATPPS" to "OK",
        "ATCRA 7E8" to "OK",
        "ATS0" to "OK",
        "ATAT1" to "OK",
        "ATSH 7E0" to "OK",
        "22F190" to "62F19001465A53444",
        "ATDPN" to "A6",
    )
    private val goodCloneReplies = genuineReplies + mapOf("22F190" to "?")
    private val badCloneReplies = mapOf(
        "ATI" to "ELM327 v2.1",
        "ATPPS" to "?",
        "ATCRA 7E8" to "?",
        "ATS0" to "?",
        "ATAT1" to "OK",
        "ATSH 7E0" to "?",
        "22F190" to "?",
        "ATDPN" to "A6",
    )
    private val obdlinkReplies = genuineReplies + mapOf("ATI" to "OBDLink MX+ STN2120 v5.8")

    @Test
    fun personalitiesMapToExpectedKinds() {
        assertEquals("genuine", Fingerprint.fingerprint(probeOf(genuineReplies)).kind)
        assertEquals("good_clone", Fingerprint.fingerprint(probeOf(goodCloneReplies)).kind)
        assertEquals("limited_clone", Fingerprint.fingerprint(probeOf(badCloneReplies)).kind)
        assertEquals("obdlink", Fingerprint.fingerprint(probeOf(obdlinkReplies)).kind)
    }

    @Test
    fun supportsFlagsMatchSpec() {
        val genuine = Fingerprint.fingerprint(probeOf(genuineReplies))
        assertEquals(true, genuine.supportsMultiPid)
        assertEquals(true, genuine.supportsMode22)
        assertEquals(true, genuine.mode22)

        val good = Fingerprint.fingerprint(probeOf(goodCloneReplies))
        assertEquals(true, good.supportsMultiPid)
        assertEquals(false, good.supportsMode22)

        val bad = Fingerprint.fingerprint(probeOf(badCloneReplies))
        assertEquals(false, bad.supportsMultiPid)
        assertEquals(false, bad.supportsHeaders)
        assertEquals(false, bad.supportsMode22)
        assertEquals("ISO 15765-4 CAN 11/500", bad.protocol)
    }

    @Test
    fun emittedProfilesAreWrittenForBackendSchemaValidation() {
        val dir = java.io.File("../../backend/tests/fixtures/adapter_profiles")
        dir.mkdirs()
        val profiles = mapOf(
            "genuine" to Fingerprint.fingerprint(probeOf(genuineReplies)),
            "good_clone" to Fingerprint.fingerprint(probeOf(goodCloneReplies)),
            "limited_clone" to Fingerprint.fingerprint(probeOf(badCloneReplies)),
            "obdlink" to Fingerprint.fingerprint(probeOf(obdlinkReplies)),
        )
        for ((name, profile) in profiles) {
            java.io.File(dir, "$name.json").writeText(profile.toJson())
        }
        assertEquals(4, dir.listFiles { f -> f.extension == "json" }!!.size)
    }
}
