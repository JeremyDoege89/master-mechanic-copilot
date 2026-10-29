"""M6B judge demo bundles (plan §1 + approved corrections).

The full bundle tests need the owner-recorded demo cache
(``backend/tests/fixtures/demo_cache/``, via
``scripts/record_demo_cache.py`` run live outside the sandbox). Until that
cache exists they skip with that reason; the fail-fast, cache-miss and
Wonder-reuse tests below run now and pin the offline guarantees:

- the generator refuses loudly (naming record_demo_cache.py) when the
  demo cache is missing, instead of silently going live;
- replay with no cached LLM raises instead of spending;
- a report built where live Wonder is skipped reuses the recorded review
  from the latest ``report_built`` event ($0), else stays unavailable.

Correction 1: the keyframe overlay must not be empty —
``sam_stream_air_filter_housing.jsonl`` is the live "not found" result, so
the generator overlays Spark's short ``sam_concept`` for a component SAM
can find (the live battery stream, 3 objects) and the bundle test asserts
at least one non-empty mask.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# backend/tests/test_x.py -> backend -> repo root (holds scripts/).
REPO_ROOT = Path(__file__).resolve().parents[2]
while not (REPO_ROOT / "scripts").exists() and REPO_ROOT != REPO_ROOT.parent:
    REPO_ROOT = REPO_ROOT.parent

FIXTURES = Path(__file__).parent / "fixtures"
DEMO_CACHE = FIXTURES / "demo_cache"
SESSIONS_OUT = FIXTURES / "sessions"

REQUIRED_CACHE_FILES = (
    "second_opinion.json",
    "wonder_vacuum-leak.json",
    "wonder_coil-unplugged.json",
)


def _load_generator():
    path = REPO_ROOT / "scripts" / "make_demo_bundle.py"
    if not path.exists():
        pytest.fail(f"generator missing: {path}")
    spec = importlib.util.spec_from_file_location("make_demo_bundle", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["make_demo_bundle"] = module
    spec.loader.exec_module(module)
    return module


def _cache_missing_reason() -> str | None:
    missing = [f for f in REQUIRED_CACHE_FILES if not (DEMO_CACHE / f).exists()]
    if not missing:
        return None
    return (
        "demo cache missing ("
        + ", ".join(missing)
        + "); run scripts/record_demo_cache.py live outside the sandbox, "
        "then run scripts/make_demo_bundle.py and commit the bundles"
    )


def _require_cache():
    reason = _cache_missing_reason()
    if reason is not None:
        pytest.skip(reason)


# -- offline guarantees (run now) -------------------------------------------

def test_generator_fails_fast_without_demo_cache(tmp_path):
    """Correction 2: no cache -> loud failure naming record_demo_cache.py."""
    gen = _load_generator()
    with pytest.raises(gen.DemoCacheMissing) as excinfo:
        gen.require_demo_cache(tmp_path / "empty-cache")
    assert "record_demo_cache.py" in str(excinfo.value)


def test_generator_check_reports_missing_cache(tmp_path):
    gen = _load_generator()
    missing = gen.missing_cache_files(tmp_path / "empty-cache")
    assert set(missing) == set(REQUIRED_CACHE_FILES)


def test_record_mode_needs_only_second_opinion_with_stub_wonder(tmp_path):
    """Recording path works offline: second opinion only, stub Wonder call.

    The recorder's deadlock was ``build_bundle`` requiring the full cache
    (including the Wonder reviews it was about to record). Recording mode
    requires only ``second_opinion.json``, drives the deterministic
    session to its finished report + Wonder audit at $0, and the single
    Wonder call runs against that audit — here via a stub transport. The
    stub's record then replays at $0, which is exactly what cache mode
    relies on. Normal (cache) mode still requires everything.
    """
    import hashlib
    import shutil
    from types import SimpleNamespace

    from mmc.meta.budget import BudgetTracker

    gen = _load_generator()
    with pytest.raises(gen.DemoCacheMissing):
        gen.require_record_cache(tmp_path / "empty-cache")

    cache_dir = tmp_path / "record-cache"
    cache_dir.mkdir()
    shutil.copy(DEMO_CACHE / "second_opinion.json", cache_dir / "second_opinion.json")
    assert gen.missing_record_cache_files(cache_dir) == []

    # Normal mode still requires the Wonder reviews even now.
    with pytest.raises(gen.DemoCacheMissing) as excinfo:
        gen.build_bundle(
            "vacuum-leak", tmp_path / "x.mmrec", cache_dir=cache_dir,
        )
    assert "wonder_" in str(excinfo.value)

    context = gen.build_wonder_context(
        "vacuum-leak", cache_dir=cache_dir, data_dir=tmp_path / "record-data"
    )
    assert context["top_hypothesis"] == "vacuum_leak_post_maf"
    assert context["top_p"] >= 0.8
    assert context["audit_sha256"] == hashlib.sha256(
        gen._canonical_bytes(context["audit"])
    ).hexdigest()
    assert context["report"]["session_id"] == context["session_id"]

    review = {
        "agrees_with_top": True,
        "gaps": [{"issue": "stub gap", "test_ref": "visual_intake_boot"}],
        "narrative": "Stub senior review.",
    }
    stub_usage = {"input_tokens": 100, "output_tokens": 50}

    def stub_transport(model, kwargs):
        assert model.startswith("muse-wonder-"), model
        return SimpleNamespace(output_text=json.dumps(review), usage=dict(stub_usage))

    from mmc.meta import wonder as wonder_mod
    from mmc.meta.client import MetaClient

    live_client = MetaClient(
        api_key="test-key",
        budget=BudgetTracker(hard_stop_usd=10.0),
        cache_dir=tmp_path / "record-live-cache",
        replay_cached=False,
        transport=stub_transport,
        sleep=lambda s: None,
    )
    parsed, record = wonder_mod.request_review(context["audit"], live_client)
    assert parsed == review
    assert record["request_sha256"]

    # The recorded file replays at $0 through the real cache path.
    replay_dir = tmp_path / "record-replay-cache"
    replay_dir.mkdir()
    (replay_dir / f"{record['request_sha256']}.json").write_text(
        json.dumps(record, sort_keys=True), encoding="utf-8"
    )
    replay_client = MetaClient(
        api_key="test-key",
        budget=BudgetTracker(hard_stop_usd=10.0),
        cache_dir=replay_dir,
        replay_cached=True,
        transport=lambda m, kw: (_ for _ in ()).throw(AssertionError("must not go live")),
        sleep=lambda s: None,
    )
    reparsed, rerecord = wonder_mod.request_review(context["audit"], replay_client)
    assert reparsed == review
    assert rerecord["cost_usd"] == 0.0


def test_replay_cache_miss_raises_instead_of_spending(tmp_path):
    """CI never goes live: a replay-mode client with an empty cache raises."""
    from mmc.meta.client import MetaClient, NoCachedResponse
    from mmc.meta.budget import BudgetTracker

    client = MetaClient(
        api_key="test-key",
        models={"spark": "muse-spark-1.3"},
        budget=BudgetTracker(hard_stop_usd=120.0),
        cache_dir=tmp_path / "empty-cache",
        replay_cached=True,
        transport=lambda m, kw: (_ for _ in ()).throw(AssertionError("must not go live")),
        sleep=lambda s: None,
    )
    with pytest.raises(NoCachedResponse):
        client.call(
            kind="llm_call",
            model_key="spark",
            request={"op": "probe", "input": []},
            estimate_usd=0.01,
        )


def _client_no_keys(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from mmc.api import routes as api_routes
    from mmc.main import app

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REPLAY_USE_CACHED_LLM", "true")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    monkeypatch.delenv("MMC_PUBLIC_MODE", raising=False)
    api_routes._listeners.clear()
    client = TestClient(app)
    yield client
    api_routes._listeners.clear()


import pytest as _pytest


@_pytest.fixture
def client_no_keys(tmp_path, monkeypatch):
    yield from _client_no_keys(tmp_path, monkeypatch)


def test_report_without_recorded_review_stays_unavailable(client_no_keys):
    """No cached review anywhere -> explicit unavailable line, $0."""
    r = client_no_keys.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "live"})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    rep = client_no_keys.post(f"/sessions/{sid}/report")
    assert rep.status_code == 200, rep.text
    body = client_no_keys.get(f"/reports/{rep.json()['report_id']}.json").json()
    assert body["wonder_review"] is None
    assert body["wonder_unavailable"] is not None


def test_report_reuses_recorded_wonder_review(client_no_keys):
    """Backend fix (§1.2): live Wonder skipped -> latest report_built review."""
    review = {
        "agrees_with_top": True,
        "gaps": [{"issue": "confirm the intake boot visually", "test_ref": "visual_intake_boot"}],
        "narrative": "Recorded senior review.",
    }
    r = client_no_keys.post("/sessions", json={"user_level": "L1", "tools": [], "mode": "live"})
    sid = r.json()["session_id"]
    # A previous (recorded) report build embedded its review in report_built.
    r = client_no_keys.post(
        f"/sessions/{sid}/events",
        json={"type": "report_built", "payload": {"report_id": "recorded", "wonder_review": review}},
    )
    assert r.status_code == 200, r.text
    rep = client_no_keys.post(f"/sessions/{sid}/report")
    assert rep.status_code == 200, rep.text
    body = client_no_keys.get(f"/reports/{rep.json()['report_id']}.json").json()
    assert body["wonder_review"] == review
    assert body["wonder_unavailable"] is None


# -- full bundle tests (skip until the owner records the demo cache) --------

BUNDLES = {
    "vacuum-leak": ("sim-vacuum-leak.mmrec", "vacuum_leak_post_maf"),
    "coil-unplugged": ("sim-coil-unplugged.mmrec", "ignition_misfire_cyl"),
}


def _bundle_bytes(name: str) -> bytes:
    _require_cache()
    path = SESSIONS_OUT / name
    assert path.exists(), f"{path} missing: run scripts/make_demo_bundle.py and commit the bundles"
    return path.read_bytes()


def test_bundle_verifies_and_holds_many_events():
    from mmc.replay import mmrec

    for _key, (name, _true) in BUNDLES.items():
        data = _bundle_bytes(name)
        members = mmrec.loads(data)
        mmrec.verify_members(members, name)
        rows = mmrec.event_rows(members)
        assert len(rows) >= 15, (name, len(rows))
        types = [row["type"] for row in rows]
        assert types[0] == "session_started"
        assert "report_built" in types


def _replay_state(client, data: bytes) -> dict:
    from mmc.replay import runner

    session = runner.replay_bundle_bytes(data, label="test")
    assert session.store.verify_chain()
    return client.get(f"/sessions/{session.id}/state").json()


def test_bundle_converges_on_truth_with_rank1_loop(client_no_keys):
    from mmc.replay import mmrec

    for key, (name, true_hyp) in BUNDLES.items():
        data = _bundle_bytes(name)
        members = mmrec.loads(data)
        rows = mmrec.event_rows(members)
        performed = [r["payload"]["test_ref"] for r in rows if r["type"] == "test_performed"]
        # Confirmatory battery check (manual-flip showcase) rides after the
        # convergent rank-1 loop; see test_bundle_manual_flips_outcome.
        loop = [t for t in performed if t != "battery_voltage_rest_running"]
        assert 1 <= len(loop) <= 4, (name, performed)
        assert "battery_voltage_rest_running" in performed, (name, performed)
        state = _replay_state(client_no_keys, data)
        top = state["hypotheses"][0]
        assert top["hypothesis_id"] == true_hyp, (name, top["hypothesis_id"])
        assert top["current"] >= 0.8, (name, top["current"])


def test_bundle_manual_flips_outcome():
    from mmc.replay import mmrec

    for _key, (name, _true) in BUNDLES.items():
        data = _bundle_bytes(name)
        rows = mmrec.event_rows(mmrec.loads(data))
        results = [r["payload"] for r in rows if r["type"] == "test_result"]
        manual = [r for r in results if r.get("expected", {}).get("source", {}).get("kind") == "manual_page"]
        assert manual, (name, "no manual_page result")
        battery = [r for r in manual if r["test_id"] == "battery_voltage_rest_running"]
        assert battery, (name, "manual flip must ride on the battery check")
        assert battery[0]["outcome"] == "normal", (name, battery[0])
        # The book range genuinely decides: without it the same volts read high.
        from mmc.engine import evaluator
        from mmc import sessions as sess_mod

        test = sess_mod.get_pack().tests["battery_voltage_rest_running"]
        measured = dict(battery[0]["measured"]["value"])
        outcome_plain, _ = evaluator.evaluate(test, measured)
        assert outcome_plain == "overcharging", (name, measured)


def test_bundle_sound_is_dsp_not_ai():
    from mmc.replay import mmrec

    want = {"vacuum-leak": "vacuum_hiss", "coil-unplugged": "misfire_dropout"}
    for key, (name, _true) in BUNDLES.items():
        data = _bundle_bytes(name)
        rows = mmrec.event_rows(mmrec.loads(data))
        results = [r["payload"] for r in rows if r["type"] == "test_result"]
        sound = [r for r in results if r["test_id"] == "engine_sound_screen"]
        assert sound and sound[0]["outcome"] == want[key], (name, sound)
        assert sound[0]["evaluator"] == "dsp.engine_sound", (name, sound[0])
        audio_evidence = [
            r["payload"]
            for r in rows
            if r["type"] == "evidence_added" and r["payload"].get("type") == "audio"
        ]
        assert audio_evidence, (name, "no audio evidence")


def test_bundle_keyframe_overlay_is_nonempty_with_agreement():
    """Correction 1: at least one non-empty mask, never the not-found stream."""
    from mmc.replay import mmrec

    for _key, (name, _true) in BUNDLES.items():
        data = _bundle_bytes(name)
        members = mmrec.loads(data)
        rows = mmrec.event_rows(members)
        images = [
            r["payload"]
            for r in rows
            if r["type"] == "evidence_added" and r["payload"].get("type") == "image"
        ]
        assert images, (name, "no keyframe evidence")
        overlay_masks = [
            mask
            for overlay in images[0].get("overlays", [])
            for mask in overlay.get("masks", [])
            if mask.get("svg_path") or mask.get("box")
        ]
        assert overlay_masks, (name, "keyframe evidence carries no SAM overlay")
        keyframe_id = images[0]["evidence_id"]
        observations = [
            r["payload"]
            for r in rows
            if r["type"] == "observation_added"
            and keyframe_id in (r["payload"].get("evidence_ids") or [])
        ]
        assert observations, (name, "no keyframe observations")
        agreements = {o.get("agreement") for o in observations if o.get("agreement")}
        assert agreements <= {"corroborated", "single_source", "disputed"}, (name, agreements)
        assert agreements, (name, "second opinion never merged")
        # Masks are served with the vision response, not stored in events,
        # so the generator records its overlay summary in meta.json.
        meta = json.loads(members["meta.json"].decode("utf-8"))
        keyframe = meta.get("keyframe", {})
        assert keyframe.get("nonempty_masks", 0) >= 1, (name, keyframe)
        assert keyframe.get("used_concept"), (name, keyframe)


def test_bundle_report_embeds_wonder_review_at_zero_spend(client_no_keys):
    from mmc.meta.budget import get_tracker
    from mmc.replay import mmrec

    for _key, (name, _true) in BUNDLES.items():
        data = _bundle_bytes(name)
        rows = mmrec.event_rows(mmrec.loads(data))
        built = [r["payload"] for r in rows if r["type"] == "report_built"]
        assert built and built[-1].get("wonder_review") is not None, (name, "no embedded review")
        meta_before = get_tracker("meta").spend_usd
        baseten_before = get_tracker("baseten").spend_usd
        state = _replay_state(client_no_keys, data)
        assert state["hypotheses"]
        rep = client_no_keys.post(f"/sessions/{state['session_id']}/report")
        assert rep.status_code == 200, rep.text
        body = client_no_keys.get(f"/reports/{rep.json()['report_id']}.json").json()
        assert body["wonder_review"] is not None, (name, body.get("wonder_unavailable"))
        assert get_tracker("meta").spend_usd == meta_before
        assert get_tracker("baseten").spend_usd == baseten_before


def test_bundle_regeneration_is_byte_identical(tmp_path):
    _require_cache()
    gen = _load_generator()
    for key, (name, _true) in BUNDLES.items():
        first = gen.build_bundle(key, tmp_path / f"a-{name}")
        second = gen.build_bundle(key, tmp_path / f"b-{name}")
        assert first["sha256"] == second["sha256"], (name, first, second)
        committed = SESSIONS_OUT / name
        assert committed.exists(), f"{committed} missing: commit the owner-built bundles"
        import hashlib

        assert hashlib.sha256(committed.read_bytes()).hexdigest() == first["sha256"], (
            name,
            "committed bundle differs from a fresh regeneration",
        )
