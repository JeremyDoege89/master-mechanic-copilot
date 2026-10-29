import { useCallback, useEffect, useRef, useState } from "react";
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

interface Props {
  autoJudge?: boolean;
  onReport?: (reportId: string) => void;
}

const JUDGE_FIXTURE = "sim-healthy.mmrec";

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

export default function Replay({ autoJudge, onReport }: Props) {
  const [sessionId, setSessionId] = useState("");
  const [typedId, setTypedId] = useState("");
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [cursor, setCursor] = useState(0);
  const [maxSeq, setMaxSeq] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [level, setLevel] = useState<Level>("L1");
  const [state, setState] = useState<SessionState | null>(null);
  const [status, setStatus] = useState(
    autoJudge ? "Loading judge demo…" : "Load a session to begin.",
  );
  const [reportId, setReportId] = useState<string | null>(null);
  const judged = useRef(false);

  const loadSession = useCallback(async (id: string, replaySpeed: number) => {
    setSessionId(id);
    setSpeed(replaySpeed);
    const ev = await apiGet<{ events: SessionEvent[] }>(`/sessions/${id}/events`);
    setEvents(ev.events);
    const max = ev.events.length > 0 ? ev.events[ev.events.length - 1].seq : 0;
    setMaxSeq(max);
    setCursor(max);
    setReportId(null);
    setStatus(`Session ${id.slice(0, 8)}… loaded (${ev.events.length} events).`);
  }, []);

  useEffect(() => {
    if (autoJudge && !judged.current) {
      judged.current = true;
      apiPost<{ session_id: string; speed: number }>("/replay", {
        fixture: JUDGE_FIXTURE,
      })
        .then((r) => loadSession(r.session_id, r.speed))
        .catch((e) => setStatus(`Judge demo failed: ${String(e)}`));
    }
  }, [autoJudge, loadSession]);

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
      await loadSession(id, 1);
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
      await loadSession(r.session_id, r.speed);
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

  return (
    <section>
      <h2>Replay</h2>
      <p>{status}</p>
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

          <div role="group" aria-label="Experience level">
            {(["L1", "L2", "L3"] as Level[]).map((l) => (
              <button key={l} onClick={() => setLevel(l)} disabled={level === l}>
                {l}
              </button>
            ))}
          </div>

          {stop?.banner ? (
            <div role="alert">
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
            <div>
              <h3>Suspects</h3>
              <HypothesisChart hypotheses={state.hypotheses} />

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
                    Tools: {t.required_tools.join(", ") || "none"} · Level {t.min_level} ·{" "}
                    {t.safety_gate === "none" ? "no safety gate" : `gate: ${t.safety_gate}`}
                  </p>
                  {t.safety_gate !== "none" && t.gate_checklist ? (
                    <details>
                      <summary>Safety checklist: {t.gate_checklist.title}</summary>
                      <ul>
                        {t.gate_checklist.items.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                        {level === "L1"
                          ? t.gate_checklist.l1_extra.map((item) => <li key={item}>{item}</li>)
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

              <h3>Evidence</h3>
              {evidence.length === 0 ? <p>No evidence yet.</p> : null}
              <ul>
                {evidence.map((e) => {
                  const id = String(e.payload.evidence_id ?? e.seq);
                  const linked = observations.filter((o) =>
                    ((o.payload.evidence_ids as string[] | undefined) ?? []).includes(id),
                  );
                  const ai = linked.some((o) =>
                    String(o.payload.extractor ?? "").startsWith("spark") ||
                    String(o.payload.extractor ?? "").startsWith("muse-"),
                  );
                  return (
                    <li key={e.seq}>
                      {String(e.payload.type ?? e.type)}{" "}
                      {ai ? <span>[AI-extracted]</span> : null}{" "}
                      <small>{id.slice(0, 8)}…</small>
                    </li>
                  );
                })}
              </ul>

              <div>
                <button onClick={buildReport}>Build report</button>{" "}
                {reportId ? <span>Report: {reportId}</span> : null}
              </div>
            </div>
          ) : (
            <p>Loading state…</p>
          )}
        </div>
      ) : null}
    </section>
  );
}
