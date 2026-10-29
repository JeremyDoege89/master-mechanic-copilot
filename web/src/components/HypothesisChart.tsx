import { useState } from "react";
import type { Hypothesis, Level, LevelTrace } from "../api";

interface Props {
  hypotheses: Hypothesis[];
  level: Level;
}

/** Posterior trajectories over event seq (SVG only, no new dependency).
 * Taller plot with readable labels; the first 0.8 crossing of the leader
 * is marked with a dashed line plus a ring. Clicking a point selects that
 * trace entry (expected vs measured, LR + source, L text). */
export default function HypothesisChart({ hypotheses, level }: Props) {
  const W = 640;
  const H = 300;
  const PAD_L = 40;
  const PAD_R = 16;
  const PAD_T = 14;
  const PAD_B = 30;
  const top = hypotheses.slice(0, 5);
  const [sel, setSel] = useState<{ hyp: string; seq: number } | null>(null);
  const maxSeq = Math.max(
    1,
    ...top.flatMap((h) => [0, ...h.history.map((p) => p.seq)]),
  );
  const x = (seq: number) => PAD_L + (seq / maxSeq) * (W - PAD_L - PAD_R);
  const y = (p: number) => H - PAD_B - Math.min(1, Math.max(0, p)) * (H - PAD_T - PAD_B);
  const palette = ["#d9480f", "#1c7ed6", "#2b8a3e", "#7048e8", "#a67900"];

  const textOf = (t: LevelTrace): string =>
    level === "L1" ? t.text_L1 : level === "L2" ? t.text_L2 : t.text_L3;

  const selected = sel
    ? top
        .find((h) => h.hypothesis_id === sel.hyp)
        ?.trace.find((t) => t.seq === sel.seq) ?? null
    : null;

  // First seq where the current leader crosses 0.8 (from its history).
  const leader = top[0];
  const crossSeq = leader
    ? [{ seq: 0, p: leader.prior }, ...leader.history].find((p) => p.p >= 0.8)?.seq ?? null
    : null;

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Hypothesis posteriors over events">
        <rect x={PAD_L} y={y(1)} width={W - PAD_L - PAD_R} height={y(0.8) - y(1)} fill="var(--band)" />
        {[0, 0.25, 0.5, 0.75, 1].map((p) => (
          <g key={p}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(p)} y2={y(p)} stroke="var(--line)" strokeWidth={p === 0.8 ? 0 : 1} />
            <text x={PAD_L - 6} y={y(p) + 4} fontSize={12} textAnchor="end" fill="var(--muted)">{p.toFixed(2)}</text>
          </g>
        ))}
        <line x1={PAD_L} x2={W - PAD_R} y1={y(0.8)} y2={y(0.8)} stroke="var(--ok)" strokeWidth={1.5} strokeDasharray="6 4" />
        <text x={W - PAD_R} y={y(0.8) - 6} fontSize={12} textAnchor="end" fill="var(--ok)">concluded ≥ 0.80</text>
        {crossSeq !== null && crossSeq > 0 ? (
          <g>
            <line x1={x(crossSeq)} x2={x(crossSeq)} y1={PAD_T} y2={H - PAD_B} stroke="var(--ok)" strokeWidth={1.5} strokeDasharray="3 3" />
            <circle cx={x(crossSeq)} cy={y(0.8)} r={7} fill="none" stroke="var(--ok)" strokeWidth={2.5} />
            <text x={Math.min(x(crossSeq) + 10, W - PAD_R - 90)} y={y(0.8) - 10} fontSize={12} fill="var(--ok)">
              crosses 0.8 @ {crossSeq}
            </text>
          </g>
        ) : null}
        {top.map((h, i) => {
          const pts = [{ seq: 0, p: h.prior }, ...h.history].map(
            (p) => `${x(p.seq).toFixed(1)},${y(p.p).toFixed(1)}`,
          );
          return (
            <g key={h.hypothesis_id}>
              <polyline
                points={pts.join(" ")}
                fill="none"
                stroke={palette[i % palette.length]}
                strokeWidth={i === 0 ? 3 : 2}
              />
              {h.history.map((p) => (
                <circle
                  key={p.seq}
                  cx={x(p.seq)}
                  cy={y(p.p)}
                  r={sel?.hyp === h.hypothesis_id && sel?.seq === p.seq ? 6 : 3.5}
                  fill={palette[i % palette.length]}
                  stroke="var(--card)"
                  strokeWidth={1.5}
                  style={{ cursor: "pointer" }}
                  onClick={() =>
                    setSel({ hyp: h.hypothesis_id, seq: p.seq })
                  }
                >
                  <title>{`${h.label.L1}: p=${p.p.toFixed(2)}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
        <text x={(PAD_L + W - PAD_R) / 2} y={H - 8} fontSize={12} textAnchor="middle" fill="var(--muted)">event seq →</text>
      </svg>
      <figcaption className="legend">
        {top.map((h, i) => (
          <span key={h.hypothesis_id} className={i === 0 ? "legend-top" : undefined}>
            <span className="swatch" style={{ background: palette[i % palette.length] }} aria-hidden="true" />{" "}
            {h.label[level] ?? h.label.L1} <strong>{(h.current * 100).toFixed(0)}%</strong>
          </span>
        ))}
      </figcaption>
      {selected ? (
        <div className="trace-detail" aria-live="polite">
          <p>
            <strong>{selected.test_id ?? "update"}</strong> · expected {selected.expected} ·{" "}
            measured {selected.measured}
          </p>
          <p>
            <small>
              LR {selected.likelihood_ratio.toFixed(2)} ({selected.lr_source}) ·{" "}
              {textOf(selected)}
            </small>
          </p>
        </div>
      ) : (
        <p><small>Select a point to see what moved it.</small></p>
      )}
    </figure>
  );
}
