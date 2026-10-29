import type { Hypothesis } from "../api";

interface Props {
  hypotheses: Hypothesis[];
}

/** Posterior trajectories over event seq (SVG only, no new dependency). */
export default function HypothesisChart({ hypotheses }: Props) {
  const W = 640;
  const H = 220;
  const PAD = 28;
  const top = hypotheses.slice(0, 5);
  const maxSeq = Math.max(
    1,
    ...top.flatMap((h) => [0, ...h.history.map((p) => p.seq)]),
  );
  const x = (seq: number) => PAD + (seq / maxSeq) * (W - PAD * 2);
  const y = (p: number) => H - PAD - Math.min(1, Math.max(0, p)) * (H - PAD * 2);
  const palette = ["#1a5fb4", "#c64600", "#1e7b34", "#813d9c", "#7a5900"];

  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Hypothesis posteriors over events">
        {[0, 0.5, 1].map((p) => (
          <g key={p}>
            <line x1={PAD} x2={W - PAD} y1={y(p)} y2={y(p)} stroke="#ccc" />
            <text x={2} y={y(p) + 4} fontSize={10}>{p.toFixed(1)}</text>
          </g>
        ))}
        {top.map((h, i) => {
          const pts = [{ seq: 0, p: h.prior }, ...h.history].map(
            (p) => `${x(p.seq).toFixed(1)},${y(p.p).toFixed(1)}`,
          );
          return (
            <polyline
              key={h.hypothesis_id}
              points={pts.join(" ")}
              fill="none"
              stroke={palette[i % palette.length]}
              strokeWidth={2}
            />
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
    </figure>
  );
}
