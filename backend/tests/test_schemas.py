"""Every schema validates its own example (M0b)."""

import glob
import json
from pathlib import Path

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "packages" / "schemas"
EXAMPLES = SCHEMAS / "examples"


def _registry():
    pairs = []
    for schema_path in sorted(glob.glob(str(SCHEMAS / "*.json"))):
        schema = json.loads(Path(schema_path).read_text())
        pairs.append(
            (Path(schema_path).name, Resource.from_contents(schema, default_specification=DRAFT202012))
        )
    return Registry().with_resources(pairs)


def test_each_schema_validates_its_example():
    schema_files = sorted(glob.glob(str(SCHEMAS / "*.json")))
    assert len(schema_files) == 13, f"expected 13 schemas, got {len(schema_files)}"
    registry = _registry()
    for schema_path in schema_files:
        name = Path(schema_path).stem
        example_path = EXAMPLES / f"{name}.example.json"
        assert example_path.exists(), f"missing example for {name}"
        schema = json.loads(Path(schema_path).read_text())
        example = json.loads(example_path.read_text())
        validator = jsonschema.Draft202012Validator(schema, registry=registry)
        validator.validate(example)


def test_event_example_chain_hash_is_consistent():
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    from mmc.store.event_store import event_hash, payload_hash

    example = json.loads((EXAMPLES / "event.example.json").read_text())
    assert example["payload_sha256"] == payload_hash(example["payload"])
    assert example["hash"] == event_hash(
        example["seq"],
        example["ts"],
        example["type"],
        example["payload_sha256"],
        example["prev_hash"],
    )
