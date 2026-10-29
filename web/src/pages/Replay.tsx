import { useCallback, useEffect, useMemo, useState } from "react";
import {
  API_BASE,
  apiGet,
  apiPost,
  type BlockedTest,
  type Level,
  type NextTest,
  type SessionEvent,
  type SessionState,
} from "../api";
import { describeEvent, gateLabel, prettyRef, testTitle } from "../labels";
import HypothesisChart from "../components/HypothesisChart";
import SamOverlay from "../components/SamOverlay";
import { hasScores, samCaption } from "../samLabels";

interface Props {
  autoJudge?: boolean;
  onReport?: (reportId: string) => void;
  onLatestReport?: (reportId: string | null) => void;
}

const JUDGE_FIXTURE = "sim-vacuum-leak.mmrec";
const COIL_FIXTURE = "sim-coil-unplugged.mmrec";

// Fixed landing order for the simulated demos (after any real-vehicle
// bundles): the vacuum-leak investigation is the default first play.
// Mirrors DEMO_SIM_ORDER in backend/mmc/replay/mmrec.py.
const SIM_ORDER = [JUDGE_FIXTURE, COIL_FIXTURE];

interface FixtureEntry {
  name: string;
  origin: string;
  label: string;
  display_label: string;
  scenario: string;
}

function orderedFixtures(list: FixtureEntry[]): FixtureEntry[] {
  return [...list].sort((a, b) => {
    const ao = a.origin === "real" ? 0 : 1;
    const bo = b.origin === "real" ? 0 : 1;
    if (ao !== bo) return ao - bo;
    if (ao === 1) {
      const ar = SIM_ORDER.indexOf(a.name);
      const br = SIM_ORDER.indexOf(b.name);
      const an = ar === -1 ? SIM_ORDER.length : ar;
      const bn = br === -1 ? SIM_ORDER.length : br;
      if (an !== bn) return an - bn;
    }
    const al = (a.display_label || a.name).toLowerCase();
    const bl = (b.display_label || b.name).toLowerCase();
    if (al !== bl) return al < bl ? -1 : 1;
    return a.name < b.name ? -1 : 1;
  });
}

const JUDGE_HEADLINE = "Watch a vacuum leak get caught.";
const JUDGE_HERO_1 =
  "These are recorded investigations — real-vehicle bundles first when the owner has exported one, simulated Fords after — all with real recorded AI outputs.";
const JUDGE_HERO_2 =
  "Every suspect probability is computed by deterministic code — the AI only reads photos, manuals and sounds; press play and watch it converge.";

const JUDGE_CHECKLIST = [
  "Press Play — the fuel-trim evidence moves the air-leak suspect past 0.8.",
  "Open its trace point — the likelihood ratio and expected-vs-measured appear.",
  "Keyframe in the evidence drawer — recorded overlay plus corroborated badge.",
  "Manual-PDF badge — the book range flips a voltage outcome.",
  "Build report — conclusion, AI review, reproducibility hashes, $0 spend.",
];

const GLYPH: Record<string, string> = {
  session_started: "●",
  adapter_fingerprinted: "◆",
  evidence_added: "■",
  observation_added: "○",
  test_requested: "▷",
  safety_ack: "✓",
  test_performed: "▶",
  test_result: "◀",
  hypothesis_proposed: "?",
  posterior_updated: "~",
  llm_call: "✎",
  sam_call: "▣",
  asr_segment: "♪",
  codes_clear_requested: "!",
  codes_cleared: "!",
  report_built: "★",
};

function instructions(
  t: Pick<NextTest, "instructions_by_level">,
  level: Level,
): string {
  return (
    t.instructions_by_level[level] ??
    t.instructions_by_level.L1 ??
    t.instructions_by_level.L3 ??
    ""
  );
}

interface OverlayMask {
  concept?: string;
  used_concept?: string;
  found?: boolean;
  svg_path?: string;
  score?: number;
  rank_label?: string;
  object_id?: string;
  box?: { x1: number; y1: number; x2: number; y2: number } | { x_min: number; x_max: number; y_min: number; y_max: number };
}

function overlayExtent(masks: OverlayMask[]): { w: number; h: number } {
  let w = 640;
  let h = 480;
  for (const m of masks) {
    if (m.box) {
      const x2 = "x2" in m.box ? m.box.x2 : m.box.x_max;
      const y2 = "y2" in m.box ? m.box.y2 : m.box.y_max;
      w = Math.max(w, x2 + 4);
      h = Math.max(h, y2 + 4);
    }
  }
  return { w, h };
}

