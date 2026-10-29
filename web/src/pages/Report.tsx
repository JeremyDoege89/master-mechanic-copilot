import { useEffect, useState } from "react";
import { API_BASE, apiGet, type Level } from "../api";
import { formatReportExpected, formatReportMeasured, rawText, testTitle } from "../labels";

interface Props {
  reportId?: string;
}

interface Report {
  report_id: string;
  session_id: string;
  conclusion: string;
  summary_by_level: Record<Level, string>;
  hypotheses_final: {
    hypothesis_id: string;
    label: { L1: string; L2: string; L3: string };
    prior: number;
    current: number;
    trace: {
      seq: number;
      test_id: string | null;
      expected: string;
      measured: string;
      likelihood_ratio: number;
      lr_source: string;
      text_L1: string;
      text_L2: string;
      text_L3: string;
    }[];
  }[];
  tests: { test_ref: string; outcome: string }[];
  expected_vs_measured: {
    test_ref: string;
    expected: unknown;
    measured: unknown;
    outcome: string;
    evaluator: string;
    origin: string;
  }[];
  evidence: {
    evidence_id: string;
    type: string;
    ai_extracted: boolean;
    extractor: string | null;
    agreement: string | null;
  }[];
  fallback_used: boolean;
  safety_notes: string[];
  stop_triggers?: string[];
  vin_last6: string | null;
  wonder_review: {
    narrative: string;
    gaps: { issue: string; test_ref?: string; evidence_id?: string; rationale?: string }[];
    agrees_with_top: boolean;
  } | null;
  wonder_unavailable: string | null;
  reproducibility: {
    engine_version: string;
    kp_version: string;
    log_head_hash: string;
    model_pins: Record<string, string>;
    prompts_sha256: string;
  };
}

function sourceBadge(row: Report["expected_vs_measured"][number]): { cls: string; text: string } {
  if (row.origin === "signal_processing") {
    return { cls: "badge dsp", text: "DSP · signal, not AI" };
  }
  const src = (row.expected as { source?: { kind?: string; ref?: string } } | undefined)?.source;
  const kind = src?.kind ?? "";
  if (kind === "manual_page") return { cls: "badge manual", text: "manual" };
  if (kind === "kp") return { cls: "badge kp", text: "knowledge pack" };
  if (kind === "physics") return { cls: "badge phys", text: "physics" };
  if (kind === "mode06_limit") return { cls: "badge sim", text: "on-board limits" };
  if (row.origin === "direct_observation") return { cls: "badge sim", text: "direct look" };
  return { cls: "badge", text: `deterministic · ${row.evaluator}` };
}

