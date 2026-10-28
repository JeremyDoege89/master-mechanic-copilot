"""Knowledge-pack loader (M3).

Validates the YAML packs in ``packs/``: hypothesis priors, per-test
likelihood rows (sum to 1 ± 1e-9, every cell within [LIK_MIN, LIK_MAX] so no
single test is a silver bullet), and fills hypotheses missing from a test's
likelihood table with a uniform-over-outcomes row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import yaml

LIK_MIN = 0.02
LIK_MAX = 0.95
ROW_TOL = 1e-9

LEVELS = {"L1": 1, "L2": 2, "L3": 3}

PACKS_DIR = Path(__file__).resolve().parent / "packs"


@dataclass
class KpTest:
    ref: str
    kind: str
    min_level: str
    tools: list[str] = field(default_factory=list)
    minutes: float = 0.0
    usd: float = 0.0
    risk: int = 0
    safety_gate: str = "none"
    outcomes: list[str] = field(default_factory=list)
    evaluator: str = ""
    likelihoods: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class KnowledgePack:
    version: str
    hypotheses: dict[str, dict]  # id -> {prior, label}
    tests: dict[str, KpTest]
    dependency_groups: list[list[str]] = field(default_factory=list)


def _check_likelihood_row(ref: str, hyp: str, row: dict[str, float], outcomes: list[str]) -> None:
    missing = set(outcomes) - set(row)
    extra = set(row) - set(outcomes)
    if missing or extra:
        raise ValueError(f"test {ref}/{hyp}: outcomes mismatch (missing={missing}, extra={extra})")
    total = sum(row[o] for o in outcomes)
    if abs(total - 1.0) > ROW_TOL:
        raise ValueError(f"test {ref}/{hyp}: likelihoods sum to {total}, expected 1 ± {ROW_TOL}")
    for outcome in outcomes:
        p = row[outcome]
        if not (LIK_MIN <= p <= LIK_MAX):
            raise ValueError(
                f"test {ref}/{hyp}/{outcome}: likelihood {p} outside [{LIK_MIN}, {LIK_MAX}]; "
                "no single test may be a silver bullet"
            )


def load_pack(path: str | Path) -> KnowledgePack:
    raw = yaml.safe_load(Path(path).read_text())
    version = raw.get("version") or Path(path).stem
    hyp_defs = raw.get("hypotheses", {})
    if not hyp_defs:
        raise ValueError(f"{path}: pack defines no hypotheses")
    for hyp, spec in hyp_defs.items():
        if not spec.get("prior", 0) > 0:
            raise ValueError(f"{path}: hypothesis {hyp} needs a positive prior")
        label = spec.get("label", {})
        if "L1" not in label or "L3" not in label:
            raise ValueError(f"{path}: hypothesis {hyp} label needs L1 and L3 text")

    total_prior = sum(spec["prior"] for spec in hyp_defs.values())
    hypotheses = {
        hyp: {"prior": spec["prior"] / total_prior, "label": spec["label"]}
        for hyp, spec in hyp_defs.items()
    }

    tests: dict[str, KpTest] = {}
    for ref, spec in raw.get("tests", {}).items():
        outcomes = list(spec.get("outcomes", []))
        if not outcomes:
            raise ValueError(f"{path}: test {ref} needs a non-empty outcomes list")
        if spec.get("min_level") not in LEVELS:
            raise ValueError(f"{path}: test {ref} has bad min_level {spec.get('min_level')}")
        likelihoods: dict[str, dict[str, float]] = {}
        for hyp, row in (spec.get("likelihoods") or {}).items():
            if hyp not in hyp_defs:
                raise ValueError(f"{path}: test {ref} lists unknown hypothesis {hyp}")
            row = {o: float(p) for o, p in row.items()}
            _check_likelihood_row(ref, hyp, row, outcomes)
            likelihoods[hyp] = row
        # Uniform default for hypotheses the test does not mention.
        uniform = {o: 1.0 / len(outcomes) for o in outcomes}
        for hyp in hyp_defs:
            likelihoods.setdefault(hyp, dict(uniform))
        tests[ref] = KpTest(
            ref=ref,
            kind=spec.get("kind", ""),
            min_level=spec["min_level"],
            tools=list(spec.get("tools", [])),
            minutes=float(spec.get("minutes", 0)),
            usd=float(spec.get("usd", 0.0)),
            risk=int(spec.get("risk", 0)),
            safety_gate=spec.get("safety_gate", "none"),
            outcomes=outcomes,
            evaluator=spec.get("evaluator", ""),
            likelihoods=likelihoods,
        )
    return KnowledgePack(
        version=version,
        hypotheses=hypotheses,
        tests=tests,
        dependency_groups=[list(g) for g in raw.get("dependency_groups", [])],
    )


def load_ford_gen_v1() -> KnowledgePack:
    return load_pack(PACKS_DIR / "ford_gen_v1.yaml")
