"""KP loader tests (M3 step 2), incl. the likelihood sanity bound."""

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from mmc.kp.loader import (
    LIK_MAX,
    LIK_MIN,
    load_pack,
    PACKS_DIR,
)

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "packages" / "schemas"


def _write_pack(tmp_path, likelihoods, outcomes=("a", "b")):
    pack = {
        "version": "test-v1",
        "hypotheses": {
            "h1": {"prior": 0.5, "label": {"L1": "one", "L3": "one"}},
            "h2": {"prior": 0.5, "label": {"L1": "two", "L3": "two"}},
        },
        "tests": {
            "t1": {
                "kind": "obd_read",
                "min_level": "L1",
                "tools": [],
                "minutes": 1,
                "risk": 0,
                "safety_gate": "none",
                "outcomes": list(outcomes),
                "evaluator": "rule.direct",
                "likelihoods": likelihoods,
            }
        },
    }
    path = tmp_path / "pack.yaml"
    path.write_text(yaml.safe_dump(pack))
    return path


def test_ford_pack_loads_and_priors_normalize():
    pack = load_pack(PACKS_DIR / "ford_gen_v1.yaml")
    assert pack.version == "ford-gen-v1"
    total = sum(h["prior"] for h in pack.hypotheses.values())
    assert abs(total - 1.0) < 1e-9
    assert len(pack.hypotheses) == 11
    assert pack.dependency_groups == [["stft_b1", "ltft_b1"]]


def test_generic_pack_loads_with_uniform_fill():
    pack = load_pack(PACKS_DIR / "generic_gas_v1.yaml")
    # trim test omits most hypotheses -> uniform fill over its 4 outcomes.
    row = pack.tests["trim_idle_vs_2500"].likelihoods["charging_low"]
    assert set(row) == {"lean_idle_normal_cruise", "lean_both", "normal", "rich"}
    assert all(abs(p - 0.25) < 1e-12 for p in row.values())


def test_likelihood_rows_sum_to_one():
    for name in ("ford_gen_v1.yaml", "generic_gas_v1.yaml"):
        pack = load_pack(PACKS_DIR / name)
        for ref, test in pack.tests.items():
            for hyp, row in test.likelihoods.items():
                assert abs(sum(row[o] for o in test.outcomes) - 1.0) < 1e-9, (name, ref, hyp)


def test_likelihood_cells_within_bounds_in_packs():
    for name in ("ford_gen_v1.yaml", "generic_gas_v1.yaml"):
        pack = load_pack(PACKS_DIR / name)
        for ref, test in pack.tests.items():
            for hyp, row in test.likelihoods.items():
                for outcome, p in row.items():
                    assert LIK_MIN <= p <= LIK_MAX, (name, ref, hyp, outcome, p)


def test_likelihood_above_max_rejected(tmp_path):
    path = _write_pack(tmp_path, {"h1": {"a": 0.96, "b": 0.04}, "h2": {"a": 0.5, "b": 0.5}})
    with pytest.raises(ValueError, match="silver bullet"):
        load_pack(path)


def test_likelihood_below_min_rejected(tmp_path):
    path = _write_pack(tmp_path, {"h1": {"a": 0.019, "b": 0.981}, "h2": {"a": 0.5, "b": 0.5}})
    # 0.019 < 0.02 -> rejected even though the row sums to 1.
    with pytest.raises(ValueError, match="silver bullet"):
        load_pack(path)


def test_likelihood_row_must_sum_to_one(tmp_path):
    path = _write_pack(tmp_path, {"h1": {"a": 0.6, "b": 0.6}, "h2": {"a": 0.5, "b": 0.5}})
    with pytest.raises(ValueError, match="sum to"):
        load_pack(path)


@pytest.mark.parametrize("schema_name", ["hypothesis", "next_test", "test_result"])
def test_engine_schemas_validate_examples(schema_name):
    schema = json.loads((SCHEMAS / f"{schema_name}.json").read_text())
    example = json.loads((SCHEMAS / "examples" / f"{schema_name}.example.json").read_text())
    jsonschema.Draft202012Validator(schema).validate(example)
