import { useEffect, useState } from "react";
import { API_BASE, apiGet, type Level } from "../api";

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

const fmt = (v: unknown): string =>
  typeof v === "string" ? v : JSON.stringify(v);

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

  return (
    <section>
      <h2>Report</h2>
      <div>
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="Report id"
          aria-label="Report id"
        />{" "}
        <button onClick={() => setId(typed.trim())}>Open report</button>{" "}
        {id ? (
          <span>
            <a href={`${API_BASE}/reports/${id}.html`}>HTML</a> ·{" "}
            <a href={`${API_BASE}/reports/${id}.pdf`}>PDF</a>
          </span>
        ) : null}
      </div>
      {status ? <p>{status}</p> : null}
      {!report ? (
        <p>No report loaded.</p>
      ) : (
        <div>
          <p>{report.conclusion}</p>
          <div role="group" aria-label="Summary level">
            {(["L1", "L2", "L3"] as Level[]).map((l) => (
              <button key={l} onClick={() => setLevel(l)} disabled={level === l}>
                {l}
              </button>
            ))}
          </div>
          <p>{report.summary_by_level[level]}</p>

          <h3>Hypotheses</h3>
          <table>
            <thead>
              <tr>
                <th>Suspect</th>
                <th>Posterior</th>
              </tr>
            </thead>
            <tbody>
              {report.hypotheses_final.map((h) => (
                <tr key={h.hypothesis_id}>
                  <td>
                    {h.label[level] ?? h.label.L1} ({h.hypothesis_id})
                  </td>
                  <td>{h.current.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Explanation trace</h3>
          {report.hypotheses_final.map((h) => (
            <details key={h.hypothesis_id}>
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
                        <td>{t.test_id}</td>
                        <td>{t.expected}</td>
                        <td>{t.measured}</td>
                        <td>{t.likelihood_ratio.toFixed(2)}</td>
                        <td>{t.lr_source}</td>
                        <td>{level === "L1" ? t.text_L1 : level === "L2" ? t.text_L2 : t.text_L3}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </details>
          ))}

          <h3>Expected vs measured</h3>
          <table>
            <thead>
              <tr>
                <th>Test</th>
                <th>Expected</th>
                <th>Measured</th>
                <th>Outcome</th>
                <th>Evaluator</th>
                <th>How judged</th>
              </tr>
            </thead>
            <tbody>
              {report.expected_vs_measured.map((row, i) => (
                <tr key={i}>
                  <td>{row.test_ref}</td>
                  <td>{fmt(row.expected)}</td>
                  <td>{fmt(row.measured)}</td>
                  <td>{row.outcome}</td>
                  <td>{row.evaluator}</td>
                  <td>
                    {row.origin === "signal_processing"
                      ? "Signal processing (DSP), not AI"
                      : row.origin === "direct_observation"
                        ? "Direct observation"
                        : `Deterministic (${row.evaluator})`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>Evidence</h3>
          <ul>
            {report.evidence.map((ev) => (
              <li key={ev.evidence_id}>
                {ev.type}{" "}
                {ev.ai_extracted ? (
                  <span className="badge ai">
                    AI-extracted{ev.extractor ? `: ${ev.extractor}` : ""}
                  </span>
                ) : (
                  <span className="badge sim">{ev.extractor ?? "manual"}</span>
                )}{" "}
                {ev.agreement ? <span className="badge ai">{ev.agreement}</span> : null}
              </li>
            ))}
          </ul>
          {report.fallback_used ? <p>Second opinion / fallback used (Spark Open).</p> : null}

          <h3>Safety notes</h3>
          <ul>
            {report.safety_notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>

          <h3>AI review — Muse Wonder (does not change the diagnosis)</h3>
          {report.wonder_review ? (
            <div>
              <p>{report.wonder_review.narrative}</p>
              <ul>
                {report.wonder_review.gaps.map((g, i) => (
                  <li key={i}>
                    {g.issue}
                    {g.test_ref ? ` — suggested test: ${g.test_ref}` : ""}
                    {g.evidence_id ? ` — evidence: ${g.evidence_id}` : ""}
                  </li>
                ))}
              </ul>
              <p>Agrees with leading suspect: {String(report.wonder_review.agrees_with_top)}</p>
            </div>
          ) : (
            <p>AI review unavailable{report.wonder_unavailable ? `: ${report.wonder_unavailable}` : ""}.</p>
          )}

          <h3>Reproducibility</h3>
          <p>
            engine {report.reproducibility.engine_version} · KP {report.reproducibility.kp_version} ·{" "}
            prompts {report.reproducibility.prompts_sha256.slice(0, 12)}… · log{" "}
            {report.reproducibility.log_head_hash.slice(0, 12)}… · models{" "}
            {Object.values(report.reproducibility.model_pins).join(", ")}
          </p>
          {report.vin_last6 ? <p>Vehicle ····{report.vin_last6}</p> : null}
        </div>
      )}
    </section>
  );
}
