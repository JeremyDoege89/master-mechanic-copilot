import type { SessionEvent } from "./api";

/** Short human titles for every KP test (raw ref stays as secondary label). */
export const TEST_TITLES: Record<string, string> = {
  dtc_read: "Read fault codes",
  maf_pid_plausibility: "Airflow reading check",
  iat_plausibility: "Intake-air temperature check",
  trim_idle_vs_2500: "Fuel trims: idle vs 2,500 rpm",
  visual_intake_boot: "Look at the intake tube",
  listen_fuel_pump_prime: "Listen for the fuel pump",
  ect_warmup_curve: "Warm-up temperature curve",
  mode06_misfire_counts: "Misfire counts per cylinder",
  battery_voltage_rest_running: "Battery voltage, off vs running",
  o2_response_check: "Oxygen sensor response",
  smoke_intake_test: "Smoke-test the intake",
  fuel_pressure_test: "Fuel pressure gauge test",
  engine_sound_screen: "Engine sound check",
};

export function testTitle(ref: string): string {
  return TEST_TITLES[ref] ?? prettyRef(ref);
}

export function prettyRef(ref: string): string {
  return ref.replace(/_/g, " ");
}

export const GATE_LABELS: Record<string, string> = {
  none: "no safety gate",
  engine_running_outdoors: "engine running outdoors",
  engine_off: "engine off",
  fuel_system: "fuel system",
};

export function gateLabel(gate: string): string {
  return GATE_LABELS[gate] ?? prettyRef(gate);
}

