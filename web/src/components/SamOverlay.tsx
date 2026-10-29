interface Mask {
  concept?: string;
  used_concept?: string;
  found?: boolean;
  svg_path?: string;
  box?: { x1: number; y1: number; x2: number; y2: number } | { x_min: number; x_max: number; y_min: number; y_max: number };
}

import { useState } from "react";

interface Props {
  imageUrl?: string;
  width: number;
  height: number;
  masks: Mask[];
  labels?: string[];
  label?: string;
}

function boxOf(box: Mask["box"]): { x: number; y: number; w: number; h: number } | null {
  if (!box) return null;
  if ("x1" in box) {
    return { x: box.x1, y: box.y1, w: Math.max(1, box.x2 - box.x1), h: Math.max(1, box.y2 - box.y1) };
  }
  return {
    x: box.x_min,
    y: box.y_min,
    w: Math.max(1, box.x_max - box.x_min),
    h: Math.max(1, box.y_max - box.y_min),
  };
}

/** SAM overlay viewer: keyframe photo plus SVG mask paths/boxes. */
export default function SamOverlay({ imageUrl, width, height, masks, labels, label }: Props) {
  // Mask coordinates arrive in the photo's pixel space, so the SVG
  // viewBox tracks the photo's natural size once it loads (props are
  // the fallback until then); the SVG sits absolutely over the img.
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  const vw = natural?.w ?? width;
  const vh = natural?.h ?? height;
  const found = masks.filter((m) => m.found !== false && (m.svg_path || m.box));
  const names = (labels ?? []).length > 0
    ? (labels ?? [])
    : [...new Set(found.map((m) => m.used_concept ?? m.concept).filter(Boolean) as string[])];
  // Overlay coordinates arrive in mixed scales (normalised vs pixel); the SVG
  // viewBox keeps them visible either way and boxes render as outlines.
  return (
    <figure className="sam">
      {label ? <figcaption className="sam-cap">{label}</figcaption> : null}
      {imageUrl ? (
        <span className="sam-img">
          {/* eslint-disable-next-line jsx-a11y/alt-text */}
          <img
            src={imageUrl}
            alt="Engine-bay keyframe"
            width="100%"
            onLoad={(e) => {
              const im = e.currentTarget;
              if (im.naturalWidth > 0 && im.naturalHeight > 0) {
                setNatural((prev) =>
                  prev?.w === im.naturalWidth && prev?.h === im.naturalHeight
                    ? prev
                    : { w: im.naturalWidth, h: im.naturalHeight },
                );
              }
            }}
          />
          <svg viewBox={`0 0 ${vw} ${vh}`} width="100%" role="img" aria-label="SAM overlays">
            {found.map((m, i) => (
              <g key={i}>
                {m.svg_path ? (
                  <path d={m.svg_path} fill="rgba(217,72,15,0.22)" stroke="#d9480f" strokeWidth={2} />
                ) : null}
                {boxOf(m.box) ? (
                  <rect
                    x={boxOf(m.box)!.x}
                    y={boxOf(m.box)!.y}
                    width={boxOf(m.box)!.w}
                    height={boxOf(m.box)!.h}
                    fill="none"
                    stroke="#d9480f"
                    strokeWidth={3}
                  />
                ) : null}
              </g>
            ))}
          </svg>
        </span>
      ) : (
        <span className="sam-empty" role="img" aria-label="Recorded SAM overlay without photo bytes">
          <svg viewBox={`0 0 ${width} ${height}`} width="100%">
            <rect x={0} y={0} width={width} height={height} fill="none" stroke="var(--line)" strokeDasharray="8 6" />
            <text x={width / 2} y={height / 2 - 8} fontSize={20} textAnchor="middle" fill="var(--muted)">
              engine-bay photo not in bundle
            </text>
            <text x={width / 2} y={height / 2 + 18} fontSize={14} textAnchor="middle" fill="var(--muted)">
              recorded overlay shapes below
            </text>
            {found.slice(0, 12).map((m, i) => {
              const b = boxOf(m.box);
              return (
                <g key={i}>
                  {b ? (
                    <rect x={b.x} y={b.y} width={b.w} height={b.h} fill="rgba(217,72,15,0.18)" stroke="#d9480f" strokeWidth={4} />
                  ) : null}
                </g>
              );
            })}
          </svg>
        </span>
      )}
      <figcaption className="sam-labels">
        {names.length === 0 && found.length === 0
          ? "No overlays for this frame."
          : names.map((n, i) => (
              <span key={`${n}-${i}`} className="badge ai">{n}</span>
            ))}
      </figcaption>
    </figure>
  );
}