export default function Replay({ autoJudge, onReport, onLatestReport }: Props) {
  const [sessionId, setSessionId] = useState("");
  const [typedId, setTypedId] = useState("");
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [cursor, setCursor] = useState(0);
  const [maxSeq, setMaxSeq] = useState(0);
  const [speed, setSpeed] = useState(2);
  const [playing, setPlaying] = useState(false);
  const [level, setLevel] = useState<Level>("L1");
  const [state, setState] = useState<SessionState | null>(null);
  const [status, setStatus] = useState(
    autoJudge ? "Judge demo ready." : "Load a session to begin.",
  );
  const [reportId, setReportId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState<boolean>(!!autoJudge);
  const [fixtures, setFixtures] = useState<FixtureEntry[] | null>(null);
  const [playedOrigin, setPlayedOrigin] = useState<string>("sim");

  const loadSession = useCallback(
    async (id: string, replaySpeed: number, startAtZero: boolean) => {
      setSessionId(id);
      setSpeed(replaySpeed);
      const ev = await apiGet<{ events: SessionEvent[] }>(`/sessions/${id}/events`);
      setEvents(ev.events);
      const max = ev.events.length > 0 ? ev.events[ev.events.length - 1].seq : 0;
      setMaxSeq(max);
      setCursor(startAtZero ? 0 : max);
      setPlaying(startAtZero);
      const built = ev.events.filter((e) => e.type === "report_built");
      const last = built[built.length - 1];
      const rid =
        last != null
          ? String((last.payload as Record<string, unknown>).report_id ?? "")
          : "";
      if (rid) {
        setReportId(rid);
        onLatestReport?.(rid);
        setStatus(
          `Session ${id.slice(0, 8)}… loaded (${ev.events.length} events). Its report is ready — open the Report tab.`,
        );
      } else {
        setReportId(null);
        onLatestReport?.(null);
        setStatus(`Session ${id.slice(0, 8)}… loaded (${ev.events.length} events).`);
      }
    },
    [onLatestReport],
  );

  const playFixture = useCallback(
    async (fixture: string, origin?: string) => {
      try {
        setStatus("Loading demo…");
        if (origin) setPlayedOrigin(origin);
        else {
          const known = (fixtures ?? []).find((f) => f.name === fixture);
          setPlayedOrigin(known?.origin ?? "sim");
        }
        const r = await apiPost<{ session_id: string; speed: number }>("/replay", {
          fixture,
        });
        await loadSession(r.session_id, 2, true);
      } catch (e) {
        setStatus(
          `Demo failed (bundles are built by scripts/make_demo_bundle.py): ${String(e)}`,
        );
      }
    },
    [fixtures, loadSession],
  );

  useEffect(() => {
    if (!autoJudge) return;
    let live = true;
    apiGet<{ fixtures: FixtureEntry[] }>("/fixtures")
      .then((r) => {
        if (live) setFixtures(orderedFixtures(r.fixtures ?? []));
      })
      .catch(() => {
        if (live) setFixtures(null);
      });
    return () => {
      live = false;
    };
  }, [autoJudge]);

  useEffect(() => {
    if (!sessionId) return;
    let live = true;
    apiGet<SessionState>(`/sessions/${sessionId}/state?upto_seq=${cursor}`)
      .then((s) => {
        if (live) setState(s);
      })
      .catch((e) => setStatus(`State failed: ${String(e)}`));
    return () => {
      live = false;
    };
  }, [sessionId, cursor]);

  useEffect(() => {
    if (!playing) return;
    if (cursor >= maxSeq) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setCursor((c) => Math.min(maxSeq, c + 1)), 1000 / Math.max(0.25, speed));
    return () => clearTimeout(t);
  }, [playing, cursor, maxSeq, speed]);

  const openTyped = async () => {
    const id = typedId.trim();
    if (!id) return;
    try {
      await loadSession(id, 1, false);
    } catch (e) {
      setStatus(`Open failed: ${String(e)}`);
    }
  };

  const uploadBundle = async (file: File) => {
    try {
      setStatus(`Uploading ${file.name}…`);
      const tmp = await apiPost<{ session_id: string }>("/sessions", {
        user_level: "L1",
        tools: [],
        mode: "sim",
      });
      const form = new FormData();
      form.append("file", file, file.name);
      const up = await fetch(`${API_BASE}/sessions/${tmp.session_id}/blobs`, {
        method: "POST",
        body: form,
      });
      if (!up.ok) throw new Error(`blob upload -> ${up.status}: ${await up.text()}`);
      const { blob_ref } = (await up.json()) as { blob_ref: string };
      const r = await apiPost<{ session_id: string; speed: number }>("/replay", {
        bundle_ref: blob_ref,
      });
      await loadSession(r.session_id, r.speed, false);
    } catch (e) {
      setStatus(`Upload failed: ${String(e)}`);
    }
  };

  const buildReport = async () => {
    if (!sessionId) return;
    try {
      setStatus("Building report…");
      const r = await apiPost<{ report_id: string }>(`/sessions/${sessionId}/report`, {});
      setReportId(r.report_id);
      onLatestReport?.(r.report_id);
      setStatus("Report ready.");
      onReport?.(r.report_id);
    } catch (e) {
      setStatus(`Report failed: ${String(e)}`);
    }
  };

  const seeReport = () => {
    if (reportId) onReport?.(reportId);
  };

  const stop = state?.stop_and_see_pro;
  const evidence = useMemo(() => events.filter((e) => e.type === "evidence_added"), [events]);
  const observations = useMemo(
    () => events.filter((e) => e.type === "observation_added"),
    [events],
  );

  const obsByEvidence = useMemo(() => {
    const m = new Map<string, SessionEvent[]>();
    for (const o of observations) {
      const ids = ((o.payload as Record<string, unknown>).evidence_ids as string[] | undefined) ?? [];
      for (const id of ids) {
        const arr = m.get(id) ?? [];
        arr.push(o);
        m.set(id, arr);
      }
    }
    return m;
  }, [observations]);

  const hypLabels = useMemo(() => {
    const m = new Map<string, string>();
    for (const h of state?.hypotheses ?? []) {
      m.set(h.hypothesis_id, h.label[level] ?? h.label.L1);
    }
    return m;
  }, [state, level]);

  const labelCtx = useMemo(
    () => ({ obsByEvidence, hypLabels }),
    [obsByEvidence, hypLabels],
  );

  // Evidence drawer: highlight the newest image/manual/sound item at the playhead.
  const activeEvidenceSeq = useMemo(() => {
    let best = -1;
    for (const e of evidence) {
      if (e.seq > cursor) continue;
      const t = String((e.payload as Record<string, unknown>).type ?? "");
      if (t === "image" || t === "manual_page" || t === "audio") best = e.seq;
    }
    return best;
  }, [evidence, cursor]);

  useEffect(() => {
    if (!drawerOpen || activeEvidenceSeq < 0) return;
    const el = document.getElementById(`ev-${activeEvidenceSeq}`);
    el?.scrollIntoView({ block: "nearest" });
  }, [activeEvidenceSeq, drawerOpen, cursor]);

  const vin = (state?.vehicle as { vin_last6?: string } | null | undefined)?.vin_last6;
  const top = state && state.hypotheses.length > 0 ? state.hypotheses[0] : null;
  const concluded = top != null && top.current >= 0.8;
  const testsTaken = state?.tests_log ?? [];

  return (
    <section>
      <h2 className="visually-hidden">Replay</h2>
      <p className="statusline" aria-live="polite">{status}</p>
      {autoJudge && !sessionId ? (
        <div className="hero">
          <h2 className="hero-title">{JUDGE_HEADLINE}</h2>
          <p className="hero-sub">{JUDGE_HERO_1} {JUDGE_HERO_2}</p>
          <p className="hero-cta">
            <button
              className="primary big"
              onClick={() => {
                const list = orderedFixtures(fixtures ?? []);
                const first = list[0]?.name ?? JUDGE_FIXTURE;
                const origin = list[0]?.origin ?? "sim";
                void playFixture(first, origin);
              }}
            >
              Play the investigation
            </button>
            <button className="ghost" onClick={() => void playFixture(COIL_FIXTURE, "sim")}>
              Play the coil demo instead
            </button>
          </p>
          {fixtures && fixtures.length > 0 ? (
            <ul className="fixture-list">
              {orderedFixtures(fixtures).map((f) => (
                <li key={f.name}>
                  <button className="ghost" onClick={() => void playFixture(f.name, f.origin)}>
                    Play {f.display_label || f.name}
                  </button>{" "}
                  <span className={f.origin === "real" ? "badge real" : "badge sim"}>
                    {f.origin === "real" ? "Real vehicle · real AI outputs" : "Simulated · real AI outputs"}
                  </span>{" "}
                  <small className="t-raw">{f.name}</small>
                </li>
              ))}
            </ul>
          ) : null}
          <ol className="checklist">
            {JUDGE_CHECKLIST.map((item, i) => (
              <li key={item}>
                <span className="step" aria-hidden="true">{i + 1}</span> {item}
              </li>
            ))}
          </ol>
          <p className="hero-note">Full 60-second script: docs/JUDGES.md in the repo.</p>
          <div className="card how-built">
            <h3>How this was built</h3>
            <p>
              Built by Muse Code during the event (muse-wonder-1.0 plans and reviews,
              muse-spark-1.3 builds; prompts and runs are in .agents/).
            </p>
            <p>
              <small>
                Spark 1.3 reasons and reads · SAM 3.1 masks · Voice Transcribe hears
                (Android only) · Wonder reviews once per report · Spark Open via
                Baseten corroborates and falls back — none set probabilities.
              </small>
            </p>
          </div>
        </div>
      ) : null}
      {!autoJudge && !sessionId ? (
        <div className="card loadcard">
          <div className="row">
            <input
              value={typedId}
              onChange={(e) => setTypedId(e.target.value)}
              placeholder="Session id"
              aria-label="Session id"
            />{" "}
            <button onClick={openTyped}>Open session</button>
          </div>
          <div>
            <label>
              or upload a .mmrec bundle:{" "}
              <input
                type="file"
                accept=".mmrec"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void uploadBundle(f);
                }}
              />
            </label>
          </div>
        </div>
      ) : null}

      {sessionId ? (
        <div>
          <div className="statusbar" aria-label="Session status">
            <span className="mark">MMC</span>
            <span>Vehicle {vin ? `····${vin}` : "—"}</span>
            <span className="chip">
              {playedOrigin === "real" ? "Real vehicle · real AI outputs" : "Simulated vehicle · real AI outputs"}
            </span>
            {reportId ? <span className="chip ok">Report ready</span> : null}
            <span className="spacer" />
            <span role="group" aria-label="Experience level">
              {(["L1", "L2", "L3"] as Level[]).map((l) => (
                <button key={l} onClick={() => setLevel(l)} disabled={level === l} aria-pressed={level === l}>
                  {l}
                </button>
              ))}
            </span>
          </div>

          <div className="scrubber card">
            <label>
              <span className="scrub-label">Event seq {cursor}/{maxSeq}</span>
              <input
                type="range"
                min={0}
                max={maxSeq}
                value={cursor}
                onChange={(e) => {
                  setPlaying(false);
                  setCursor(Number(e.target.value));
                }}
                aria-label="Event scrubber"
              />
            </label>{" "}
            <span className="scrub-controls">
              <button onClick={() => setPlaying((p) => !p)}>
                {playing ? "Pause" : "Play"}
              </button>{" "}
              <label>
                speed{" "}
                <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                  {[0.5, 1, 2, 4].map((s) => (
                    <option key={s} value={s}>{s}×</option>
                  ))}
                </select>
              </label>
            </span>
          </div>

          {stop?.banner ? (
            <div role="alert" className="pro-banner">
              <strong>Stop — see a professional.</strong>
              <p>{stop.banner}</p>
              <ul>
                {stop.triggers.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {concluded && top ? (
            <div className="conclusion" role="status">
              <div className="conclusion-main">
                <small className="conclusion-kicker">Most likely</small>
                <p className="conclusion-line">
                  <strong>{top.label[level] ?? top.label.L1}</strong>
                  <span className="pct"> {(top.current * 100).toFixed(0)}%</span>
                </p>
                <p className="conclusion-sub">
                  after {testsTaken.length} test{testsTaken.length === 1 ? "" : "s"}
                  {testsTaken.length > 0
                    ? `: ${testsTaken.map((t) => testTitle(t.test_ref)).join(" · ")}`
                    : ""}
                </p>
              </div>
              <div className="conclusion-cta">
                {reportId ? (
                  <button className="primary" onClick={seeReport}>See the report</button>
                ) : (
                  <button className="primary" onClick={buildReport}>Build report</button>
                )}
              </div>
            </div>
          ) : null}

          {state ? (
            <div className="replay-grid">
              <aside className="card timeline-card" aria-label="Event timeline">
                <h3>Timeline</h3>
                <ol className="timeline">
                  {events.map((e) => {
                    const d = describeEvent(e, labelCtx);
                    return (
                      <li key={e.seq}>
                        <button
                          aria-current={e.seq === cursor}
                          onClick={() => {
                            setPlaying(false);
                            setCursor(e.seq);
                          }}
                        >
                          <span className="glyph" aria-hidden="true">
                            {GLYPH[e.type] ?? "·"}
                          </span>
                          <span className="seq">{e.seq}</span>
                          <span className="t-text">
                            <span className="t-head">{d.headline}</span>
                            {d.detail ? <span className="t-detail"> {d.detail}</span> : null}
                            <span className="t-raw">{e.type}</span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ol>
              </aside>

              <div className="card chart-card">
                <h3>Suspects</h3>
                <HypothesisChart hypotheses={state.hypotheses} level={level} />
              </div>

              <div className="card tests-card" aria-label="Next best tests">
                <h3>Next best tests</h3>
                {state.next_tests.length === 0 ? <p>No runnable tests.</p> : null}
                {state.next_tests.map((t) => (
                  <article key={t.kp_test_ref}>
                    <h4 className="test-title">{testTitle(t.kp_test_ref)}</h4>
                    <p className="t-raw">{t.kp_test_ref} · #{t.rank}</p>
                    <p>{instructions(t, level)}</p>
                    <p>{t.why}</p>
                    <p>
                      <small>
                        EIG {t.eig_bits.toFixed(2)} bits · {t.cost.minutes} min ·{" "}
                        Tools: {t.required_tools.join(", ") || "none"} · Level {t.min_level} ·{" "}
                        {t.safety_gate === "none" ? "no safety gate" : `gate: ${gateLabel(t.safety_gate)}`}
                      </small>
                    </p>
                    {t.safety_gate !== "none" && t.gate_checklist ? (
                      <details>
                        <summary>Safety checklist: {t.gate_checklist.title}</summary>
                        <ul>
                          {t.gate_checklist.items.map((item) => (
                            <li key={item}>
                              <label>
                                <input type="checkbox" disabled /> {item}
                              </label>
                            </li>
                          ))}
                          {level === "L1"
                            ? t.gate_checklist.l1_extra.map((item) => (
                                <li key={item}>
                                  <label>
                                    <input type="checkbox" disabled /> {item}
                                  </label>
                                </li>
                              ))
                            : null}
                        </ul>
                      </details>
                    ) : null}
                    <details>
                      <summary>Expected outcomes</summary>
                      <ul>
                        {t.expected_outcomes.map((o) => (
                          <li key={o.outcome}>
                            {prettyRef(o.outcome)} (p={o.p.toFixed(2)})
                          </li>
                        ))}
                      </ul>
                    </details>
                  </article>
                ))}

                {state.blocked_tests.length > 0 ? (
                  <div>
                    <h3>Blocked tests (not hidden)</h3>
                    <ul>
                      {state.blocked_tests.map((b: BlockedTest) => (
                        <li key={b.kp_test_ref}>
                          <strong>{testTitle(b.kp_test_ref)}</strong>{" "}
                          <small className="t-raw">{b.kp_test_ref}</small> — {b.blocked_reasons.join("; ")}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {state.missing_evidence.length > 0 ? (
                  <div>
                    <h3>Still needed</h3>
                    <ul>
                      {state.missing_evidence.map((m) => (
                        <li key={m}>{m}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <div className="report-cta">
                  {reportId ? (
                    <>
                      <button className="primary" onClick={seeReport}>See the report</button>{" "}
                      <button onClick={buildReport}>Rebuild report</button>
                    </>
                  ) : (
                    <button className="primary" onClick={buildReport}>Build report</button>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p>Loading state…</p>
          )}

          <section className="drawer">
            <h3>
              <button
                onClick={() => setDrawerOpen((o) => !o)}
                aria-expanded={drawerOpen}
              >
                Evidence ({evidence.length}) {drawerOpen ? "▾" : "▸"}
              </button>
            </h3>
            {drawerOpen ? (
              <ul>
                {evidence.map((e) => {
                  const p = e.payload as Record<string, unknown>;
                  const id = String(p.evidence_id ?? e.seq);
                  const etype = String(p.type ?? e.type);
                  const linked = obsByEvidence.get(id) ?? [];
                  const extractors = linked.map((o) =>
                    String((o.payload as Record<string, unknown>).extractor ?? ""),
                  );
                  const ai = extractors.find(
                    (x) => x.startsWith("spark") || x.startsWith("muse-"),
                  );
                  const dsp = extractors.some((x) => x.startsWith("dsp:"));
                  const sim = extractors.some((x) => x.startsWith("sim:"));
                  const agreement = linked
                    .map((o) => String((o.payload as Record<string, unknown>).agreement ?? ""))
                    .find(Boolean);
                  const overlays = (p.overlays as {
                    concept?: string;
                    used_concept?: string;
                    found?: boolean;
                    masks?: OverlayMask[];
                  }[] | undefined) ?? [];
                  const masks = overlays.flatMap((o) => o.masks ?? []);
                  const overlayConcept =
                    overlays[0]?.used_concept ?? overlays[0]?.concept ?? undefined;
                  const { w, h } = overlayExtent(masks);
                  // Keyframe photo served by GET /sessions/{id}/blobs/{sha256},
                  // which only streams blobs this session's log references.
                  const sha = typeof p.content_hash === "string" ? p.content_hash : "";
                  const refTail =
                    typeof p.blob_ref === "string" ? (p.blob_ref.split("/").pop() ?? "") : "";
                  const digest = /^[0-9a-fA-F]{64}$/.test(sha)
                    ? sha.toLowerCase()
                    : /^[0-9a-fA-F]{64}$/.test(refTail)
                      ? refTail.toLowerCase()
                      : "";
                  const imageUrl =
                    etype === "image" && digest
                      ? `${API_BASE}/sessions/${sessionId}/blobs/${digest}`
                      : undefined;
                  const headline = describeEvent(e, labelCtx).headline;
                  const isActive = e.seq === activeEvidenceSeq;
                  const seen = e.seq <= cursor;
                  const compLabels = [...new Set(
                    linked
                      .map((o) => String((o.payload as Record<string, unknown>).feature ?? ""))
                      .filter((f) => f.startsWith("visual_component:") || f.startsWith("visual_anomaly:"))
                      .map((f) => f.replace("visual_component:", "").replace("visual_anomaly:", "").replace(/_/g, " ")),
                  )];
                  const specRanges = (p.spec_ranges as {
                    parameter?: string; min?: number; max?: number; units?: string; page?: number;
                  }[] | undefined) ?? [];
                  return (
                    <li
                      key={e.seq}
                      id={`ev-${e.seq}`}
                      className={isActive ? "active" : seen ? "seen" : "upcoming"}
                    >
                      <p className="ev-head"><strong>{headline}</strong></p>
                      <p>
                        {ai ? <span className="badge ai">AI-extracted: {ai}</span> : null}{" "}
                        {agreement ? <span className="badge ai">{agreement}</span> : null}{" "}
                        {dsp ? <span className="badge dsp">Signal processing, not AI</span> : null}{" "}
                        {etype === "manual_page" ? (
                          <span className="badge manual">
                            manual p.{String(specRanges[0]?.page ?? "?")}
                          </span>
                        ) : null}{" "}
                        {sim ? <span className="badge sim">OBD · sim</span> : null}{" "}
                        <small className="t-raw">{etype} · {id.slice(0, 8)}… · seq {e.seq}</small>
                      </p>
                      {specRanges.length > 0 ? (
                        <table className="specs">
                          <thead>
                            <tr><th>Book check</th><th>Healthy range</th></tr>
                          </thead>
                          <tbody>
                            {specRanges.map((s, i) => (
                              <tr key={i}>
                                <td>{(s.parameter ?? "").replace(/_/g, " ")}</td>
                                <td>{s.min}–{s.max} {s.units ?? ""} (p.{s.page ?? "?"})</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      ) : null}
                      {typeof p.note === "string" ? <p><small>{p.note}</small></p> : null}
                      {overlays.length > 0 ? (
                        <SamOverlay
                          imageUrl={imageUrl}
                          width={w}
                          height={h}
                          masks={masks}
                          labels={compLabels}
                          label={samCaption(
                            [overlayConcept ?? compLabels[0] ?? "candidate"],
                            masks.length,
                            hasScores(masks),
                          )}
                        />
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            ) : null}
          </section>
        </div>
      ) : null}
    </section>
  );
}
