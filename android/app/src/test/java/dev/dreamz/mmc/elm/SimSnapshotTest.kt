package dev.dreamz.mmc.elm

import java.io.File
import org.junit.Test

/**
 * Sim parity (M3): writes each scenario's PID/DTC/Mode 06 snapshot (warm
 * idle and 2500 rpm, plus the ECT warm-up series) to
 * backend/tests/fixtures/sim_snapshots/<scenario>.json, commit-ready.
 * backend/tests/test_sim_parity.py asserts sim_scenarios.py reproduces
 * these values within 1e-6. Uses the model functions directly so the file
 * holds exact doubles, not PID-quantized bytes.
 */
class SimSnapshotTest {
    private val outDir = File("../../backend/tests/fixtures/sim_snapshots")

    private fun pointJson(sim: Elm327Sim): String {
        val dtcs = sim.storedDtcs().joinToString(",") { "\"$it\"" }
        val counts = sim.misfireCounts().entries.sortedBy { it.key }
            .joinToString(",") { "\"${it.key}\":${it.value}" }
        return "{\"rpm\":${sim.rpm},\"ect_c\":${sim.ect()}," +
            "\"maf_gs\":${sim.maf()},\"trim_total_pct\":" +
            "${if (sim.rpm > 0) sim.totalTrim() else 0.0}," +
            "\"iat_c\":${sim.iat()},\"dtcs\":[$dtcs],\"misfire_counts\":{$counts}}"
    }

    private fun snapshotJson(scenario: Scenario, name: String): String {
        val sim = Elm327Sim(Personality.GENUINE, scenario)
        sim.simTimeSec = 1200.0
        val fuelOut = scenario == Scenario.FUEL_FUSE_REMOVED
        sim.rpm = if (fuelOut) 0.0 else 750.0
        val idle = pointJson(sim)
        sim.rpm = if (fuelOut) 0.0 else 2500.0
        val cruise = pointJson(sim)
        val series = listOf(0.0, 60.0, 120.0, 300.0, 600.0, 900.0, 1200.0)
            .joinToString(",") { t ->
                sim.simTimeSec = t
                "{\"t_s\":$t,\"ect_c\":${sim.ect()}}"
            }
        return "{\"scenario\":\"$name\",\"displacement_l\":${sim.displacementL}," +
            "\"points\":{\"idle\":$idle,\"cruise2500\":$cruise}," +
            "\"ect_series\":[$series]}"
    }

    @Test
    fun writeAllScenarioSnapshots() {
        outDir.mkdirs()
        val scenarios = mapOf(
            Scenario.HEALTHY to "healthy",
            Scenario.MAF_UNPLUGGED to "maf_unplugged",
            Scenario.IAT_UNPLUGGED to "iat_unplugged",
            Scenario.VACUUM_LEAK to "vacuum_leak",
            Scenario.FUEL_FUSE_REMOVED to "fuel_fuse_removed",
            Scenario.COIL_UNPLUGGED to "coil_unplugged",
            Scenario.THERMOSTAT_STUCK_OPEN to "thermostat_stuck_open",
        )
        for ((scenario, name) in scenarios) {
            File(outDir, "$name.json").writeText(snapshotJson(scenario, name))
        }
        // Sanity: every file parses as an object with both operating points.
        for ((_, name) in scenarios) {
            val text = File(outDir, "$name.json").readText()
            assert(text.contains("\"idle\"") && text.contains("\"cruise2500\"")) { name }
        }
    }
}
