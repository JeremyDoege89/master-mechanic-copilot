package dev.dreamz.mmc.elm

/**
 * Clone-tolerant fingerprinting (SPEC §6): probes a capability matrix and
 * stores an adapter_profile matching packages/schemas/adapter_profile.json.
 * "?" replies are recorded, never fatal.
 */
object Fingerprint {
    private val PROTOCOL_NAMES = mapOf(
        "A6" to "ISO 15765-4 CAN 11/500",
        "A7" to "ISO 15765-4 CAN 29/500",
        "A8" to "ISO 15765-4 CAN 11/250",
    )

    /**
     * @param probe sends one adapter command line, returns the raw reply
     *   text (already '>'-stripped by the caller convention is fine).
     */
    fun fingerprint(probe: (String) -> String): AdapterProfile {
        val banner = Parsers.cleanAtResponse(probe("ATI")).replace("?", "").trim()
        val atppsOk = !probe("ATPPS").contains("?")
        val atcraOk = !probe("ATCRA 7E8").contains("?")
        probe("ATS0")
        probe("ATAT1")
        probe("ATSH 7E0")
        // 3-byte echo-tolerance probe: clones that only read 2 command bytes
        // truncate "22F190" and answer "?" (or garbage).
        val echo22 = probe("22F190")
        val echo22Ok = !echo22.contains("?") && echo22.length >= 4
        val protoRaw = Parsers.cleanAtResponse(probe("ATDPN"))
        val protocol = PROTOCOL_NAMES[protoRaw.uppercase()] ?: protoRaw

        val isObdlink = banner.contains("OBDLink", ignoreCase = true) || banner.contains("STN", ignoreCase = true)
        val kind = when {
            isObdlink -> "obdlink"
            atppsOk && atcraOk && echo22Ok -> "genuine"
            atppsOk || atcraOk || echo22Ok -> "good_clone"
            else -> "limited_clone"
        }
        val full = kind == "genuine" || kind == "obdlink"
        return AdapterProfile(
            kind = kind,
            supportsMultiPid = full || kind == "good_clone",
            supportsHeaders = kind != "limited_clone",
            supportsMode22 = full,
            supportsAtpps = atppsOk,
            supportsAtcra = atcraOk,
            protocol = protocol,
            banner = banner,
        )
    }
}