export default function Report({ reportId }: Props) {
  const [typed, setTyped] = useState(reportId ?? "");
  const [id, setId] = useState(reportId ?? "");
  const [report, setReport] = useState<Report | null>(null);
  const [level, setLevel] = useState<Level>("L1");
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (reportId) {
      setTyped(reportId);
      setId(reportId);
    } else {
      setReport(null);
      setId("");
      setTyped("");
    }
  }, [reportId]);

  useEffect(() => {
    if (!id) return;
    setStatus("Loading report…");
    apiGet<Report>(`/reports/${id}.json`)
      .then((r) => {
        setReport(r);
        setStatus("");
      })
      .catch((e) => setStatus(`Report failed: ${String(e)}`));
  }, [id]);

  const top = report && report.hypotheses_final.length > 0 ? report.hypotheses_final[0] : null;

  return (
    <section className="report">
      <div className="report-head">
        <div>
          <h2>Report</h2>
          {top ? (
            <p className="conclusion-line">
              <strong>{top.label[level] ?? top.label.L1}</strong>
              <span className="pct"> {(top.current * 100).toFixed(0)}%</span>
            </p>
          ) : null}
          {report ? <p className="report-sub">{report.conclusion}</p> : null}
        </div>
        <div role="group" aria-label="Summary level" className="level-switch">
          {(["L1", "L2", "L3"] as Level[]).map((l) => (
            <button key={l} onClick={() => setLevel(l)} disabled={level === l} aria-pressed={level === l}>
              {l === "L1" ? "Driver" : l === "L2" ? "DIY" : "Tech"}
            </button>
          ))}
        </div>
      </div>
      <div className="report-tools">
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="Report id"
          aria-label="Report id"
        />{" "}
        <button onClick={() => setId(typed.trim())}>Open report</button>{" "}
        {id ? (
          <span>
            <a href={`${API_BASE}/reports/${id}.html`}>Printable HTML</a> ·{" "}
            <a href={`${API_BASE}/reports/${id}.pdf`}>PDF</a> ·{" "}
            <button className="linklike" onClick={() => window.print()}>Print this page</button>
          </span>
        ) : null}
      </div>
      {status ? <p>{status}</p> : null}
      {!report ? (
        <div className="card">
          {reportId || id ? (
            <p>Loading…</p>
          ) : (
            <>
              <p><strong>No report loaded.</strong></p>
              <p>
                Load a session in Replay first — its latest report opens here
                automatically. Or press <strong>Build report</strong> on the
                session card and you will land here in one click.
              </p>
            </>
          )}
        </div>
      ) : (
        <div>
          <div className="card summary-card">
            <h3>Summary ({level === "L1" ? "driver" : level === "L2" ? "DIY" : "technician"})</h3>
            <p className="summary">{report.summary_by_level[level]}</p>
          </div>

          <div className="card">
            <h3>Suspects</h3>
            <table>
              <thead>
                <tr>
                  <th>Suspect</th>
                  <th>Posterior</th>
                </tr>
              </thead>
              <tbody>
                {report.hypotheses_final.map((h) => (
                  <tr key={h.hypothesis_id} className={h === top ? "top-row" : undefined}>
                    <td>
                      <strong>{h.label[level] ?? h.label.L1}</strong>{" "}
                      <small className="t-raw">{h.hypothesis_id}</small>
                    </td>
                    <td className="bignum">{(h.current * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3>Explanation trace</h3>
            {report.hypotheses_final.map((h) => (
              <details key={h.hypothesis_id} open={h === top}>
                <summary>
                  {h.label[level] ?? h.label.L1} ({h.trace.length} updates)
                </summary>
                {h.trace.length === 0 ? (
                  <p>No updates yet.</p>
                ) : (
                  <table>
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Test</th>
                        <th>Expected</th>
                        <th>Measured</th>
                        <th>LR</th>
                        <th>Source</th>
                        <th>Meaning</th>
                      </tr>
                    </thead>
                    <tbody>
                      {h.trace.map((t, i) => (
                        <tr key={i}>
                          <td>{t.seq}</td>
                          <td>{t.test_id ? testTitle(t.test_id) : "—"} <small className="t-raw">{t.test_id}</small></td>
                          <td>{formatReportExpected(t.expected)}</td>
                          <td>{formatReportMeasured(t.test_id, t.measured)}{level === "L3" ? (
                            <details className="raw"><summary>raw</summary><code>{rawText(t.measured)}</code></details>
                          ) : null}</td>
                          <td>×{t.likelihood_ratio.toFixed(2)}</td>
                          <td><span className="badge">{t.lr_source}</span></td>
                          <td>{level === "L1" ? t.text_L1 : level === "L2" ? t.text_L2 : t.text_L3}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </details>
            ))}
          </div>

          <div className="card">
            <h3>Expected vs measured</h3>
            <table>
              <thead>
                <tr>
                  <th>Test</th>
                  <th>Expected</th>
                  <th>Measured</th>
                  <th>Outcome</th>
                  <th>How judged</th>
                </tr>
              </thead>
              <tbody>
                {report.expected_vs_measured.map((row, i) => {
                  const b = sourceBadge(row);
                  const raw = level === "L3" ? (
                    (v: unknown) => (
                      <details className="raw"><summary>raw</summary><code>{rawText(v)}</code></details>
                    )
                  ) : (_v: unknown) => null;
                  return (
                    <tr key={i}>
                      <td><strong>{testTitle(row.test_ref)}</strong> <small className="t-raw">{row.test_ref}</small></td>
                      <td>{formatReportExpected(row.expected)}{raw(row.expected)}</td>
                      <td>{formatReportMeasured(row.test_ref, row.measured)}{raw(row.measured)}</td>
                      <td>{row.outcome}</td>
                      <td>
                        <span className={b.cls}>{b.text}</span>{" "}
                        <small className="t-raw">{row.evaluator}</small>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="card">
            <h3>Evidence</h3>
            <ul className="ev-list">
              {report.evidence.map((ev) => (
                <li key={ev.evidence_id}>
                  <strong>{ev.type.replace(/_/g, " ")}</strong>{" "}
                  <small className="t-raw">{ev.evidence_id.slice(0, 8)}…</small>{" "}
                  {ev.ai_extracted ? (
                    <span className="badge ai">
                      AI{ev.extractor ? `: ${ev.extractor}` : ""}
                    </span>
                  ) : (
                    <span className="badge sim">{ev.extractor ?? "manual"}</span>
                  )}{" "}
                  {ev.agreement ? <span className="badge ai">{ev.agreement}</span> : null}
                </li>
              ))}
            </ul>
            {report.fallback_used ? <p>Second opinion / fallback used (Spark Open).</p> : null}
          </div>

          {report.safety_notes.length > 0 ? (
            <div className="card">
              <h3>Safety notes</h3>
              <ul>
                {report.safety_notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="card">
            <h3>AI review — Muse Wonder (does not change the diagnosis)</h3>
            {report.wonder_review ? (
              <div>
                <p>{report.wonder_review.narrative}</p>
                <ul>
                  {report.wonder_review.gaps.map((g, i) => (
                    <li key={i}>
                      {g.issue}
                      {g.test_ref ? ` — suggested test: ${testTitle(g.test_ref)} (${g.test_ref})` : ""}
                      {g.evidence_id ? ` — evidence: ${g.evidence_id.slice(0, 8)}…` : ""}
                    </li>
                  ))}
                </ul>
                <p>Agrees with leading suspect: {String(report.wonder_review.agrees_with_top)}</p>
              </div>
            ) : (
              <p>AI review unavailable{report.wonder_unavailable ? `: ${report.wonder_unavailable}` : ""}.</p>
            )}
          </div>

          <div className="card repro">
            <h3>Reproducibility</h3>
            <p>
              engine <code>{report.reproducibility.engine_version}</code> · KP{" "}
              <code>{report.reproducibility.kp_version}</code> · prompts{" "}
              <code>{report.reproducibility.prompts_sha256.slice(0, 12)}…</code> · log{" "}
              <code>{report.reproducibility.log_head_hash.slice(0, 12)}…</code> · models{" "}
              <code>{Object.values(report.reproducibility.model_pins).join(", ")}</code>
            </p>
            <p className="t-raw">
              report {report.report_id} · session {report.session_id.slice(0, 8)}…
              {report.vin_last6 ? ` · vehicle ····${report.vin_last6}` : ""}
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
