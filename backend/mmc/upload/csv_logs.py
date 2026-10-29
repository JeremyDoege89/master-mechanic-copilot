"""CSV log ingestion (M5U §3): Torque / Car Scanner / FORScan — deterministic.

Stdlib ``csv`` only, no LLM. A parsed log becomes one ``csv_log`` evidence
plus one observation per canonical series (median, confidence 1.0);
posteriors move only when a derived test is submitted through the normal
evaluator + ``test_result`` fold — observations alone never move them
(matches ``sessions.compute_state``).
"""

from __future__ import annotations

import csv
import io
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Normalized header -> (canonical series, unit family). Unit families drive
# conversion: temp F->C, pressure psi->kPa, speed mph->km/h, g/s passthrough.
HEADER_MAP: dict[str, tuple[str, str]] = {
    # RPM
    "rpm": ("rpm", "rpm"),
    "engine rpm": ("rpm", "rpm"),
    "engine rpm(rpm)": ("rpm", "rpm"),
    "engine rpm (rpm)": ("rpm", "rpm"),
    "rpm(rpm)": ("rpm", "rpm"),
    "rpm [rpm]": ("rpm", "rpm"),
    # ECT
    "ect": ("ect_c", "c"),
    "ect(c)": ("ect_c", "c"),
    "ect [c]": ("ect_c", "c"),
    "ect(f)": ("ect_c", "f"),
    "coolant": ("ect_c", "c"),
    "coolant temp": ("ect_c", "c"),
    "coolant [c]": ("ect_c", "c"),
    "engine coolant temperature": ("ect_c", "c"),
    "engine coolant temperature(c)": ("ect_c", "c"),
    "engine coolant temperature(f)": ("ect_c", "f"),
    "engine coolant temperature (f)": ("ect_c", "f"),
    # IAT
    "iat": ("iat_c", "c"),
    "iat(c)": ("iat_c", "c"),
    "iat(f)": ("iat_c", "f"),
    "intake air temperature": ("iat_c", "c"),
    "intake air temperature(f)": ("iat_c", "f"),
    "intake air temperature (f)": ("iat_c", "f"),
    # MAF
    "maf": ("maf_gs", "gps"),
    "maf(g/s)": ("maf_gs", "gps"),
    "maf [g/s]": ("maf_gs", "gps"),
    "mass air flow": ("maf_gs", "gps"),
    "mass air flow(g/s)": ("maf_gs", "gps"),
    # MAP
    "map": ("map_kpa", "kpa"),
    "map(kpa)": ("map_kpa", "kpa"),
    "map(psi)": ("map_kpa", "psi"),
    "intake manifold pressure": ("map_kpa", "kpa"),
    "intake manifold pressure(psi)": ("map_kpa", "psi"),
    # Fuel trims (%)
    "stft b1": ("stft_b1_pct", "pct"),
    "stft bank 1": ("stft_b1_pct", "pct"),
    "stft bank 1(%)": ("stft_b1_pct", "pct"),
    "short term fuel trim bank 1(%)": ("stft_b1_pct", "pct"),
    "short term fuel trim bank 1 (%)": ("stft_b1_pct", "pct"),
    "ltft b1": ("ltft_b1_pct", "pct"),
    "ltft bank 1": ("ltft_b1_pct", "pct"),
    "ltft bank 1(%)": ("ltft_b1_pct", "pct"),
    "long term fuel trim bank 1(%)": ("ltft_b1_pct", "pct"),
    "long term fuel trim bank 1 (%)": ("ltft_b1_pct", "pct"),
    "stft b2": ("stft_b2_pct", "pct"),
    "stft bank 2(%)": ("stft_b2_pct", "pct"),
    "short term fuel trim bank 2(%)": ("stft_b2_pct", "pct"),
    "ltft b2": ("ltft_b2_pct", "pct"),
    "ltft bank 2(%)": ("ltft_b2_pct", "pct"),
    "long term fuel trim bank 2(%)": ("ltft_b2_pct", "pct"),
    # O2 B1S1 (V)
    "o2 b1s1": ("o2_b1s1_v", "v"),
    "o2 sensor bank 1 sensor 1(v)": ("o2_b1s1_v", "v"),
    "o2 b1s1 [v]": ("o2_b1s1_v", "v"),
    # Voltage (V)
    "voltage": ("voltage_v", "v"),
    "battery voltage": ("voltage_v", "v"),
    "battery voltage(v)": ("voltage_v", "v"),
    "control module voltage(v)": ("voltage_v", "v"),
    # Load (%)
    "load": ("load_pct", "pct"),
    "engine load": ("load_pct", "pct"),
    "calculated load(%)": ("load_pct", "pct"),
    "engine load(%)": ("load_pct", "pct"),
    # Speed
    "speed": ("speed_kph", "kph"),
    "speed(km/h)": ("speed_kph", "kph"),
    "vehicle speed": ("speed_kph", "kph"),
    "gps speed(mph)": ("speed_kph", "mph"),
    "speed(mph)": ("speed_kph", "mph"),
}

