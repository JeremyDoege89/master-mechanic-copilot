"""Stop rule (M3): SPEC §5 conclude/refer gate."""

STOP_TOP_P = 0.85
STOP_EIG_BITS = 0.05


def should_stop(top_p: float, best_eig_bits: float | None) -> bool:
    """Conclude when P(top) ≥ 0.85 and the best remaining EIG < 0.05 bits,
    or when no positive-score test remains (``best_eig_bits`` None)."""
    if best_eig_bits is None:
        return True
    if best_eig_bits <= 0:
        return True
    return top_p >= STOP_TOP_P and best_eig_bits < STOP_EIG_BITS
