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

def _decode_dtcs_kotlin_rule(payload: bytes) -> list[str]:
    """Python mirror of Parsers.decodeDtcs (P/C/B/U + packed nibbles)."""
    table = "PCBU"
    assert len(payload) % 2 == 0
    out = []
    for b0, b1 in zip(payload[0::2], payload[1::2]):
        out.append(
            table[(b0 >> 6) & 0x03]
            + chr(ord("0") + ((b0 >> 4) & 0x03))
            + f"{b0 & 0x0F:X}{(b1 >> 4) & 0x0F:X}{b1 & 0x0F:X}"
        )
    return out


_HEX = frozenset("0123456789ABCDEFabcdef")

_ERROR_LINES = frozenset({
    "NO DATA", "CAN ERROR", "BUS INIT: ...ERROR", "BUS INIT: ERROR",
    "BUFFER FULL", "STOPPED", "UNABLE TO CONNECT", "?",
})


def _drop_pci(payload: bytes) -> bytes:
    """Python mirror of Parsers.dropPciLength (length byte counts the rest)."""
    if payload and payload[0] == len(payload) - 1:
        return payload[1:]
    return payload


def _obd_data_line(line: str) -> tuple[str | None, bytes] | None:
    """Python mirror of Parsers.parseDataLine (spaced or ATS0-packed).

    Returns (can_id, payload) or None for blank/SEARCHING.../typed-error
    lines. Accepts ELM "N:" numbered lines, spaced "7E8 04 ..." lines and
    space-less "7E804..." lines, with or without the CAN-id header.
    """
    t = line.strip()
    if not t or t == "SEARCHING..." or t.upper() in _ERROR_LINES:
        return None
    if len(t) >= 2 and t[1] == ":" and t[0] in _HEX:
        return None, _drop_pci(bytes.fromhex("".join(t[2:].split())))
    compact = "".join(t.split())
    if any(c.isspace() for c in t):
        toks = t.split()
        if len(toks) >= 2 and len(toks[0]) == 3 and all(c in _HEX for c in toks[0]):
            return toks[0].upper(), _drop_pci(bytes.fromhex(compact[3:]))
        return None, _drop_pci(bytes.fromhex(compact))
    if (
        len(compact) > 3
        and all(c in _HEX for c in compact[:3])
        and len(compact[3:]) >= 2
        and len(compact[3:]) % 2 == 0
        and all(c in _HEX for c in compact[3:])
    ):
        return compact[:3].upper(), _drop_pci(bytes.fromhex(compact[3:]))
    return None, _drop_pci(bytes.fromhex(compact))


def _transcript_dtcs(raw: bytes) -> list[str]:
    """DTCs a ReplayTransport playback would show for a Mode 03 response."""
    rows = [json.loads(line) for line in raw.splitlines()]
    codes: list[str] = []
    for i, row in enumerate(rows):
        if row.get("dir") == "tx" and row.get("data") == "03":
            rx_data = rows[i + 1]["data"].replace("\r", "\n").replace(">", "")
            for line in rx_data.split("\n"):
                parsed = _obd_data_line(line)
                if parsed is None:
                    continue
                _can_id, payload = parsed
                if payload[:1] == b"\x43":
                    cells = _decode_dtcs_kotlin_rule(bytes(payload[1:]))
                    codes = [c for c in cells if c != "P0000"]
    return codes


def test_synth_transcript_dtc_cells_decode_to_snapshot_dtcs():
    """M7 review #14: transcript bytes encode the real P030n codes.

    The coil demo's events say P0303; the transcript must carry the
    packed bytes for P0303 (``0303``), not ``3033`` (which decodes to a
    non-existent P3033 through the app's own decoder).
    """
    gen = _load_generator()
    import sim_scenarios as sim_mod

    for sim in ("vacuum_leak", "coil_unplugged"):
        snap = sim_mod.snapshot(sim)
        want = [c.upper() for c in snap["points"]["idle"].get("dtcs", [])]
        raw = gen.synth_transcript(sim, snap)
        assert _transcript_dtcs(raw) == want
        if sim == "coil_unplugged":
            assert want == ["P0303"]

