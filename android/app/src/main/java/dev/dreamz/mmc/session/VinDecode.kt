package dev.dreamz.mmc.session

import java.net.HttpURLConnection
import java.net.URL

/**
 * VIN handling for the live on-vehicle flow (Mazda 3 and friends).
 *
 * The backend picks the knowledge pack from `vehicle.make` (Ford →
 * ford-gen-v1, anything else → generic-gas-v1) and stores only the encrypted
 * VIN plus the last 6. This file builds that vehicle object:
 *
 * 1. Read the 17-char VIN over Mode 09 (`Elm327Driver.queryVin`).
 * 2. Best effort: decode make/model/year/displacement via the free NHTSA
 *    vPIC API (no key, read-only). The phone already has internet whenever
 *    it can reach the backend.
 * 3. Offline fallback: WMI make + model-year character from the VIN itself,
 *    model "Unknown". The pack outcome is the same (generic) — only the
 *    stored labels are less specific.
 *
 * Pure except [fetchVpic]: JSON parsing, validation, WMI/year tables and the
 * vehicle assembly are JVM-tested. The full VIN never appears in logs or
 * status text — only [last6].
 */
object VinDecode {
    const val VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/"
    const val TIMEOUT_MS = 8_000

    /** Last 6 of the VIN: the only substring ever shown or logged. */
    fun last6(vin: String): String = vin.trim().uppercase().takeLast(6)

    /** True for a plausible 17-char VIN (no I/O/Q, standard alphabet). */
    fun isPlausibleVin(vin: String): Boolean {
        val v = vin.trim().uppercase()
        if (v.length != 17) return false
        if ('I' in v || 'O' in v || 'Q' in v) return false
        return v.all { it in 'A'..'Z' || it in '0'..'9' }
    }

    /**
     * Make from the WMI (first 3 chars). Only assignments certain enough to
     * act on — anything else is "Unknown", which still folds to the generic
     * pack. Two-letter prefixes are SAE manufacturer blocks (JM = Mazda
     * Japan, JT = Toyota Japan, JH = Honda Japan); 1F/2F = Ford USA/Canada.
     */
    fun wmiMake(vin: String): String {
        val v = vin.trim().uppercase()
        if (v.length < 3) return "Unknown"
        val wmi = v.take(3)
        val two = v.take(2)
        if (two == "JM") return "Mazda"
        if (two == "JT") return "Toyota"
        if (two == "JH") return "Honda"
        if (wmi == "1HG" || wmi == "2HG" || wmi == "19X" || two == "19") return "Honda"
        if (two == "1F" || two == "2F" || two == "3F") return "Ford"
        if (two == "4T" || wmi == "5TD" || wmi == "5TB") return "Toyota"
        return "Unknown"
    }

    /**
     * Model year from the 10th VIN character (30-year cycle). Picks the most
     * recent cycle year that is not in the future (relative to [nowYear]).
     * Returns null when the character is not a valid year code.
     */
    fun vinModelYear(vin: String, nowYear: Int = java.util.Calendar.getInstance().get(java.util.Calendar.YEAR)): Int? {
        val v = vin.trim().uppercase()
        if (v.length != 17) return null
        val code = v[9]
        // Base cycle starting 1980: A-Y (no U/Z/0) then 1-9.
        val cycle = "ABCDEFGHJKLMNPRSTVWXY123456789"
        val idx = cycle.indexOf(code)
        if (idx < 0) return null
        val base = 1980 + idx
        // 30-year repeat: candidates base and base+30; take the latest that
        // is not next-year-or-later (allows Sep+ model-year rollover).
        val candidates = listOf(base, base + 30).filter { it <= nowYear + 1 }
        return candidates.maxOrNull()
    }

    /** Offline fallback vehicle: honest labels, generic pack, VIN attached. */
    fun fallbackVehicle(vin: String, nowYear: Int = 2026): Map<String, Any?> {
        val v = vin.trim().uppercase()
        return mapOf(
            "make" to wmiMake(v),
            "model" to "Unknown",
            "year" to (vinModelYear(v, nowYear) ?: 0),
            "vin" to v,
        )
    }