TIME_HEADERS = frozenset(
    {
        "time", "time(s)", "time (s)", "gps time", "device time",
        "timestamp", "time [s]",
    }
)

IDLE_RPM_LO, IDLE_RPM_HI = 650.0, 900.0
CRUISE_RPM_LO, CRUISE_RPM_HI = 2000.0, 3000.0


def _normalize(header: str) -> str:
    return " ".join(header.strip().lower().split())


def _to_float(raw: str) -> float | None:
    try:
        return float(raw.strip())
    except (ValueError, AttributeError):
        return None


def _convert(value: float, family: str) -> float:
    if family == "f":
        return (value - 32.0) * 5.0 / 9.0
    if family == "psi":
        return value * 6.89476
    if family == "mph":
        return value * 1.60934
    return value


def detect_exporter(headers: list[str]) -> str:
    normed = [_normalize(h) for h in headers]
    joined = " ".join(normed)
    if "gps" in joined or any("(rpm)" in h and "engine" in h for h in normed):
        return "torque"
    if any("[" in h and "]" in h for h in normed):
        return "car_scanner"
    if normed and all(h == h.upper() or "(" not in h for h in headers if h.strip()):
        short = {h.strip().upper() for h in headers}
        if {"RPM", "ECT"} & short:
            return "forscan"
    return "generic"


@dataclass
class ParsedLog:
    exporter: str
    series: dict[str, list[float]] = field(default_factory=dict)
    ignored_columns: list[str] = field(default_factory=list)
    n_rows: int = 0


def parse_csv(data: bytes) -> ParsedLog:
    """Parse one exporter CSV into canonical SI series.

    Skips Torque ``[GPS]``-style preamble lines (everything before the
    first line that names a known column). Raises ``ValueError`` naming
    the missing time/RPM column when neither is present.
    """
    text = data.decode("utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("unparseable CSV: no header row (missing time/RPM column)")
    sample = "\n".join(lines[:5])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t"])
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO("\n".join(lines)), dialect)
    rows = list(reader)
    header_idx = None
    mapping: dict[int, tuple[str, str] | None] = {}
    has_time = False
    for i, row in enumerate(rows):
        trial: dict[int, tuple[str, str] | None] = {}
        known = 0
        time_seen = False
        for j, cell in enumerate(row):
            norm = _normalize(cell)
            if norm in TIME_HEADERS:
                trial[j] = ("time_s", "s")
                time_seen = True
            elif norm in HEADER_MAP:
                trial[j] = HEADER_MAP[norm]
                known += 1
            else:
                trial[j] = None
        if known >= 1 or (time_seen and any(
            _normalize(c) in HEADER_MAP or _normalize(c) in TIME_HEADERS
            for r in rows[i + 1 : i + 2] for c in r
        )):
            header_idx = i
            mapping = trial
            has_time = time_seen
            break
        if time_seen and known == 0:
            continue
    if header_idx is None:
        # Fall back: first row as header; if it names nothing, the file is unparseable.
        first = [_normalize(c) for c in rows[0]]
        if not any(c in HEADER_MAP or c in TIME_HEADERS for c in first):
            raise ValueError("unparseable CSV: no header row (missing time/RPM column)")
        header_idx = 0
        for j, cell in enumerate(rows[0]):
            norm = _normalize(cell)
            if norm in TIME_HEADERS:
                mapping[j] = ("time_s", "s")
                has_time = True
            elif norm in HEADER_MAP:
                mapping[j] = HEADER_MAP[norm]
            else:
                mapping[j] = None
    canonical_cols = {v[0] for v in mapping.values() if v is not None}
    if "rpm" not in canonical_cols and "time_s" not in canonical_cols:
        raise ValueError("unparseable CSV: missing time/RPM column (need one of them)")
    headers = rows[header_idx]
    exporter = detect_exporter(headers)
    series: dict[str, list[float]] = {}
    ignored: list[str] = []
    for j, cell in enumerate(headers):
        target = mapping.get(j)
        if target is None:
            if cell.strip():
                ignored.append(cell.strip())
            continue
        canon, family = target
        values = []
        for row in rows[header_idx + 1 :]:
            if j >= len(row):
                continue
            number = _to_float(row[j])
            if number is None:
                continue
            values.append(_convert(number, family))
        if values:
            series[canon] = values
    return ParsedLog(
        exporter=exporter,
        series=series,
        ignored_columns=sorted(set(ignored)),
        n_rows=max((len(v) for v in series.values()), default=0),
    )


