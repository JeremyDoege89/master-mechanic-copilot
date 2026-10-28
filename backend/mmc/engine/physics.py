"""SPEC §5 physics / rule-of-thumb checks (M3).

All ranges here are generic engineering heuristics, NOT vehicle-specific
specs. The evaluator prefers manual_page > kp > physics sources, and the UI
must label physics-derived expectations "generic".
"""

LEAN_THRESHOLD_PCT = 10.0
RICH_THRESHOLD_PCT = -10.0

O2_CROSS_VOLTAGE = 0.45
O2_MIN_SWING_LOW = 0.2
O2_MIN_SWING_HIGH = 0.7
O2_MIN_CROSSINGS = 4

ECT_NORMAL_PLATEAU_C = 80.0

VOLT_REST_MIN, VOLT_REST_MAX = 12.4, 12.7
VOLT_RUN_MIN, VOLT_RUN_MAX = 13.5, 14.7


def maf_expected(
    rpm: float,
    disp_l: float,
    map_kpa: float = 30.0,
    baro_kpa: float = 100.0,
    ve: float = 0.8,
    rho_g_per_l: float = 1.18,
) -> float:
    """Expected MAF in g/s (SPEC §5 rule of thumb).

    g/s ≈ (RPM/120) × displacement_L × VE × ρ_air × (MAP/BARO).
    """
    if rpm <= 0 or disp_l <= 0 or baro_kpa <= 0:
        return 0.0
    return (rpm / 120.0) * disp_l * ve * rho_g_per_l * (map_kpa / baro_kpa)


def trim_rpm_split(idle_total_pct: float, cruise_total_pct: float) -> str:
    """Map (idle, 2500 rpm) total trims to a discrete outcome token.

    A fixed unmetered-air leak is a shrinking fraction of total airflow as
    RPM rises, so leak ⇒ lean at idle that normalises at cruise; fuel
    delivery / MAF faults ⇒ lean at all loads.
    """
    idle_lean = idle_total_pct >= LEAN_THRESHOLD_PCT
    cruise_lean = cruise_total_pct >= LEAN_THRESHOLD_PCT
    rich = idle_total_pct <= RICH_THRESHOLD_PCT or cruise_total_pct <= RICH_THRESHOLD_PCT
    if idle_lean and not cruise_lean:
        return "lean_idle_normal_cruise"
    if idle_lean and cruise_lean:
        return "lean_both"
    if rich:
        return "rich"
    return "normal"


def o2_switching_ok(samples: list[float]) -> bool:
    """Warm narrowband upstream O2 should cross 0.45 V repeatedly (0.1–0.9 V)."""
    if len(samples) < 2:
        return False
    crossings = sum(
        1 for a, b in zip(samples, samples[1:])
        if (a < O2_CROSS_VOLTAGE) != (b < O2_CROSS_VOLTAGE)
    )
    return (
        crossings >= O2_MIN_CROSSINGS
        and min(samples) <= O2_MIN_SWING_LOW
        and max(samples) >= O2_MIN_SWING_HIGH
    )


def ect_plateau_ok(series: list[float]) -> bool:
    """True when the warm-up curve reaches a normal regulated plateau.

    Uses the mean of the last 3 samples as the plateau estimate.
    """
    if not series:
        return False
    tail = series[-3:]
    plateau = sum(tail) / len(tail)
    return plateau >= ECT_NORMAL_PLATEAU_C


def voltage_ok(v: float, engine_on: bool) -> bool:
    """Rest ≈12.4–12.7 V; running ≈13.5–14.7 V."""
    if engine_on:
        return VOLT_RUN_MIN <= v <= VOLT_RUN_MAX
    return VOLT_REST_MIN <= v <= VOLT_REST_MAX