    /**
     * Parse a vPIC DecodeVinValues response into the vehicle map the backend
     * expects (cf. backend/tests/test_pack_selection.py: the "as VIN-decoded"
     * Mazda shape). Plain maps (not JSONObject: org.json is stubbed on the
     * JVM harness). Returns null when the response carries no usable make —
     * the caller then uses [fallbackVehicle].
     */
    fun parseVpicResponse(vin: String, responseBody: String): Map<String, Any?>? {
        val v = vin.trim().uppercase()
        val first = firstResultObject(responseBody) ?: return null
        if (first["ErrorCode"] == "1" && first["Make"].isNullOrBlank()) return null
        val make = (first["Make"] ?: "").trim().trimEnd('*')
            .lowercase().replaceFirstChar { it.uppercase() }
            .takeIf { it.isNotBlank() && it != "Not Applicable" } ?: return null
        val model = (first["Model"] ?: "").trim().takeIf { it.isNotBlank() } ?: "Unknown"
        val year = (first["ModelYear"] ?: "").trim().toIntOrNull() ?: vinModelYear(v)
        val out = mutableMapOf<String, Any?>(
            "make" to make,
            "model" to model,
            "year" to (year ?: 0),
            "vin" to v,
        )
        val dispL = (first["DisplacementL"] ?: "").trim().toDoubleOrNull()
        val cyl = (first["EngineCylinders"] ?: "").trim().toIntOrNull()
        if (dispL != null && dispL > 0) {
            out["DisplacementL"] = dispL
            out["engine"] = if (cyl != null && cyl in 2..16) "%.1fL I%d".format(dispL, cyl) else "%.1fL".format(dispL)
        }
        return out
    }

    /**
     * Minimal string-field extraction from a vPIC DecodeVinValues body: the
     * first object inside "Results". Hand-rolled (no org.json: it is stubbed
     * on the JVM unit-test harness) and limited to flat "key":"value" pairs,
     * which is all vPIC emits for the fields we read.
     */
    fun firstResultObject(body: String): Map<String, String>? {
        val results = body.substringAfter("\"Results\"", "")
        if (results.isEmpty()) return null
        val objStart = results.indexOf('{')
        if (objStart < 0) return null
        // Balance braces from the first '{' (values carry no nested objects).
        var depth = 0
        var end = -1
        for (i in objStart until results.length) {
            when (results[i]) {
                '{' -> depth++
                '}' -> {
                    depth--
                    if (depth == 0) {
                        end = i
                        break
                    }
                }
            }
        }
        if (end < 0) return null
        val obj = results.substring(objStart, end + 1)
        val field = Regex("\"([A-Za-z0-9]+)\"\\s*:\\s*\"([^\"]*)\"")
        return field.findAll(obj).associate { it.groupValues[1] to it.groupValues[2] }
    }

    /**
     * Best-effort vPIC decode over HTTPS. Returns null on any failure
     * (offline, timeout, bad response) — the caller falls back to
     * [fallbackVehicle]. [fetcher] is injectable for JVM tests.
     */
    fun fetchVpic(
        vin: String,
        fetcher: (url: String, timeoutMs: Int) -> String? = ::httpGet,
    ): Map<String, Any?>? {
        val v = vin.trim().uppercase()
        if (!isPlausibleVin(v)) return null
        return try {
            val body = fetcher("$VPIC_URL$v?format=json", TIMEOUT_MS) ?: return null
            parseVpicResponse(v, body)
        } catch (_: Exception) {
            null
        }
    }

    private fun httpGet(url: String, timeoutMs: Int): String? {
        val conn = URL(url).openConnection() as HttpURLConnection
        try {
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs
            conn.setRequestProperty("Accept", "application/json")
            if (conn.responseCode != 200) return null
            return conn.inputStream.bufferedReader().readText()
        } finally {
            conn.disconnect()
        }
    }
}