def series_median(values: list[float]) -> float:
    return float(statistics.median(values))


def _bucket(series: dict[str, list[float]], lo: float, hi: float) -> list[int]:
    rpm = series.get("rpm", [])
    return [i for i, r in enumerate(rpm) if lo <= r <= hi]


def _bank_total(series: dict[str, list[float]], idx: list[int], bank: str) -> list[float]:
    stft = series.get(f"stft_{bank}_pct", [])
    ltft = series.get(f"ltft_{bank}_pct", [])
    totals = []
    for i in idx:
        parts = []
        if i < len(stft):
            parts.append(stft[i])
        if i < len(ltft):
            parts.append(ltft[i])
        if parts:
            totals.append(sum(parts))
    return totals


def derive_tests(parsed: ParsedLog) -> tuple[list[dict], list[dict]]:
    """Map parsed series to KP test measured payloads.

    Returns ``(derived, skipped)``; each test_ref appears at most once in
    ``derived``. ``skipped`` entries carry ``test_ref`` + ``reason``.
    """
    series = parsed.series
    derived: list[dict] = []
    skipped: list[dict] = []

    def skip(ref: str, reason: str) -> None:
        skipped.append({"test_ref": ref, "reason": reason})

    # Fuel-trim split: idle bucket vs ~2500 rpm bucket (B1 totals).
    idle_idx = _bucket(series, IDLE_RPM_LO, IDLE_RPM_HI)
    cruise_idx = _bucket(series, CRUISE_RPM_LO, CRUISE_RPM_HI)
    idle_totals = _bank_total(series, idle_idx, "b1")
    cruise_totals = _bank_total(series, cruise_idx, "b1")
    if idle_totals and cruise_totals:
        derived.append(
            {
                "test_ref": "trim_idle_vs_2500",
                "measured": {
                    "idle_trim_pct": series_median(idle_totals),
                    "cruise_trim_pct": series_median(cruise_totals),
                },
            }
        )
    else:
        skip("trim_idle_vs_2500", "need idle (650-900 rpm) and cruise (2000-3000 rpm) rows with STFT/LTFT")

    # ECT warm-up curve: full series as recorded.
    if series.get("ect_c"):
        derived.append(
            {"test_ref": "ect_warmup_curve", "measured": {"ect_series_c": list(series["ect_c"])}}
        )
    else:
        skip("ect_warmup_curve", "no ECT column")

    # MAF plausibility: mean MAF at median RPM.
    if series.get("maf_gs") and series.get("rpm"):
        derived.append(
            {
                "test_ref": "maf_pid_plausibility",
                "measured": {
                    "maf_gs": sum(series["maf_gs"]) / len(series["maf_gs"]),
                    "rpm": series_median(series["rpm"]),
                    "disp_l": 2.0,
                },
            }
        )
    else:
        skip("maf_pid_plausibility", "need MAF and RPM columns")

    # Battery voltage: running rows preferred, else rest.
    volts = series.get("voltage_v", [])
    rpm = series.get("rpm", [])
    if volts:
        running = [v for v, r in zip(volts, rpm) if r > 400] if rpm else []
        if running:
            derived.append(
                {
                    "test_ref": "battery_voltage_rest_running",
                    "measured": {"voltage_v": series_median(running), "engine_on": True},
                }
            )
        else:
            engine_on = bool(rpm) and series_median(rpm) > 400
            derived.append(
                {
                    "test_ref": "battery_voltage_rest_running",
                    "measured": {"voltage_v": series_median(volts), "engine_on": engine_on},
                }
            )
    else:
        skip("battery_voltage_rest_running", "no voltage column")

    return derived, skipped


def build_evidence(blob_ref: str, content_hash: str, parsed: ParsedLog) -> dict:
    return {
        "evidence_id": uuid.uuid4().hex,
        "type": "csv_log",
        "blob_ref": blob_ref,
        "content_hash": content_hash,
        "exporter": parsed.exporter,
        "series": sorted(parsed.series),
        "ignored_columns": list(parsed.ignored_columns),
        "ts": datetime.now(timezone.utc).isoformat(),
    }


def to_observations(parsed: ParsedLog, evidence_id: str) -> list[dict]:
    """One observation per canonical series (median; confidence 1.0)."""
    ts = datetime.now(timezone.utc).isoformat()
    extractor = f"csv:{parsed.exporter}_v1"
    observations = []
    for name in sorted(parsed.series):
        values = parsed.series[name]
        observations.append(
            {
                "observation_id": uuid.uuid4().hex,
                "feature": name,
                "value": series_median(values),
                "confidence": 1.0,
                "extractor": extractor,
                "evidence_ids": [evidence_id],
                "ts": ts,
            }
        )
    return observations
