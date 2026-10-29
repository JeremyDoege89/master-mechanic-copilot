"""Human-facing value formatting for report RENDERING only (M6 reports).

These helpers turn raw ``expected``/``measured`` payloads into short
plain-language strings for the HTML report and the web Report page. They
never touch the reproducible report JSON — ``build_report`` output is
byte-identical with or without them; only the renderers call these.
Every function is total: anything unrecognised falls back to a compact
``str()`` instead of raising, so one odd payload can never break a
report page.
"""

from __future__ import annotations

import ast
from typing import Any

#: Plain labels for known outcome slugs. Unknown slugs fall back to the
#: slug with underscores replaced by spaces.
OUTCOME_LABELS = {
    "none": "No codes",
    "p030n": "Misfire code (P030n)",
    "misfire_cyl_n": "Misfire on one cylinder",
    "primes_normally": "Primes normally",
    "lean_idle_normal_cruise": "Lean at idle, normal at cruise",
    "lean_both": "Lean at idle and cruise",
    "normal": "Normal",
    "rich": "Rich",
    "vacuum_hiss": "Hissing",
    "misfire_dropout": "Misfire dropout",
    "belt_bearing_tone": "Belt or bearing tone",
    "inconclusive": "Inconclusive",
    "switching_ok": "Switching normally",
    "lazy_stuck": "Slow or stuck",
    "maf_ok": "Airflow reading plausible",
    "maf_dead": "No airflow signal",
    "maf_under": "Airflow reading low",
    "maf_over": "Airflow reading high",
    "iat_ok": "Intake-air temperature plausible",
    "iat_open": "Intake-air sensor circuit open",
    "warms_ok": "Warms up normally",
    "stuck_open": "Thermostat stuck open",
    "in_spec": "Within specification",
    "out_of_spec": "Out of specification",
}


def outcome_label(slug: object) -> str:
    """Plain label for an outcome slug (unknown slugs are prettified)."""
    text = str(slug)
    return OUTCOME_LABELS.get(text, text.replace("_", " "))


def outcome_measured(slug: object) -> str:
    """Measured-outcome text: ``Hissing (vacuum hiss)``.

    The parenthetical carries the prettified slug so the raw code stays
    visible without underscores; when the label already says the same
    thing (``Primes normally``) it stands alone.
    """
    text = str(slug)
    pretty = text.replace("_", " ")
    label = OUTCOME_LABELS.get(text, pretty)
    if label.lower() == pretty.lower():
        return label
    return f"{label} ({pretty})"


def _is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _signed_pct(value: float) -> str:
    clean = 0.0 if abs(value) < 0.05 else value
    sign = "+" if clean > 0 else ""
    return f"{sign}{clean:.1f}%"


def _volts(value: float) -> str:
    return f"{value:.2f}"


def _short(value: Any) -> str:
    """Compact scalar for fallbacks (floats trimmed, long text cut)."""
    if isinstance(value, float):
        text = f"{value:.3f}".rstrip("0").rstrip(".")
        return text if text not in ("-0", "") else "0"
    text = str(value)
    return text if len(text) <= 60 else text[:57] + "..."


def coerce_value(raw: Any) -> Any:
    """Parse a trace-cell Python-repr string back to a value.

    Trace ``measured``/``expected`` cells arrive as ``str(dict)`` text
    (e.g. ``"{'dtcs': []}"``); anything else passes through unchanged.
    Never raises: unparseable input comes back as-is.
    """
    if not isinstance(raw, str):
        return raw
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return raw
    if isinstance(parsed, (dict, list, int, float, bool)) or parsed is None:
        return parsed
    return raw


def _unwrap_measured(raw: Any) -> Any:
    """Unwrap ``{"value": ...}`` envelopes and repr strings to the payload."""
    value = coerce_value(raw)
    if isinstance(value, dict) and set(value) == {"value"}:
        return coerce_value(value["value"])
    return value


