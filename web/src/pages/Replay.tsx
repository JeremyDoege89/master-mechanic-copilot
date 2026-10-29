import { useCallback, useEffect, useState } from "react";
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
import HypothesisChart from "../components/HypothesisChart";
import SamOverlay from "../components/SamOverlay";

interface Props {
  autoJudge?: boolean;
  onReport?: (reportId: string) => void;
}

const JUDGE_FIXTURE = "sim-vacuum-leak.mmrec";
const COIL_FIXTURE = "sim-coil-unplugged.mmrec";

const JUDGE_HERO_1 =
  "This is a recorded-style investigation of a simulated Ford with a vacuum leak.";
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

function shortLabel(e: SessionEvent): string {
  const p = e.payload as Record<string, unknown>;
  const ref =
    (p.test_ref as string | undefined) ??
    (p.test_id as string | undefined) ??
    (p.gate as string | undefined) ??
    (p.evidence_id as string | undefined)?.slice(0, 8) ??
    "";
  return ref ? `${e.type} ${ref}` : e.type;
}

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
  box?: { x1: number; y1: number; x2: number; y2: number };
}

function overlayExtent(masks: OverlayMask[]): { w: number; h: number } {
  let w = 640;
  let h = 480;
  for (const m of masks) {
    if (m.box) {
      w = Math.max(w, m.box.x2 + 4);
      h = Math.max(h, m.box.y2 + 4);
    }
  }
  return { w, h };
}

export default function Replay({ autoJudge, onReport }: Props) {
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
  const [drawerOpen, setDrawerOpen] = useState(false);

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
      setReportId(null);
      setStatus(`Session ${id.slice(0, 8)}… loaded (${ev.events.length} events).`);
    },
    [],
  );

  const playFixture = useCallback(
    async (fixture: string) => {
      try {
        setStatus("Loading demo…");
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
    [loadSession],
  );

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
      setStatus("Report ready.");
      onReport?.(r.report_id);
    } catch (e) {
      setStatus(`Report failed: ${String(e)}`);
    }
  };

  const stop = state?.stop_and_see_pro;
  const evidence = events.filter((e) => e.type === "evidence_added");
  const observations = events.filter((e) => e.type === "observation_added");
  const vin = (state?.vehicle as { vin_last6?: string } | null | undefined)?.vin_last6;

  return (
    <section>
      <h2>Replay</h2>
      <p>{status}</p>
      {autoJudge && !sessionId ? (
        <div className="hero">
          <p>{JUDGE_HERO_1}</p>
          <p>{JUDGE_HERO_2}</p>
          <p>
            <button className="primary" onClick={() => void playFixture(JUDGE_FIXTURE)}>
              Play the investigation
            </button>
          </p>
          <ol>
            {JUDGE_CHECKLIST.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
          <p className="quiet-links">
            <button onClick={() => void playFixture(COIL_FIXTURE)}>
              Play the coil demo instead
            </button>
            <button onClick={buildReport} disabled={!sessionId}>
              Report
            </button>
            <span>Full 60-second script: docs/JUDGES.md in the repo.</span>
          </p>
        </div>
      ) : null}
      {!autoJudge ? (
        <div>
          <div>
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
            <span className="chip">Simulated vehicle · real AI outputs</span>
            <span className="spacer" />
            <span role="group" aria-label="Experience level">
              {(["L1", "L2", "L3"] as Level[]).map((l) => (
                <button key={l} onClick={() => setLevel(l)} disabled={level === l}>
                  {l}
                </button>
              ))}
            </span>
          </div>

          <div>
            <label>
              Event seq {cursor}/{maxSeq}{" "}
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

          {state ? (
            <div className="replay-grid">
              <aside className="card" aria-label="Event timeline">
                <h3>Timeline</h3>
                <ol className="timeline">
                  {events.map((e) => (
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
                        <span>{shortLabel(e)}</span>
                      </button>
                    </li>
                  ))}
                </ol>
              </aside>

              <div className="card">
                <h3>Suspects</h3>
                <HypothesisChart hypotheses={state.hypotheses} level={level} />
              </div>

              <div className="card" aria-label="Next best tests">
                <h3>Next best tests</h3>
                {state.next_tests.length === 0 ? <p>No runnable tests.</p> : null}
                {state.next_tests.map((t) => (
                  <article key={t.kp_test_ref}>
                    <h4>
                      #{t.rank} {t.kp_test_ref}
                    </h4>
                    <p>{instructions(t, level)}</p>
                    <p>{t.why}</p>
                    <p>
                      <small>
                        EIG {t.eig_bits.toFixed(2)} bits · {t.cost.minutes} min ·{" "}
                        Tools: {t.required_tools.join(", ") || "none"} · Level {t.min_level} ·{" "}
                        {t.safety_gate === "none" ? "no safety gate" : `gate: ${t.safety_gate}`}
                      </small>
                    </p>
                    {t.safety_gate !== "none" && t.gate_checklist ? (
                      <details open>
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
                            {o.outcome} (p={o.p.toFixed(2)})
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
                          <strong>{b.kp_test_ref}</strong> — {b.blocked_reasons.join("; ")}
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

                <div>
                  <button onClick={buildReport}>Build report</button>{" "}
                  {reportId ? <span>Report: {reportId}</span> : null}
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
                  const linked = observations.filter((o) =>
                    ((o.payload.evidence_ids as string[] | undefined) ?? []).includes(id),
                  );
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
                  const { w, h } = overlayExtent(masks);
                  return (
                    <li key={e.seq}>
                      <strong>{etype}</strong>{" "}
                      {ai ? <span className="badge ai">AI-extracted: {ai}</span> : null}{" "}
                      {agreement ? <span className="badge ai">{agreement}</span> : null}{" "}
                      {dsp ? <span className="badge dsp">Signal processing, not AI</span> : null}{" "}
                      {etype === "manual_page" ? (
                        <span className="badge manual">
                          manual p.{String((p.spec_ranges as { page?: number }[] | undefined)?.[0]?.page ?? "?")}
                        </span>
                      ) : null}{" "}
                      {sim ? <span className="badge sim">OBD · sim</span> : null}{" "}
                      <small>{id.slice(0, 8)}…</small>
                      {overlays.length > 0 ? (
                        <SamOverlay
                          width={w}
                          height={h}
                          masks={masks}
                          label={`Recorded overlay (${masks.length} masks; photo bytes are not bundled)`}
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