export interface LabelCtx {
  obsByEvidence: Map<string, SessionEvent[]>;
  hypLabels: Map<string, string>;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

/** Round to 1 decimal, trimming a trailing ".0" (18 -> "18", 4.24 -> "4.2"). */
function rounded1(v: number): string {
  const r = Math.round(v * 10) / 10;
  return Number.isInteger(r) ? String(r) : r.toFixed(1);
}

function signedPct(v: number): string {
  // Snap tiny values to zero so "-0%" never renders.
  const clean = Math.abs(v) < 0.05 ? 0 : v;
  return `${clean > 0 ? "+" : ""}${rounded1(clean)}%`;
}

/** Volts to 2 decimals (14.75 V, never 14.7500000001 V). */
function fmtVolts(v: number): string {
  return (Math.round(v * 100) / 100).toFixed(2);
}

/** Generic human-facing number: round to 2 decimals, trim trailing zeros. */
function fmtNum(v: number): string {
  return String(Math.round(v * 100) / 100);
}

/** Book range for a label: volts to 2 decimals, other units rounded short. */
function fmtRange(min: unknown, max: unknown, units?: string): string {
  const u = (units ?? "").trim();
  const isVolts = /^[vV](olts?)?$/.test(u);
  const f = (x: unknown): string => {
    if (typeof x !== "number" || !Number.isFinite(x)) return String(x ?? "");
    return isVolts ? fmtVolts(x) : fmtNum(x);
  };
  return `${f(min)}–${f(max)} ${u}`.trim();
}

/** Plain-language headline + optional detail for one timeline event. */
export function describeEvent(
  e: SessionEvent,
  ctx: LabelCtx,
): { headline: string; detail?: string } {
  const p = e.payload as Record<string, unknown>;
  switch (e.type) {
    case "session_started": {
      const v = (p.vehicle as Record<string, unknown> | undefined) ?? {};
      const bits = [v.year, v.make, v.model, v.engine].filter(Boolean).join(" ");
      const lvl = typeof p.user_level === "string" ? ` · ${p.user_level}` : "";
      return { headline: bits ? `Opened ${bits}${lvl}` : "Session opened" };
    }
    case "adapter_fingerprinted": {
      const banner = typeof p.banner === "string" ? p.banner : "adapter";
      const proto = typeof p.protocol === "string" ? ` · ${p.protocol}` : "";
      const prof = typeof p.profile === "string" ? ` · ${p.profile}` : "";
      return { headline: `Adapter: ${banner}${proto}${prof}` };
    }
    case "evidence_added": {
      const etype = String(p.type ?? "evidence");
      const eid = String(p.evidence_id ?? "");
      const linked = ctx.obsByEvidence.get(eid) ?? [];
      if (etype === "dtc") {
        const dtc = linked.find((o) => String((o.payload as Record<string, unknown>).feature) === "dtc_readout");
        const dtcs = (dtc?.payload as Record<string, unknown> | undefined)?.value as
          | { dtcs?: string[] }
          | undefined;
        const list = dtcs?.dtcs ?? [];
        return {
          headline:
            list.length === 0 ? "Read fault codes: none stored" : `Read fault codes: ${list.join(", ")}`,
        };
      }
      if (etype === "obd_pid") {
        const snap = linked.find((o) =>
          String((o.payload as Record<string, unknown>).feature ?? "").startsWith("pid_snapshot"),
        );
        const val = (snap?.payload as Record<string, unknown> | undefined)?.value as
          | Record<string, unknown>
          | undefined;
        const idle = num(val?.trim_idle_pct);
        const cruise = num(val?.trim_cruise_pct);
        if (idle !== null && cruise !== null) {
          return {
            headline: `Fuel trims: ${signedPct(idle)} at idle, ${signedPct(cruise)} at 2,500 rpm`,
          };
        }
        return { headline: "Live engine data snapshot" };
      }
      if (etype === "manual_page") {
        const ranges = (p.spec_ranges as { parameter?: string; min?: number; max?: number; units?: string }[] | undefined) ?? [];
        const first = ranges[0];
        const extra = ranges.length > 1 ? ` + ${ranges.length - 1} more` : "";
        if (first) {
          return {
            headline: `Shop manual: ${prettyRef(first.parameter ?? "spec")} ${fmtRange(first.min, first.max, first.units)}${extra}`.trim(),
          };
        }
        return { headline: "Shop manual page saved" };
      }
      if (etype === "image") {
        const comps = linked
          .map((o) => String((o.payload as Record<string, unknown>).feature ?? ""))
          .filter((f) => f.startsWith("visual_component:"))
          .map((f) => f.slice("visual_component:".length).replace(/_/g, " "));
        const uniq = [...new Set(comps)];
        const first = uniq[0] ? ` — saw ${uniq[0]}` : "";
        const extra = uniq.length > 1 ? ` + ${uniq.length - 1} more parts` : "";
        return { headline: `Engine-bay photo${first}${extra}` };
      }
      if (etype === "audio") {
        const note = typeof p.note === "string" ? p.note : "";
        const m = note.match(/vacuum_hiss|misfire_dropout|belt_bearing_tone|normal|inconclusive/);
        const what = m ? ` — ${m[0].replace(/_/g, " ")}` : "";
        return { headline: `Engine sound clip${what}` };
      }
      return { headline: `Saved ${prettyRef(etype)} evidence` };
    }
    case "observation_added": {
      const feature = String(p.feature ?? "");
      const val = p.value as Record<string, unknown> | number | undefined;
      if (feature === "dtc_readout") {
        const dtcs = (val as { dtcs?: string[] } | undefined)?.dtcs ?? [];
        return {
          headline: dtcs.length === 0 ? "Read fault codes: none stored" : `Fault codes: ${dtcs.join(", ")}`,
        };
      }
      if (feature.startsWith("pid_snapshot")) {
        const v = (val ?? {}) as Record<string, unknown>;
        const idle = num(v.trim_idle_pct);
        const cruise = num(v.trim_cruise_pct);
        if (idle !== null && cruise !== null) {
          return {
            headline: `Fuel trims: ${signedPct(idle)} at idle, ${signedPct(cruise)} at 2,500 rpm`,
          };
        }
        return { headline: "Live data read" };
      }
      if (feature.startsWith("visual_component:")) {
        const name = feature.slice("visual_component:".length).replace(/_/g, " ");
        const agr = typeof p.agreement === "string" ? ` (${p.agreement})` : "";
        return { headline: `Photo shows ${name}${agr}` };
      }
      if (feature.startsWith("visual_anomaly:")) {
        const name = feature.slice("visual_anomaly:".length).replace(/_/g, " ");
        return { headline: `Possible ${name} in photo` };
      }
      if (feature.startsWith("acoustic:")) {
        return { headline: "Hiss in engine sound (signal processing, not AI)" };
      }
      return { headline: prettyRef(feature) || "Reading saved" };
    }
    case "safety_ack":
      return { headline: `Safety check done: ${gateLabel(String(p.gate ?? ""))}` };
    case "test_performed":
      return { headline: `Ran: ${testTitle(String(p.test_ref ?? ""))}` };
    case "test_result": {
      const ref = String((p as Record<string, unknown>).test_id ?? (p as Record<string, unknown>).test_ref ?? "");
      return { headline: describeTestResult(ref, p) };
    }
    case "posterior_updated": {
      const updates = (p.updates as { hypothesis_id?: string; prior?: number; posterior?: number }[] | undefined) ?? [];
      let best: { hypothesis_id?: string; prior?: number; posterior?: number } | null = null;
      for (const u of updates) {
        if (typeof u.posterior !== "number" || typeof u.prior !== "number") continue;
        if (!best || u.posterior - u.prior > (best.posterior! - best.prior!)) best = u;
      }
      if (best?.hypothesis_id) {
        const label = ctx.hypLabels.get(best.hypothesis_id) ?? prettyRef(best.hypothesis_id);
        return {
          headline: `${cap(label)} ${pct(best.prior ?? 0)} → ${pct(best.posterior ?? 0)}`,
          detail: `after ${testTitle(String(p.test_id ?? ""))}`,
        };
      }
      return { headline: `Suspects updated after ${testTitle(String(p.test_id ?? ""))}` };
    }
    case "llm_call": {
      const model = String(p.model ?? "AI");
      const short = model.includes("wonder") ? "Wonder" : model.includes("spark") ? "Spark" : model;
      return { headline: `AI review drafted (${short}, cached, $0)` };
    }
    case "report_built":
      return { headline: "Report ready: conclusion plus AI review" };
    case "hypothesis_proposed":
      return { headline: "New suspect proposed" };
    case "test_requested":
      return { headline: `Next up: ${testTitle(String(p.test_ref ?? ""))}` };
    case "codes_clear_requested":
    case "codes_cleared":
      return { headline: "Fault-code clear (gated, not a fix)" };
    case "sam_call":
      return { headline: "Photo outline drawn (SAM)" };
    case "asr_segment":
      return { headline: "Voice note transcribed" };
    default:
      return { headline: prettyRef(e.type) };
  }
}

function describeTestResult(ref: string, p: Record<string, unknown>): string {
  const measured = (p.measured as { value?: Record<string, unknown> } | undefined)?.value ?? {};
  const outcome = typeof p.outcome === "string" ? p.outcome : "";
  if (ref === "dtc_read") {
    const dtcs = (measured as { dtcs?: string[] }).dtcs ?? [];
    return dtcs.length === 0 ? "Read fault codes: none stored" : `Fault codes: ${dtcs.join(", ")}`;
  }
  if (ref === "trim_idle_vs_2500") {
    const idle = num((measured as Record<string, unknown>).idle_trim_pct);
    const cruise = num((measured as Record<string, unknown>).cruise_trim_pct);
    if (idle !== null && cruise !== null) {
      return `Fuel trims: ${signedPct(idle)} at idle, ${signedPct(cruise)} at 2,500 rpm`;
    }
  }
  if (ref === "battery_voltage_rest_running") {
    const v = num((measured as Record<string, unknown>).voltage_v);
    const expected = (p.expected as { min?: number; max?: number } | undefined) ?? {};
    if (v !== null) {
      const book =
        typeof expected.min === "number" && typeof expected.max === "number"
          ? ` (book ${fmtVolts(expected.min)}–${fmtVolts(expected.max)} V)`
          : "";
      return `Battery: ${fmtVolts(v)} V running${book} → ${prettyRef(outcome) || "read"}`;
    }
  }
  if (ref === "engine_sound_screen") {
    const o = String((measured as Record<string, unknown>).outcome ?? outcome).replace(/_/g, " ");
    return `Engine sound: ${o}`;
  }
  if (ref === "listen_fuel_pump_prime") {
    return `Fuel pump: ${prettyRef(outcome || "heard")}`;
  }
  return `${testTitle(ref)}: ${prettyRef(outcome) || "done"}`;
}

function cap(s: string): string {
  return s.length > 0 ? s[0].toUpperCase() + s.slice(1) : s;
}

/** Plain labels for known outcome slugs (mirrors backend report/format.py). */
export const OUTCOME_LABELS: Record<string, string> = {
  none: "No codes",
  p030n: "Misfire code (P030n)",
  misfire_cyl_n: "Misfire on one cylinder",
  primes_normally: "Primes normally",
  lean_idle_normal_cruise: "Lean at idle, normal at cruise",
  lean_both: "Lean at idle and cruise",
  normal: "Normal",
  rich: "Rich",
  vacuum_hiss: "Hissing",
  misfire_dropout: "Misfire dropout",
  belt_bearing_tone: "Belt or bearing tone",
  inconclusive: "Inconclusive",
  switching_ok: "Switching normally",
  lazy_stuck: "Slow or stuck",
  maf_ok: "Airflow reading plausible",
  maf_dead: "No airflow signal",
  maf_under: "Airflow reading low",
  maf_over: "Airflow reading high",
  iat_ok: "Intake-air temperature plausible",
  iat_open: "Intake-air sensor circuit open",
  warms_ok: "Warms up normally",
  stuck_open: "Thermostat stuck open",
  in_spec: "Within specification",
  out_of_spec: "Out of specification",
};

export function outcomeLabel(slug: unknown): string {
  const text = String(slug ?? "");
  return OUTCOME_LABELS[text] ?? prettyRef(text);
}

function outcomeMeasured(slug: unknown): string {
  const text = String(slug ?? "");
  const pretty = text.replace(/_/g, " ");
  const label = OUTCOME_LABELS[text] ?? pretty;
  if (label.toLowerCase() === pretty.toLowerCase()) return label;
  return `${label} (${pretty})`;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Parse a trace-cell Python-repr string (e.g. `{'dtcs': []}`) back to a
 * value. Plain slugs and unparseable text pass through as strings.
 */
export function parsePyRepr(raw: unknown): unknown {
  if (typeof raw !== "string") return raw;
  const t = raw.trim();
  if (!(t.startsWith("{") || t.startsWith("["))) return raw;
  const jsonish = t
    .replace(/\bTrue\b/g, "true")
    .replace(/\bFalse\b/g, "false")
    .replace(/\bNone\b/g, "null")
    .replace(/'/g, '"');
  try {
    return JSON.parse(jsonish) as unknown;
  } catch {
    return raw;
  }
}

function unwrapMeasured(raw: unknown): unknown {
  const v = parsePyRepr(raw);
  if (isRecord(v) && Object.keys(v).length === 1 && "value" in v) {
    return parsePyRepr(v.value);
  }
  return v;
}

function signedPct1(v: number): string {
  const clean = Math.abs(v) < 0.05 ? 0 : v;
  return `${clean > 0 ? "+" : ""}${clean.toFixed(1)}%`;
}

function shortScalar(v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) {
    return String(Math.round(v * 1000) / 1000);
  }
  const text = String(v ?? "");
  return text.length <= 60 ? text : `${text.slice(0, 57)}...`;
}

/** Raw cell content for the collapsed Tech-level detail. */
export function rawText(raw: unknown): string {
  return typeof raw === "string" ? raw : JSON.stringify(raw);
}

/** Human string for a measured payload (dict, repr string, or scalar). */
export function formatReportMeasured(testRef: string | null, raw: unknown): string {
  try {
    const value = unwrapMeasured(raw);
    if (isRecord(value)) {
      if (Array.isArray(value.dtcs)) {
        const dtcs = value.dtcs.map((d) => String(d));
        return dtcs.length === 0 ? "No codes stored" : dtcs.join(", ");
      }
      if (num(value.idle_trim_pct) !== null && num(value.cruise_trim_pct) !== null) {
        return `Idle ${signedPct1(value.idle_trim_pct as number)}, 2,500 rpm ${signedPct1(value.cruise_trim_pct as number)}`;
      }
      if (num(value.voltage_v) !== null) {
        const text = `${(value.voltage_v as number).toFixed(2)} V`;
        if (value.engine_on === true) return `${text} with engine running`;
        if (value.engine_on === false) return `${text} with engine off`;
        return text;
      }
      if (isRecord(value.misfire_counts)) {
        const hits = Object.entries(value.misfire_counts)
          .filter(([, n]) => num(n) !== null && (n as number) > 0)
          .map(([cyl, n]) => `cyl ${cyl} = ${n as number}`)
          .sort();
        return hits.length === 0 ? "No misfires counted" : `Misfire counts: ${hits.join(", ")}`;
      }
      if (Array.isArray(value.o2_samples_v)) {
        const samples = value.o2_samples_v.filter((s) => num(s) !== null) as number[];
        if (samples.length > 0) {
          return `${samples.length} samples, ${Math.min(...samples).toFixed(2)}-${Math.max(...samples).toFixed(2)} V`;
        }
      }
      if (num(value.maf_gs) !== null) {
        const rpm = num(value.rpm);
        return `${(value.maf_gs as number).toFixed(2)} g/s${rpm !== null ? ` at ${rpm.toFixed(0)} rpm` : ""}`;
      }
      if (Object.keys(value).length === 1 && "iat_c" in value && num(value.iat_c) !== null) {
        return `${(value.iat_c as number).toFixed(1)} °C intake air`;
      }
      if ("outcome" in value) {
        return outcomeMeasured(value.outcome);
      }
      const parts = Object.entries(value).map(([k, v]) => `${k}: ${shortScalar(v)}`);
      return parts.length > 0 ? parts.join(", ") : "—";
    }
    if (Array.isArray(value)) {
      if (value.length === 0) return testRef === "dtc_read" ? "No codes stored" : "—";
      return value.map((v) => shortScalar(v)).join(", ");
    }
    if (typeof value === "boolean") return value ? "yes" : "no";
    if (value === null || value === undefined) return "—";
    if (num(value) !== null) return shortScalar(value);
    const slug = String(value);
    return slug in OUTCOME_LABELS ? outcomeMeasured(slug) : slug;
  } catch {
    return shortScalar(raw);
  }
}

/** Human string for an expected payload (dict, slug, or repr string). */
export function formatReportExpected(raw: unknown): string {
  try {
    const value = parsePyRepr(raw);
    if (isRecord(value)) {
      const source = isRecord(value.source) ? value.source : {};
      if (source.kind === "manual_page" && num(value.min) !== null && num(value.max) !== null) {
        const units = String(value.units ?? "").trim();
        const ref = typeof source.ref === "string" && source.ref ? ` (manual ${source.ref})` : "";
        const middle = units ? ` ${units}` : "";
        return `Book range ${(value.min as number).toFixed(2)}-${(value.max as number).toFixed(2)}${middle}${ref}`;
      }
      if ("outcome" in value) return outcomeLabel(value.outcome);
      const parts = Object.entries(value).map(([k, v]) => `${k}: ${shortScalar(v)}`);
      return parts.length > 0 ? parts.join(", ") : "—";
    }
    if (typeof value === "boolean") return value ? "yes" : "no";
    if (value === null || value === undefined) return "—";
    if (num(value) !== null) return shortScalar(value);
    return outcomeLabel(String(value));
  } catch {
    return shortScalar(raw);
  }
}
