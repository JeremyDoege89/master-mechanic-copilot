"""Every emitted adapter_profile fixture validates against its schema (M2 correction 5).

The fixtures are written by FingerprintTest (Kotlin) into
backend/tests/fixtures/adapter_profiles/; this test validates all of them
plus the checked-in example against packages/schemas/adapter_profile.json.
"""

import glob
import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "packages" / "schemas" / "adapter_profile.json").read_text())
PROFILES_DIR = Path(__file__).resolve().parent / "fixtures" / "adapter_profiles"


def test_every_emitted_profile_validates():
    validator = jsonschema.Draft202012Validator(SCHEMA)
    fixtures = sorted(glob.glob(str(PROFILES_DIR / "*.json")))
    assert len(fixtures) >= 4, f"expected >= 4 emitted profiles, got {fixtures}"
    kinds = set()
    for fixture in fixtures:
        profile = json.loads(Path(fixture).read_text())
        validator.validate(profile)
        kinds.add(profile["kind"])
    assert {"genuine", "good_clone", "limited_clone", "obdlink"} <= kinds


def test_schema_example_still_validates():
    example = json.loads(
        (ROOT / "packages" / "schemas" / "examples" / "adapter_profile.example.json").read_text()
    )
    jsonschema.Draft202012Validator(SCHEMA).validate(example)
