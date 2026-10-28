package dev.dreamz.mmc.elm

/** Minimal parser for config/mode22_allowlist.yaml (`allow:` list of hex DIDs). */
object Mode22Allowlist {
    fun parseYaml(text: String): Set<String> {
        val out = mutableSetOf<String>()
        var inAllow = false
        for (raw in text.lines()) {
            val line = raw.trim()
            if (line.startsWith("#") || line.isEmpty()) continue
            if (line == "allow:") {
                inAllow = true
                continue
            }
            if (inAllow) {
                if (!line.startsWith("-")) break
                val did = line.removePrefix("-").trim().split(Regex("\\s"))[0].uppercase()
                if (did.isNotEmpty()) out.add(did)
            }
        }
        return out
    }
}
