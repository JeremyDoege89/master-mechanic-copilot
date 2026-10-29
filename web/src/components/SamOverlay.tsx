interface Mask {
  concept?: string;
  used_concept?: string;
  found?: boolean;
  svg_path?: string;
  box?: { x1: number; y1: number; x2: number; y2: number };
}

interface Props {
  imageUrl?: string;
  width: number;
  height: number;
  masks: Mask[];
  label?: string;
}

/** SAM overlay viewer: keyframe image plus SVG mask paths/boxes. */
export default function SamOverlay({ imageUrl, width, height, masks, label }: Props) {
  const found = masks.filter((m) => m.found !== false && (m.svg_path || m.box));
  return (
    <figure>
      {label ? <figcaption>{label}</figcaption> : null}
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label="SAM overlays">
        {imageUrl ? <image href={imageUrl} x={0} y={0} width={width} height={height} /> : null}
        {found.map((m, i) => (
          <g key={i}>
            {m.svg_path ? (
              <path d={m.svg_path} fill="rgba(26,95,180,0.25)" stroke="#1a5fb4" strokeWidth={2} />
            ) : null}
            {m.box ? (
              <rect
                x={m.box.x1}
                y={m.box.y1}
                width={Math.max(1, m.box.x2 - m.box.x1)}
                height={Math.max(1, m.box.y2 - m.box.y1)}
                fill="none"
                stroke="#c64600"
                strokeWidth={2}
              />
            ) : null}
          </g>
        ))}
      </svg>
      <figcaption>
        {found.length === 0
          ? "No overlays for this frame."
          : found.map((m, i) => <span key={i} style={{ marginRight: 8 }}>{m.used_concept ?? m.concept}</span>)}
      </figcaption>
    </figure>
  );
}
