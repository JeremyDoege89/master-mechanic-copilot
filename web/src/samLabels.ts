/** Shared SAM candidate labelling (M5 overlays).
 *
 * Confidence is not identity: a scored candidate renders "<concept>? <score>";
 * when the recording carries no per-mask score, candidates render
 * "<concept>? (1)", "(2)", ... in display order and the caption says so
 * explicitly — no candidate is ever called the match. Display-only: recorded
 * masks are unchanged.
 */

export interface ScoredMask {
  score?: number | null;
}

export function hasScores(masks: readonly ScoredMask[]): boolean {
  return masks.some(
    (m) => typeof m.score === "number" && Number.isFinite(m.score),
  );
}

/** One candidate tag; position is 1-based display order. */
export function samCandidateLabel(
  concept: string,
  score: number | null | undefined,
  position: number,
): string {
  return typeof score === "number" && Number.isFinite(score)
    ? `${concept}? ${score.toFixed(2)}`
    : `${concept}? (${position})`;
}

export function samCaption(
  names: string[],
  count: number,
  scored: boolean,
): string {
  const cands = `${count} candidate${count === 1 ? "" : "s"}`;
  if (names.length === 0) {
    return scored
      ? `SAM 3.1 found ${cands}; scores are SAM's own confidence, not a confirmed match.`
      : `SAM 3.1 found ${cands}; this recording has no per-candidate confidence, so none is marked as the match.`;
  }
  const who = names.map((n) => `"${n}"`).join(", ");
  return scored
    ? `SAM 3.1 found ${cands} for ${who}; scores are SAM's own confidence, not a confirmed match.`
    : `SAM 3.1 found ${cands} for ${who}; this recording has no per-candidate confidence, so none is marked as the match.`;
}