def _transcript_bytes(pairs: list[tuple[str, str]]) -> bytes:
    rows = [
        json.dumps({"t": 85 * k, "dir": direction, "data": data},
                   sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for k, (direction, data) in enumerate(pairs)
    ]
    return b"".join(rows)


def test_transcript_dtcs_accepts_spaceless_field_shapes():
    """ATS0+ATH1 parity: packed replies decode like the spaced ones."""
    # Packed with 11-bit header (Mazda 3 field shape).
    raw = _transcript_bytes([("tx", "03"), ("rx", "7E8054301330203\r\r>")])
    assert _transcript_dtcs(raw) == ["P0133", "P0203"]
    # Packed, headers off.
    raw = _transcript_bytes([("tx", "03"), ("rx", "054301330203\r\r>")])
    assert _transcript_dtcs(raw) == ["P0133", "P0203"]
    # Legacy spaced shapes still decode.
    raw = _transcript_bytes([("tx", "03"), ("rx", "7E8 05 43 01 33 02 03\r\r>")])
    assert _transcript_dtcs(raw) == ["P0133", "P0203"]
    raw = _transcript_bytes([("tx", "03"), ("rx", "43 01 33 02 03\r\r>")])
    assert _transcript_dtcs(raw) == ["P0133", "P0203"]
    # SEARCHING... prefix and prompt framing are tolerated.
    raw = _transcript_bytes([("tx", "03"), ("rx", "SEARCHING...\r7E8054301330203\r\r>")])
    assert _transcript_dtcs(raw) == ["P0133", "P0203"]
    # NO DATA yields no codes instead of raising.
    raw = _transcript_bytes([("tx", "03"), ("rx", "NO DATA\r\r>")])
    assert _transcript_dtcs(raw) == []


def test_obd_data_line_parity_mirrors_kotlin():
    """_obd_data_line matches Parsers.parseDataLine on every line shape."""
    assert _obd_data_line("7E8 04 41 0C 1A F8") == ("7E8", bytes((0x41, 0x0C, 0x1A, 0xF8)))
    assert _obd_data_line("7E804410C1AF8") == ("7E8", bytes((0x41, 0x0C, 0x1A, 0xF8)))
    assert _obd_data_line("04410C1AF8") == (None, bytes((0x41, 0x0C, 0x1A, 0xF8)))
    assert _obd_data_line("0: 14 49 02 01 31 46 41 54 50 38") == (
        None, bytes((0x14, 0x49, 0x02, 0x01, 0x31, 0x46, 0x41, 0x54, 0x50, 0x38)),
    )
    assert _obd_data_line("NO DATA") is None
    assert _obd_data_line("SEARCHING...") is None
    assert _obd_data_line("") is None


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
    # Appended via the registry: POST /events 403s engine-owned types
    # (M6 finding #1); the folded state is identical.
    from mmc import sessions as sess_mod

    recorded_session = sess_mod.get_registry().get(sid)
    assert recorded_session is not None
    sess_mod.get_registry().append(
        recorded_session,
        "report_built",
        {"report_id": "recorded", "wonder_review": review},
    )
    rep = client_no_keys.post(f"/sessions/{sid}/report")
    assert rep.status_code == 200, rep.text
    body = client_no_keys.get(f"/reports/{rep.json()['report_id']}.json").json()
    assert body["wonder_review"] == review
    assert body["wonder_unavailable"] is None


def test_replay_restores_recorded_report_id(client_no_keys):
    """Live bug §2: replaying a bundle serves its recorded report id in one click.

    The bundle carries the ``report_built`` event (id bb5539d4...) but no
    report file, so ``GET /reports/<id>.json`` 404s after a replay. Replay
    rebuilds the report deterministically under that SAME id with the
    recorded Wonder review ($0 — keyless here proves no live call).
    """
    from mmc.replay import mmrec, runner

    data = (SESSIONS_OUT / "sim-vacuum-leak.mmrec").read_bytes()
    members = mmrec.loads(data)
    mmrec.verify_members(members, "sim-vacuum-leak.mmrec")
    assert not any(n == "reports" or n.startswith("reports/") for n in members), (
        "bundle ships no report file (that is the bug shape)"
    )
    rows = mmrec.event_rows(members)
    recorded = [r for r in rows if r.get("type") == "report_built"][-1]
    report_id = recorded["payload"]["report_id"]
    assert report_id.startswith("bb5539d4"), report_id

    session = runner.replay_bundle_bytes(data, label="test")
    assert session.store.verify_chain()

    got = client_no_keys.get(f"/reports/{report_id}.json")
    assert got.status_code == 200, got.text[:300]
    report = got.json()
    assert report["report_id"] == report_id
    assert report["session_id"] == session.id
    assert report["wonder_review"] == recorded["payload"]["wonder_review"]
    assert report["wonder_review"] is not None
    assert report["wonder_unavailable"] is None
    html = client_no_keys.get(f"/reports/{report_id}.html")
    assert html.status_code == 200, html.text[:300]


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