def format_measured(test_ref: Any, raw: Any) -> str:
    """Human string for a measured payload (dict, repr string, or scalar)."""
    try:
        return _format_measured_inner(str(test_ref or ""), _unwrap_measured(raw))
    except Exception:  # noqa: BLE001 - rendering never breaks the page
        return _short(raw)


def _format_measured_inner(test_ref: str, value: Any) -> str:
    if isinstance(value, dict):
        if "dtcs" in value and isinstance(value["dtcs"], list):
            dtcs = [str(d) for d in value["dtcs"]]
            return "No codes stored" if not dtcs else ", ".join(dtcs)
        if "idle_trim_pct" in value and "cruise_trim_pct" in value:
            idle, cruise = value["idle_trim_pct"], value["cruise_trim_pct"]
            if _is_num(idle) and _is_num(cruise):
                return f"Idle {_signed_pct(float(idle))}, 2,500 rpm {_signed_pct(float(cruise))}"
        if "voltage_v" in value and _is_num(value["voltage_v"]):
            text = f"{_volts(float(value['voltage_v']))} V"
            engine_on = value.get("engine_on", None)
            if engine_on is True:
                return text + " with engine running"
            if engine_on is False:
                return text + " with engine off"
            return text
        if "misfire_counts" in value and isinstance(value["misfire_counts"], dict):
            counts = value["misfire_counts"]
            hits = sorted(
                (str(cyl), int(n))
                for cyl, n in counts.items()
                if _is_num(n) and int(n) > 0
            )
            if not hits:
                return "No misfires counted"
            return "Misfire counts: " + ", ".join(f"cyl {cyl} = {n}" for cyl, n in hits)
        if "o2_samples_v" in value and isinstance(value["o2_samples_v"], list):
            samples = [float(s) for s in value["o2_samples_v"] if _is_num(s)]
            if samples:
                return (
                    f"{len(samples)} samples, "
                    f"{min(samples):.2f}-{max(samples):.2f} V"
                )
        if "maf_gs" in value and _is_num(value["maf_gs"]):
            text = f"{float(value['maf_gs']):.2f} g/s"
            rpm = value.get("rpm", None)
            if _is_num(rpm):
                text += f" at {float(rpm):.0f} rpm"
            return text
        if set(value) == {"iat_c"} and _is_num(value["iat_c"]):
            return f"{float(value['iat_c']):.1f} °C intake air"
        if "outcome" in value:
            return outcome_measured(value["outcome"])
        return ", ".join(f"{k}: {_short(v)}" for k, v in value.items()) or "—"
    if isinstance(value, list):
        if not value:
            return "No codes stored" if test_ref == "dtc_read" else "—"
        return ", ".join(_short(v) for v in value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "—"
    if _is_num(value):
        return _short(value)
    slug = str(value)
    if slug in OUTCOME_LABELS:
        return outcome_measured(slug)
    return slug


def format_expected(expected: Any) -> str:
    """Human string for an expected payload (dict, slug, or repr string)."""
    try:
        return _format_expected_inner(coerce_value(expected))
    except Exception:  # noqa: BLE001 - rendering never breaks the page
        return _short(expected)


def _format_expected_inner(value: Any) -> str:
    if isinstance(value, dict):
        source = value.get("source", {})
        kind = source.get("kind", "") if isinstance(source, dict) else ""
        if kind == "manual_page" and _is_num(value.get("min")) and _is_num(value.get("max")):
            units = str(value.get("units", "") or "").strip()
            ref = ""
            if isinstance(source, dict) and source.get("ref"):
                ref = f" (manual {source['ref']})"
            middle = f" {units}" if units else ""
            return (
                f"Book range {_volts(float(value['min']))}"
                f"-{_volts(float(value['max']))}{middle}{ref}"
            )
        if "outcome" in value:
            return outcome_label(value["outcome"])
        return ", ".join(f"{k}: {_short(v)}" for k, v in value.items()) or "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None:
        return "—"
    if _is_num(value):
        return _short(value)
    return outcome_label(str(value))
