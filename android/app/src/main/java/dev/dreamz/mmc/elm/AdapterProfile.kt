package dev.dreamz.mmc.elm

/**
 * Fingerprinted adapter capabilities. [mode22] is the SPEC §6
 * "genuine/OBDLink only" gate: true only for genuine/obdlink kinds.
 * [toJson] emits the packages/schemas/adapter_profile.json shape.
 */
data class AdapterProfile(
    val kind: String,
    val supportsMultiPid: Boolean,
    val supportsHeaders: Boolean,
    val supportsMode22: Boolean,
    val supportsAtpps: Boolean,
    val supportsAtcra: Boolean,
    val protocol: String = "",
    val banner: String = "",
) {
    val mode22: Boolean get() = supportsMode22

    fun toJson(): String {
        fun esc(s: String) = s.replace("\\", "\\\\").replace("\"", "\\\"")
        return """{"kind":"${esc(kind)}","supports":{""" +
            """"headers":$supportsHeaders,"mode22":$supportsMode22,""" +
            """"multi_pid":$supportsMultiPid,"atpps":$supportsAtpps,"atcra":$supportsAtcra},""" +
            """"mode22":$mode22,"protocol":"${esc(protocol)}","banner":"${esc(banner)}"}"""
    }
}
