import { useState } from "react";
import type { Hypothesis, Level, LevelTrace } from "../api";

interface Props {
  hypotheses: Hypothesis[];
  level: Level;
}

/** Posterior trajectories over event seq (SVG only, no new dependency).
 * Shaded p ≥ 0.8 "concluded" band plus the 0.5 line; clicking a point
 * selects that trace entry (expected vs measured, LR + source, L text). */
export default function HypothesisChart({ hypotheses, level }: Props) {
  const W = 640;
  const H = 220;
  const PAD = 28;
  const top = hypotheses.slice(0, 5);
  const [sel, setSel] = useState<{ hyp: string; seq: number } | null>(null);
  const maxSeq = Math.max(
    1,
    ...top.flatMap((h) => [0, ...h.history.map((p) => p.seq)]),
  );
  const x = (seq: number) => PAD + (seq / maxSeq) * (W - PAD * 2);
  const y = (p: number) => H - PAD - Math.min(1, Math.max(0, p)) * (H - PAD * 2);
  const palette = ["#1a5fb4", "#c64600", "#1e7b34", "#813d9c", "#7a5900"];

  const textOf = (t: LevelTrace): string =>
    level === "L1" ? t.text_L1 : level === "L2" ? t.text_L2 : t.text_L3;

  const selected = sel
    ? top
        .find((h) => h.hypothesis_id === sel.hyp)
        ?.trace.find((t) => t.seq === sel.seq) ?? null
    : null;

  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Hypothesis posteriors over events">
        <rect x={PAD} y={y(1)} width={W - PAD * 2} height={y(0.8) - y(1)} fill="var(--band)" />
        {[0, 0.5, 1].map((p) => (
          <g key={p}>
            <line x1={PAD} x2={W - PAD} y1={y(p)} y2={y(p)} stroke="#ccc" />
            <text x={2} y={y(p) + 4} fontSize={10}>{p.toFixed(1)}</text>
          </g>
        ))}
        <text x={W - PAD} y={y(0.9)} fontSize={10} textAnchor="end">concluded ≥ 0.8</text>
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
                strokeWidth={2}
              />
              {h.history.map((p) => (
                <circle
                  key={p.seq}
                  cx={x(p.seq)}
                  cy={y(p.p)}
                  r={sel?.hyp === h.hypothesis_id && sel?.seq === p.seq ? 5 : 3}
                  fill={palette[i % palette.length]}
                  stroke="var(--card)"
                  strokeWidth={1}
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
      </svg>
      <figcaption>
        {top.map((h, i) => (
          <span key={h.hypothesis_id} style={{ marginRight: 12 }}>
            <span style={{ color: palette[i % palette.length] }}>■</span>{" "}
            {h.label.L1} ({h.current.toFixed(2)})
          </span>
        ))}
      </figcaption>
      {selected ? (
        <div className="card" aria-live="polite">
          <p>
            <strong>{selected.test_id ?? "update"}</strong> · expected {selected.expected} ·
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
