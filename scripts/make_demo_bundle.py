"""M6B judge demo bundles (plan §1).

Drives the REAL backend in-process (FastAPI ``create_app`` + ``TestClient``,
non-public mode, ``REPLAY_USE_CACHED_LLM=true``) and never hand-writes
posteriors: every posterior comes out of the engine fold via the normal
routes. Vehicle truth comes from the Python parity fixture
``backend/tests/fixtures/sim_scenarios.py`` (same constants as Kotlin
``Elm327Sim.kt``); the Android sim is not run.

Per bundle, in order:

1. ``POST /sessions`` (sim Ford 2.0 L, ``vin_last6`` only, L2,
   ``[multimeter, fuel pressure gauge]``, mode live).
2. ``adapter_fingerprinted`` event (GENUINE personality).
3. DTC/PID evidence (``evidence_added`` + matching ``observation_added``)
   from ``snapshot(scenario)``.
4. Manual-PDF upload + ``POST /uploads/pdf/extract`` FIRST (the recorded
   book ranges must exist before the confirmatory battery check that cites
   them; evidence order does not move posteriors).
5. Planner loop: ``GET /state``, take ``next_tests[0]``; if gated, append
   ``safety_ack`` and re-read (the new rank 1 is then performed). Each
   performed ref must equal the planner rank 1 at submission time. Loop
   stops once converged AND the DSP sound screen has run (cap 4).
   ``engine_sound_screen`` is fulfilled with a deterministic synth WAV
   through ``POST /vision/engine-sound`` (pure DSP, $0); every other test
   uses the fixture measured payload.
6. Keyframe after the 2nd performed test: fixture photo through
   ``POST /vision/keyframe`` (recorded Spark components + recorded SAM
   bytes parsed by the real parser + recorded Spark Open second opinion
   merged for real). The overlay uses a concept SAM can find (battery);
   the generator fails loudly on an empty overlay (approved correction 1).
7. Confirmatory battery check: ``battery_voltage_rest_running`` is always
   runnable at L2 (no gate) but the planner correctly ranks it low for
   these scenarios, so it rides after the convergent loop as a labelled
   showcase of manual > kp > physics: 14.75 V running reads overcharging
   against the generic 13.5-14.7 band but normal against the recorded book
   13.5-14.8 page. Fails loudly when the flip does not reproduce.
8. ``POST /report``: Wonder is served from the recorded demo cache ($0,
   validated again on the way in); ``report_built`` embeds the review.

Byte-identical output: frozen clock + deterministic uuids, sorted JSON,
fixed blob bytes, fixed zip timestamps. Two consecutive runs give equal
sha256 (asserted by ``tests/test_demo_bundles.py``).

Demo-cache provenance (``backend/tests/fixtures/demo_cache/``): every
model output is a REAL recorded live call — Spark components
(``fixtures/vision/spark_components_01.json``), SAM bytes
(``fixtures/vision/sam_stream_battery.jsonl`` unless the demo cache holds
a fresher ``sam_<concept>.jsonl``), Spark Open second opinion
(``demo_cache/second_opinion.json``), Wonder reviews
(``demo_cache/wonder_<scenario>.json``). No demo cache -> :class:`DemoCacheMissing`
naming ``scripts/record_demo_cache.py`` (approved correction 2); the
generator never silently goes live in CI.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import itertools
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"
FIXTURES_DIR = BACKEND_DIR / "tests" / "fixtures"
VISION_FIXTURES = FIXTURES_DIR / "vision"
UPLOAD_FIXTURES = FIXTURES_DIR / "upload"
IMAGES_DIR = FIXTURES_DIR / "images"
DEFAULT_CACHE_DIR = FIXTURES_DIR / "demo_cache"
DEFAULT_OUT_DIR = FIXTURES_DIR / "sessions"

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(FIXTURES_DIR))

SECOND_OPINION_FILE = "second_opinion.json"
WONDER_FILES = {
    "vacuum-leak": "wonder_vacuum-leak.json",
    "coil-unplugged": "wonder_coil-unplugged.json",
}
REQUIRED_CACHE_FILES = (SECOND_OPINION_FILE, *WONDER_FILES.values())
# Recording mode (record_demo_cache.py) needs only the non-Wonder entries:
# the Wonder review's input is the finished report, so the report + audit
# are built first from these and the live Wonder call happens afterwards.
RECORD_REQUIRED_CACHE_FILES = (SECOND_OPINION_FILE,)

SCENARIOS = {"vacuum-leak": "vacuum_leak", "coil-unplugged": "coil_unplugged"}
TRUE_HYPOTHESIS = {
    "vacuum-leak": "vacuum_leak_post_maf",
    "coil-unplugged": "ignition_misfire_cyl",
}
BUNDLE_FILENAMES = {
    "vacuum-leak": "sim-vacuum-leak.mmrec",
    "coil-unplugged": "sim-coil-unplugged.mmrec",
}

VEHICLE = {
    "make": "Ford",
    "model": "Focus",
    "year": 2012,
    "engine": "2.0L I4",
    "vin_last6": "ABC123",
}
USER_LEVEL = "L2"
USER_TOOLS = ["multimeter", "fuel pressure gauge"]

ENGINE_IMAGE = "engine_bay_02.jpg"
PDF_FIXTURE = "upload_spec_test.pdf"
SPARK_COMPONENTS_FIXTURE = "spark_components_01.json"
SAM_BATTERY_FIXTURE = "sam_stream_battery.jsonl"
OVERLAY_CONCEPT = "battery"

SOUND_RPM = 750.0
SOUND_CYLINDERS = 4
SOUND_WAV_SECONDS = 6.0
SOUND_WANTED = {"vacuum-leak": "vacuum_hiss", "coil-unplugged": "misfire_dropout"}

# Post-convergence showcase: generic running band reads these volts high,
# the recorded book page (13.5-14.8 V) reads them normal.
BATTERY_MEASURED = {"voltage_v": 14.75, "engine_on": True}
BATTERY_SPEC_ID = "charging_voltage_v"

BUNDLE_DATE_TIME = (2026, 10, 29, 0, 0, 0)
MAX_LOOP_TESTS = 4


class DemoCacheMissing(Exception):
    """Raised when the recorded demo cache is absent or incomplete."""


def missing_cache_files(cache_dir: Path | str) -> list[str]:
    cache = Path(cache_dir)
    return [name for name in REQUIRED_CACHE_FILES if not (cache / name).exists()]


def require_demo_cache(cache_dir: Path | str) -> dict:
    """Load and parse the demo cache, or fail naming the recorder script."""
    missing = missing_cache_files(cache_dir)
    if missing:
        raise DemoCacheMissing(
            "demo cache incomplete (missing: "
            + ", ".join(missing)
            + f") in {cache_dir}; run scripts/record_demo_cache.py live outside "
            "the sandbox (about $0.20 one-time), then run "
            "scripts/make_demo_bundle.py and commit the bundles"
        )
    cache = Path(cache_dir)
    out: dict = {}
    for name in REQUIRED_CACHE_FILES:
        try:
            out[name] = json.loads((cache / name).read_text(encoding="utf-8"))
        except ValueError as e:
            raise DemoCacheMissing(f"demo cache file {name} is not valid JSON: {e}") from e
    return out


def missing_record_cache_files(cache_dir: Path | str) -> list[str]:
    """Non-Wonder cache entries missing (the recording-mode requirement)."""
    cache = Path(cache_dir)
    return [name for name in RECORD_REQUIRED_CACHE_FILES if not (cache / name).exists()]


def require_record_cache(cache_dir: Path | str) -> dict:
    """Load the non-Wonder demo cache, or fail naming the recorder script."""
    missing = missing_record_cache_files(cache_dir)
    if missing:
        raise DemoCacheMissing(
            "demo cache incomplete for recording (missing: "
            + ", ".join(missing)
            + f") in {cache_dir}; run scripts/record_demo_cache.py live outside "
            "the sandbox (about $0.20 one-time), then run "
            "scripts/make_demo_bundle.py and commit the bundles"
        )
    cache = Path(cache_dir)
    out: dict = {}
    for name in RECORD_REQUIRED_CACHE_FILES:
        try:
            out[name] = json.loads((cache / name).read_text(encoding="utf-8"))
        except ValueError as e:
            raise DemoCacheMissing(f"demo cache file {name} is not valid JSON: {e}") from e
    return out


@contextlib.contextmanager
def deterministic():
    """Frozen clock + deterministic uuids for byte-identical bundles.

    Patches the narrow choke points every timestamp/uuid flows through:
    ``uuid.uuid4`` globally, the ``datetime`` class globally (covers
    function-local ``from datetime import datetime`` imports) and on the
    backend modules that imported it at module level, plus the two
    ``_utc_now`` helpers. Restores everything on exit.
    """
    import datetime as dt_mod
    import uuid as uuid_mod

    base = dt_mod.datetime(2026, 10, 29, 0, 0, 0, tzinfo=dt_mod.timezone.utc)
    counter = itertools.count()
    lock = threading.Lock()
    real_uuid4 = uuid_mod.uuid4
    real_datetime = dt_mod.datetime

    def fake_uuid4():
        with lock:
            i = next(counter)
        return uuid_mod.uuid5(uuid_mod.NAMESPACE_URL, f"mmc-demo:{i}")

    class _FrozenDT(real_datetime):
        @classmethod
        def now(cls, tz=None):
            with lock:
                i = next(counter)
            ts = base + dt_mod.timedelta(seconds=i)
            if tz is None:
                return ts.replace(tzinfo=None)
            return ts.astimezone(tz)

    import mmc.sessions as sess_mod
    import mmc.store.event_store as store_mod

    datetime_modules = []
    for module_name in (
        "mmc.vision.components",
        "mmc.vision.engine_sound",
        "mmc.upload.specs",
        "mmc.upload.video",
        "mmc.upload.csv_logs",
        "mmc.vision.instruments",
    ):
        try:
            module = sys.modules.get(module_name) or __import__(module_name, fromlist=["x"])
        except ImportError:
            continue
        if hasattr(module, "datetime"):
            datetime_modules.append(module)
    saved = [(m, m.datetime) for m in datetime_modules]
    real_sess_now = sess_mod._utc_now
    real_store_now = store_mod.utc_now_iso

    def _frozen_now() -> str:
        return _FrozenDT.now(dt_mod.timezone.utc).isoformat()

    uuid_mod.uuid4 = fake_uuid4  # type: ignore[assignment]
    dt_mod.datetime = _FrozenDT  # type: ignore[assignment]
    for module, _ in saved:
        module.datetime = _FrozenDT
    sess_mod._utc_now = _frozen_now
    store_mod.utc_now_iso = _frozen_now
    try:
        yield
    finally:
        uuid_mod.uuid4 = real_uuid4
        dt_mod.datetime = real_datetime
        for module, original in saved:
            module.datetime = original
        sess_mod._utc_now = real_sess_now
        store_mod.utc_now_iso = real_store_now


_SAM_PARSE_CACHE: dict[str, tuple] = {}


def _recorded_sam_events(cache_dir: Path) -> tuple[list[dict], str]:
    """Recorded SAM bytes for the overlay concept (demo cache preferred).

    Correction 1: ``sam_stream_air_filter_housing.jsonl`` is the live
    "not found" result and must never be the overlay source. Prefer a
    fresher ``demo_cache/sam_<concept>.jsonl`` when the recorder stored
    one, else the live battery stream (3 objects on this photo).
    """
    demo = cache_dir / f"sam_{OVERLAY_CONCEPT}.jsonl"
    if demo.exists():
        source = demo
    else:
        source = VISION_FIXTURES / SAM_BATTERY_FIXTURE
    key = f"{source}:{source.stat().st_size}"
    cached = _SAM_PARSE_CACHE.get(key)
    if cached is not None:
        return cached
    from mmc.meta import sam as sam_mod

    events = sam_mod.load_stream_fixture(source)
    parsed = sam_mod.result_from_image(
        OVERLAY_CONCEPT, sam_mod.parse_image_events(events)
    )
    out = (events, parsed)
    _SAM_PARSE_CACHE[key] = out
    return out


class DemoVisionAnalyzer:
    """Offline analyzer: recorded Spark primary + recorded SAM bytes.

    The primary replays the live ``spark_components_01.json`` capture; the
    overlay parses recorded SAM bytes with the REAL parser (never the
    not-found air-filter stream); the second opinion is the recorded Spark
    Open capture merged for real (agreement badges, never probabilities).
    """

    def __init__(self, cache: dict, cache_dir: Path) -> None:
        import json as _json

        self._spark_record = _json.loads(
            (VISION_FIXTURES / SPARK_COMPONENTS_FIXTURE).read_text(encoding="utf-8")
        )
        self._sam_events, self._sam_result = _recorded_sam_events(cache_dir)
        second = cache[SECOND_OPINION_FILE]
        self._second_parsed = _json.loads(second["record"]["output_text"])

    def analyze_keyframe(self, image: bytes) -> tuple[dict, list[dict]]:
        from mmc.meta import sam as sam_mod
        from mmc.vision import components as comp_mod

        parsed = comp_mod.parse_components(json.loads(self._spark_record["output_text"]))
        second = comp_mod.parse_components(dict(self._second_parsed))
        merged, _disagreements = comp_mod.merge_opinions(parsed, second)
        overlays = [
            {
                "concept": OVERLAY_CONCEPT,
                "used_concept": OVERLAY_CONCEPT,
                "found": bool(self._sam_result.found),
                "masks": sam_mod.overlays_for_result(self._sam_result),
            }
        ]
        return merged, overlays

    def analyze_instrument(self, image: bytes) -> dict:
        raise NotImplementedError("demo bundles use the keyframe path only")


class DemoUploadAnalyzer:
    """Offline upload analyzer: the recorded live PDF spec ranges ($0)."""

    def extract_pdf(self, data: bytes) -> list[dict]:
        from mmc.meta.spark import _validate_spec_ranges

        record = json.loads((UPLOAD_FIXTURES / "spark_pdf_spec_01.json").read_text())
        return _validate_spec_ranges(record["output_text"])

    def analyze_video(self, data: bytes, filename: str) -> dict:
        raise NotImplementedError("demo bundles carry no video clips")


def synth_wav(scenario_key: str) -> bytes:
    """Deterministic 6 s 44.1 kHz mono16 engine clip (stdlib ``wave``).

    Same recipes as ``tests/test_engine_sound.py`` (seeded hiss for the
    vacuum leak, firing-dropout for the unplugged coil), parametrised for
    the demo vehicle (750 rpm idle, 4 cylinders).
    """
    import math
    import random
    import struct
    import wave
    from io import BytesIO

    from mmc.vision import engine_sound as es_mod

    rate = 44100
    n = int(rate * SOUND_WAV_SECONDS)
    f_fire = es_mod.firing_freq_hz(SOUND_RPM, SOUND_CYLINDERS)
    rnd = random.Random(7)
    kind = "hiss" if scenario_key == "vacuum-leak" else "misfire"

    def harm(t: float, amp: float = 1.0) -> float:
        v = 0.0
        for harmonic, weight in ((1, 0.5), (2, 0.3), (3, 0.2), (4, 0.12)):
            v += weight * math.sin(2 * math.pi * harmonic * f_fire * t)
        return v * amp

    out = [0.0] * n
    for i in range(n):
        t = i / rate
        noise = rnd.random() * 2 - 1
        if kind == "misfire":
            idx = int(t / (1.0 / f_fire))
            amp = 0.15 if (idx % SOUND_CYLINDERS) == (SOUND_CYLINDERS - 1) else 1.0
            out[i] = harm(t, amp) + 0.01 * noise
        else:
            out[i] = harm(t) * 0.6 + 0.25 * noise
    if kind == "hiss":
        out = [out[i] - 0.9 * out[i - 1] if i else 0.0 for i in range(n)]
    peak = max(abs(v) for v in out) or 1.0
    out = [v / peak * 0.9 for v in out]
    buf = BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(
            b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767)) for v in out)
        )
    return buf.getvalue()


def _hex16(value: int) -> str:
    return f"{max(0, min(0xFFFF, int(value))) & 0xFFFF:04X}"


def synth_transcript(sim: str, snapshot: dict) -> bytes:
    """Deterministic ELM327 tx/rx traffic consistent with the snapshot."""
    idle = snapshot["points"]["idle"]
    rpm = int(idle["rpm"])
    ect = float(idle["ect_c"])
    maf = float(idle["maf_gs"])
    trim_idle = float(idle["trim_total_pct"])
    cruise = snapshot["points"].get("cruise2500", {})
    trim_cruise = float(cruise.get("trim_total_pct", trim_idle))
    dtcs = list(snapshot["points"]["idle"].get("dtcs", []))
    misfire = snapshot["points"]["idle"].get("misfire_counts", {})

    def rx_dtcs() -> str:
        if not dtcs:
            return "43 00 00 00 00 00 00"
        cells = []
        for code in dtcs[:3]:
            digits = "".join(c for c in code if c.isdigit())
            # M7 review #14: packed bytes for the actual code — P0303 is
            # "0303", which the app decoder reads back as P0303.
            cells.append((digits[:4] or "0000").ljust(4, "0"))
        return "43 " + " ".join(cells).upper()

    rows: list[dict] = []
    t = 0

    def txrx(tx: str, rx: str, gap: int = 85) -> None:
        nonlocal t
        rows.append({"t": t, "dir": "tx", "data": tx})
        t += 12
        rows.append({"t": t, "dir": "rx", "data": rx})
        t += gap

    for tx, rx in [
        ("ATZ", "ELM327 v1.5\r\r>"),
        ("ATE0", "OK\r\r>"),
        ("ATL0", "OK\r\r>"),
        ("ATS0", "OK\r\r>"),
        ("ATH1", "OK\r\r>"),
        ("ATAT1", "OK\r\r>"),
        ("ATST32", "OK\r\r>"),
        ("ATSP0", "OK\r\r>"),
        ("0100", "41 00 BE 3E B8 11\r\r>"),
        ("ATDPN", "A6\r\r>"),
        ("ATI", "ELM327 v1.5\r\r>"),
        ("ATRV", "14.2V\r\r>"),
    ]:
        txrx(tx, rx)
    txrx("010C", f"41 0C {_hex16(rpm * 4)[:2]} {_hex16(rpm * 4)[2:]}\r\r>")
    txrx("0105", f"41 05 {_hex16(ect + 40)[2:]}\r\r>")
    txrx("0110", f"41 10 {_hex16(maf * 100)[:2]} {_hex16(maf * 100)[2:]}\r\r>")
    txrx("0106", f"41 06 {_hex16((trim_idle + 100) * 128 / 100)[2:]}\r\r>")
    txrx("03", rx_dtcs() + "\r\r>")
    if any(int(v) for v in misfire.values()):
        txrx("0603", "46 03 01 2D 00 00 00 00\r\r>")
    txrx("010C", f"41 0C {_hex16(2500 * 4)[:2]} {_hex16(2500 * 4)[2:]}\r\r>")
    txrx("0106", f"41 06 {_hex16((trim_cruise + 100) * 128 / 100)[2:]}\r\r>")
    lines = [
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for row in rows
    ]
    return b"".join(lines)


def _canonical_bytes(obj: object) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _seed_wonder_cache(data_dir: Path, cache: dict) -> None:
    """Seed the Meta disk cache from the recorded Wonder reviews ($0 replay).

    Each ``wonder_<scenario>.json`` file carries the original call record
    (``request_sha256`` included), so seeding is an exact copy: the live
    ``POST /report`` then hits the cache at $0 and re-validates the
    recorded review. A drifted audit misses the cache and raises
    ``NoCachedResponse`` instead of spending — the generator turns that
    into a loud re-record prompt.
    """
    llm_cache = data_dir / "llm_cache"
    llm_cache.mkdir(parents=True, exist_ok=True)
    for name in WONDER_FILES.values():
        record = cache[name]["record"]
        sha = record["request_sha256"]
        (llm_cache / f"{sha}.json").write_text(
            json.dumps(record, sort_keys=True), encoding="utf-8"
        )


def _drive_to_wonder_context(
    client, scenario_key: str, cache: dict, cache_dir: Path
) -> dict:
    """Drive one scenario up to the finished report + Wonder audit.

    Runs steps 1-6 plus the confirmatory battery check (all deterministic,
    $0), then builds the provisional report and its Wonder audit bundle
    exactly as the normal path does — but stops before any Wonder call or
    ``POST /report``. Returns a context dict with ``session_id``,
    ``report``, ``audit``, ``audit_sha256`` and the drive summary fields.
    The caller (normal bundle path or the recorder) owns the Wonder step.
    """
    import sim_scenarios as sim_mod
    from mmc.meta.client import NoCachedResponse

    sim = SCENARIOS[scenario_key]
    true_hyp = TRUE_HYPOTHESIS[scenario_key]
    summary: dict = {"scenario": scenario_key, "sim": sim}

    def post(path: str, payload: dict | None = None, files: dict | None = None):
        if files is not None:
            response = client.post(path, files=files)
        else:
            response = client.post(path, json=payload or {})
        assert response.status_code == 200, f"POST {path} -> {response.status_code}: {response.text}"
        return response.json()

    def get_state(sid: str, upto: int | None = None) -> dict:
        url = f"/sessions/{sid}/state" + (f"?upto_seq={upto}" if upto else "")
        response = client.get(url)
        assert response.status_code == 200, f"GET {url} -> {response.status_code}: {response.text}"
        return response.json()

    # 1. Session (level + tools ride session_started for the status bar).
    created = post(
        "/sessions",
        {"vehicle": dict(VEHICLE), "user_level": USER_LEVEL, "tools": list(USER_TOOLS), "mode": "live"},
    )
    sid = created["session_id"]

    # 2. Genuine-personality adapter fingerprint.
    # Appended via the registry, not POST /events: the events route only
    # accepts client-postable types (M6 finding #1) and 403s engine-owned
    # types such as adapter_fingerprinted. Same payload, same sequence
    # position, same bytes as the old route call (no WS listeners here,
    # so the broadcast was already a no-op).
    from mmc import sessions as sess_mod

    _demo_session = sess_mod.get_registry().get(sid)
    assert _demo_session is not None
    sess_mod.get_registry().append(
        _demo_session,
        "adapter_fingerprinted",
        {
            "profile": "genuine",
            "protocol": "CAN 11/500",
            "multi_pid": True,
            "headers": True,
            "banner": "ELM327 v1.5",
        },
    )

    # 3. DTC/PID evidence + matching observations from the sim snapshot.
    snapshot = sim_mod.snapshot(sim)
    idle = snapshot["points"]["idle"]
    cruise = snapshot["points"].get("cruise2500", {})

    def upload(data: bytes, filename: str, mime: str) -> dict:
        return post(f"/sessions/{sid}/blobs", files={"file": (filename, data, mime)})

    def add_evidence(etype: str, doc: dict) -> str:
        import uuid as uuid_mod

        from mmc import sessions as sess_ref

        up = upload(_canonical_bytes(doc), f"{etype}.json", "application/json")
        payload = {
            "evidence_id": uuid_mod.uuid4().hex,
            "type": etype,
            "blob_ref": up["blob_ref"],
            "content_hash": up["sha256"],
            "ts": sess_ref._utc_now(),
        }
        post(f"/sessions/{sid}/events", {"type": "evidence_added", "payload": payload})
        return payload["evidence_id"]

    def add_observation(feature: str, value, extractor: str, evidence_ids: list) -> None:
        import uuid as uuid_mod

        from mmc import sessions as sess_ref

        post(
            f"/sessions/{sid}/events",
            {
                "type": "observation_added",
                "payload": {
                    "observation_id": uuid_mod.uuid4().hex,
                    "feature": feature,
                    "value": value,
                    "confidence": 1.0,
                    "extractor": extractor,
                    "evidence_ids": list(evidence_ids),
                    "ts": sess_ref._utc_now(),
                },
            },
        )

    dtc_doc = {"dtcs": list(idle.get("dtcs", [])), "modes": ["03", "07", "0A"]}
    dtc_eid = add_evidence("dtc", dtc_doc)
    add_observation("dtc_readout", {"dtcs": dtc_doc["dtcs"]}, "sim:elm327_v1", [dtc_eid])
    pid_doc = {
        "rpm": idle["rpm"],
        "ect_c": idle["ect_c"],
        "maf_gs": idle["maf_gs"],
        "trim_idle_pct": idle["trim_total_pct"],
        "trim_cruise_pct": cruise.get("trim_total_pct", idle["trim_total_pct"]),
        "iat_c": idle["iat_c"],
    }
    pid_eid = add_evidence("obd_pid", pid_doc)
    add_observation("pid_snapshot_warm_idle", pid_doc, "sim:elm327_v1", [pid_eid])

    # 4. Manual-PDF ranges before anything may cite them.
    pdf_bytes = (UPLOAD_FIXTURES / PDF_FIXTURE).read_bytes()
    pdf_up = upload(pdf_bytes, PDF_FIXTURE, "application/pdf")
    extracted = post(f"/sessions/{sid}/uploads/pdf/extract", {"blob_ref": pdf_up["blob_ref"]})
    spec_ids = {item["spec_id"] for item in extracted["spec_ranges"]}
    assert BATTERY_SPEC_ID in spec_ids, f"recorded PDF lacks {BATTERY_SPEC_ID}: {spec_ids}"

    # 5. Planner loop (rank 1 every step; hiss + convergence bound the loop).
    # The demo vehicle sits outdoors with the engine available, so its
    # checklist is acknowledged once up front (a safety ack, not a test
    # choice — every performed test still equals the planner rank 1).
    # Without it the loop starves on low-value ungated tests and stalls
    # with informative OBD procedures still behind the gate.
    acked: set[str] = set()
    post(f"/sessions/{sid}/events", {"type": "safety_ack", "payload": {"gate": "engine_running_outdoors"}})
    acked.add("engine_running_outdoors")
    performed_loop: list[str] = []
    keyframe_summary: dict | None = None

    def converged(state: dict) -> bool:
        top = state["hypotheses"][0]
        return top["hypothesis_id"] == true_hyp and top["current"] >= 0.8

    def do_keyframe() -> dict:
        image = (IMAGES_DIR / ENGINE_IMAGE).read_bytes()
        up = upload(image, ENGINE_IMAGE, "image/jpeg")
        body = post(f"/sessions/{sid}/vision/keyframe", {"blob_ref": up["blob_ref"]})
        overlays = body.get("overlays", [])
        nonempty = sum(
            1
            for overlay in overlays
            for mask in overlay.get("masks", [])
            if mask.get("svg_path") or mask.get("box")
        )
        if nonempty < 1:
            raise RuntimeError(
                f"keyframe overlay is empty for {scenario_key}: "
                f"{[(o.get('used_concept'), len(o.get('masks', []))) for o in overlays]}"
            )
        return {
            "used_concept": OVERLAY_CONCEPT,
            "nonempty_masks": nonempty,
            "agreements": sorted(
                {o.get("agreement") for o in body.get("components", []) if o.get("agreement")}
            ),
            "evidence_id": body.get("evidence_id"),
        }

    def perform(ref: str, measured: dict, extra: dict | None = None) -> dict:
        body = {"measured": measured}
        if extra:
            body.update(extra)
        return post(f"/sessions/{sid}/tests/{ref}/result", body)

    for _ in range(MAX_LOOP_TESTS + 1):
        state = get_state(sid)
        hiss_done = "engine_sound_screen" in performed_loop
        if performed_loop and converged(state) and hiss_done:
            break
        runnable = state["next_tests"]
        if not runnable:
            raise RuntimeError(f"planner stalled for {scenario_key} after {performed_loop}")
        cand = runnable[0]
        ref = cand["kp_test_ref"]
        assert ref not in performed_loop, f"planner re-ranked {ref} for {scenario_key}"
        gate = cand.get("safety_gate", "none")
        if gate != "none" and gate not in acked:
            post(f"/sessions/{sid}/events", {"type": "safety_ack", "payload": {"gate": gate}})
            acked.add(gate)
            state = get_state(sid)
            cand = state["next_tests"][0]
            ref = cand["kp_test_ref"]
            assert ref not in performed_loop, f"planner re-ranked {ref} for {scenario_key}"
        if ref == "engine_sound_screen":
            wav = synth_wav(scenario_key)
            up = upload(wav, "engine.wav", "audio/wav")
            heard = post(
                f"/sessions/{sid}/vision/engine-sound",
                {"blob_ref": up["blob_ref"], "rpm": SOUND_RPM, "cylinders": SOUND_CYLINDERS},
            )
            outcome = heard["outcome"]
            assert outcome == SOUND_WANTED[scenario_key], (scenario_key, heard)
            want_outcome, _, _ = sim_mod.outcome_for_test(sim, ref, _pack())
            assert outcome == want_outcome, (scenario_key, outcome, want_outcome)
            result = perform(ref, {"outcome": outcome})
        else:
            outcome, measured, _ = sim_mod.outcome_for_test(sim, ref, _pack())
            result = perform(ref, measured)
        assert result["test_result"]["test_id"] == ref, (scenario_key, result)
        performed_loop.append(ref)
        if len(performed_loop) == 2:
            keyframe_summary = do_keyframe()
    else:
        raise RuntimeError(f"loop cap exceeded for {scenario_key}: {performed_loop}")

    state = get_state(sid)
    if not converged(state):
        raise RuntimeError(f"never converged for {scenario_key}: {performed_loop}")
    if "engine_sound_screen" not in performed_loop:
        raise RuntimeError(f"DSP sound screen never ran for {scenario_key}: {performed_loop}")
    if len(performed_loop) > MAX_LOOP_TESTS:
        raise RuntimeError(f"converged too late for {scenario_key}: {performed_loop}")
    assert keyframe_summary, f"keyframe never ran for {scenario_key}"

    # 6. Confirmatory battery check: manual page flips the generic verdict.
    runnable_refs = {t["kp_test_ref"] for t in state["next_tests"]}
    assert "battery_voltage_rest_running" in runnable_refs, (
        f"battery check not runnable for {scenario_key}"
    )
    from mmc.engine import evaluator as evaluator_mod

    flipped = perform(
        "battery_voltage_rest_running", dict(BATTERY_MEASURED), {"spec_id": BATTERY_SPEC_ID}
    )["test_result"]
    assert flipped["expected"]["source"]["kind"] == "manual_page", (scenario_key, flipped)
    assert flipped["outcome"] == "normal", (scenario_key, flipped)
    plain_outcome, _ = evaluator_mod.evaluate(
        _pack().tests["battery_voltage_rest_running"], dict(BATTERY_MEASURED)
    )
    if plain_outcome != "overcharging":
        raise RuntimeError(f"manual flip broke for {scenario_key}: generic now {plain_outcome}")

    # 7. Finished report + Wonder audit (no Wonder call here — the caller
    # owns that step: the normal path re-validates the recorded review at
    # $0, the recorder spends one live call on this exact audit).
    from mmc import sessions as sess_mod
    from mmc.meta import wonder as wonder_mod

    registry = sess_mod.get_registry()
    session = registry.get(sid)
    assert session is not None
    state = sess_mod.compute_state(session)
    provisional = sess_mod.build_report(session, state)
    audit = wonder_mod.build_audit_bundle(provisional, state)
    summary["audit_sha256"] = hashlib.sha256(_canonical_bytes(audit)).hexdigest()
    top = state["hypotheses"][0]
    summary.update(
        {
            "session_id": sid,
            "performed": performed_loop + ["battery_voltage_rest_running"],
            "loop": performed_loop,
            "top_hypothesis": top["hypothesis_id"],
            "top_p": top["current"],
            "keyframe": keyframe_summary,
            "report": provisional,
            "audit": audit,
        }
    )
    return summary


def _drive_session(client, scenario_key: str, cache: dict, cache_dir: Path) -> dict:
    """Drive one scenario through the real routes. Returns a build summary."""
    from mmc.meta.client import MetaClient, NoCachedResponse

    summary = _drive_to_wonder_context(client, scenario_key, cache, cache_dir)
    sid = summary["session_id"]
    audit = summary["audit"]

    def post(path: str, payload: dict | None = None, files: dict | None = None):
        if files is not None:
            response = client.post(path, files=files)
        else:
            response = client.post(path, json=payload or {})
        assert response.status_code == 200, f"POST {path} -> {response.status_code}: {response.text}"
        return response.json()

    def get_state(sid: str, upto: int | None = None) -> dict:
        url = f"/sessions/{sid}/state" + (f"?upto_seq={upto}" if upto else "")
        response = client.get(url)
        assert response.status_code == 200, f"GET {url} -> {response.status_code}: {response.text}"
        return response.json()

    # 8. Wonder provenance: re-validate the recorded review through the real
    # path (cache hit, $0), log its record, then build via the real route.
    from mmc import sessions as sess_mod
    from mmc.meta import wonder as wonder_mod

    registry = sess_mod.get_registry()
    session = registry.get(sid)
    assert session is not None
    cache_client = MetaClient(
        api_key=os.environ.get("MODEL_API_KEY") or "demo-cache-only",
        cache_dir=str(Path(os.environ["DATA_DIR"]) / "llm_cache"),
        replay_cached=True,
        on_call=lambda record: None,
        sleep=lambda s: None,
    )
    try:
        _review, wonder_record = wonder_mod.request_review(audit, cache_client)
    except NoCachedResponse as e:
        raise DemoCacheMissing(
            f"Wonder audit drifted for {scenario_key} ({e}); re-run "
            "scripts/record_demo_cache.py live, then scripts/make_demo_bundle.py"
        ) from e
    registry.append(session, "llm_call", wonder_record)

    built = post(f"/sessions/{sid}/report", {})
    report_body = client.get(f"/reports/{built['report_id']}.json")
    assert report_body.status_code == 200, report_body.text
    report = report_body.json()
    if report.get("wonder_review") is None:
        raise DemoCacheMissing(
            f"Wonder review unavailable for {scenario_key} "
            f"({report.get('wonder_unavailable')}); re-run "
            "scripts/record_demo_cache.py live, then scripts/make_demo_bundle.py"
        )

    final = get_state(sid)
    top = final["hypotheses"][0]
    assert top["hypothesis_id"] == summary["top_hypothesis"], (scenario_key, top)
    summary["report_id"] = report["report_id"]
    return summary


_PACK = None


def _pack():
    global _PACK
    if _PACK is None:
        from mmc.kp.loader import PACKS_DIR, load_pack

        _PACK = load_pack(PACKS_DIR / "ford_gen_v1.yaml")
    return _PACK


def build_bundle(
    scenario_key: str,
    out_path: str | Path,
    *,
    cache_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
    wonder_mode: str = "cache",
) -> dict:
    """Build one judge bundle; returns ``{path, sha256, ...summary}``.

    ``wonder_mode="record"`` is for ``record_demo_cache.py`` only: it
    requires just the non-Wonder cache entries, drives the deterministic
    session to the finished report + Wonder audit, and stops before any
    Wonder call — no ``POST /report``, no bundle file. The returned
    summary carries ``report``, ``audit`` and ``audit_sha256`` for the
    recorder's single live Wonder call. ``wonder_mode="cache"`` (default)
    still requires the full cache and writes the bundle at $0.

    Run with the backend interpreter (``backend/.venv/bin/python``): the
    demo-cache check below is stdlib-only so the missing-cache message
    prints even where the backend deps are absent.
    """
    if scenario_key not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario_key!r} (want {sorted(SCENARIOS)})")
    if os.environ.get("MMC_PUBLIC_MODE", "") == "replay":
        raise RuntimeError("refusing: MMC_PUBLIC_MODE=replay is set; build bundles locally")
    if wonder_mode not in ("cache", "record"):
        raise ValueError(f"unknown wonder_mode {wonder_mode!r} (want 'cache' or 'record')")
    cache_path = Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
    if wonder_mode == "cache":
        cache = require_demo_cache(cache_path)
    else:
        cache = require_record_cache(cache_path)
    from mmc.api import routes_uploads, routes_vision
    from mmc.main import app
    from mmc.meta.budget import get_tracker

    # Scrubbed workspace: a fresh DATA_DIR per build keeps bundles hermetic.
    auto_workspace = data_dir is None
    workspace = Path(data_dir) if data_dir else Path(tempfile.mkdtemp(prefix="mmc-demo-"))
    workspace.mkdir(parents=True, exist_ok=True)
    saved_env = dict(os.environ)
    os.environ["DATA_DIR"] = str(workspace)
    os.environ["REPLAY_USE_CACHED_LLM"] = "true"
    os.environ.setdefault("MODEL_API_KEY", "demo-cache-only")
    os.environ.setdefault("BASETEN_API_KEY", "demo-cache-only")
    meta_before = get_tracker("meta").spend_usd
    baseten_before = get_tracker("baseten").spend_usd
    old_vision = routes_vision.get_analyzer()
    old_upload = routes_uploads.get_analyzer()
    routes_vision.set_analyzer(DemoVisionAnalyzer(cache, cache_path))
    routes_uploads.set_analyzer(DemoUploadAnalyzer())
    try:
        with deterministic():
            from fastapi.testclient import TestClient

            with TestClient(app) as client:
                if wonder_mode == "cache":
                    _seed_wonder_cache(workspace, cache)
                    summary = _drive_session(client, scenario_key, cache, cache_path)
                else:
                    summary = _drive_to_wonder_context(client, scenario_key, cache, cache_path)
                    if get_tracker("meta").spend_usd != meta_before:
                        raise RuntimeError(f"Meta spend moved during {scenario_key} (must stay $0)")
                    if get_tracker("baseten").spend_usd != baseten_before:
                        raise RuntimeError(f"Baseten spend moved during {scenario_key} (must stay $0)")
                    return summary
            from mmc import sessions as sess_mod
            from mmc.replay import mmrec

            session = sess_mod.get_registry().get(summary["session_id"])
            assert session is not None
            events_raw = (workspace / "sessions" / summary["session_id"] / "events.jsonl").read_bytes()
            assert session.store.verify_chain(), f"hash chain broken for {scenario_key}"
            sim = SCENARIOS[scenario_key]
            import sim_scenarios as sim_mod

            transcript = synth_transcript(sim, sim_mod.snapshot(sim))
            # Demo engine-bay photo (CC0, see fixtures/images/SOURCE.txt):
            # the keyframe evidence already references these bytes via its
            # blob_ref/content_hash; embedding them lets bundle replay
            # restore the blob store and serve the photo to the viewer.
            image_bytes = (IMAGES_DIR / ENGINE_IMAGE).read_bytes()
            image_sha = hashlib.sha256(image_bytes).hexdigest()
            keyframe_evidence = [
                e["payload"]
                for e in session.store.iter_replay()
                if e.get("type") == "evidence_added"
                and (e.get("payload") or {}).get("type") == "image"
            ]
            assert keyframe_evidence, f"no keyframe evidence for {scenario_key}"
            assert keyframe_evidence[0].get("content_hash") == image_sha, (
                scenario_key, keyframe_evidence[0].get("content_hash"), image_sha,
            )
            assert keyframe_evidence[0].get("blob_ref") == f"blobs/{image_sha}", (
                scenario_key, keyframe_evidence[0].get("blob_ref"),
            )
            vehicle = dict(session.meta.get("vehicle") or {})
            meta = {
                "role": "demo",
                "scenario": sim,
                "personality": "genuine",
                "label": "simulated vehicle, real AI outputs",
                "user_level": session.meta.get("user_level", USER_LEVEL),
                "tools": list(session.meta.get("tools", [])),
                "vehicle": vehicle,
                "keyframe": {
                    "used_concept": summary["keyframe"]["used_concept"],
                    "nonempty_masks": summary["keyframe"]["nonempty_masks"],
                    "agreements": summary["keyframe"]["agreements"],
                },
                "tests": summary["performed"],
            }
            bundle = mmrec.create(
                {
                    "transcript.jsonl": transcript,
                    "events.jsonl": events_raw,
                    f"blobs/{image_sha}": image_bytes,
                },
                meta,
                date_time=BUNDLE_DATE_TIME,
            )
            mmrec.verify_members(mmrec.loads(bundle), scenario_key)
            out = Path(out_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(bundle)
    finally:
        routes_vision.set_analyzer(old_vision)
        routes_uploads.set_analyzer(old_upload)
        os.environ.clear()
        os.environ.update(saved_env)
        if auto_workspace:
            import shutil

            shutil.rmtree(workspace, ignore_errors=True)
    if get_tracker("meta").spend_usd != meta_before:
        raise RuntimeError(f"Meta spend moved during {scenario_key} (must stay $0)")
    if get_tracker("baseten").spend_usd != baseten_before:
        raise RuntimeError(f"Baseten spend moved during {scenario_key} (must stay $0)")
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    summary.update({"path": str(out), "sha256": sha})
    return summary


def build_wonder_context(
    scenario_key: str,
    *,
    cache_dir: str | Path | None = None,
    data_dir: str | Path | None = None,
) -> dict:
    """Recording-mode entry point: finished report + Wonder audit, $0.

    Thin wrapper over ``build_bundle(..., wonder_mode="record")`` for
    ``record_demo_cache.py`` and its offline test: requires only the
    non-Wonder cache entries and returns the context dict (``report``,
    ``audit``, ``audit_sha256``, ``top_hypothesis``, ...) the single live
    Wonder call runs against. Writes no bundle file; ``out`` is unused.
    """
    return build_bundle(
        scenario_key,
        Path(data_dir) / "throwaway.mmrec" if data_dir else "throwaway.mmrec",
        cache_dir=cache_dir,
        data_dir=data_dir,
        wonder_mode="record",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build M6B judge demo bundles ($0, offline).")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default=None)
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR))
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate to a temp dir and diff sha256 against the committed bundles",
    )
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    args = parser.parse_args(argv)
    try:
        keys = [args.scenario] if args.scenario else sorted(SCENARIOS)
        if args.check:
            failed = False
            with tempfile.TemporaryDirectory(prefix="mmc-demo-check-") as tmp:
                for key in keys:
                    summary = build_bundle(key, Path(tmp) / BUNDLE_FILENAMES[key], cache_dir=args.cache_dir)
                    committed = Path(args.out) / BUNDLE_FILENAMES[key]
                    if not committed.exists():
                        print(f"check MISSING: {committed} (build it and commit the bundle)")
                        failed = True
                        continue
                    want = hashlib.sha256(committed.read_bytes()).hexdigest()
                    status = "OK" if want == summary["sha256"] else "MISMATCH"
                    print(f"check {status}: {key} {summary['sha256']}")
                    if want != summary["sha256"]:
                        failed = True
            return 1 if failed else 0
        for key in keys:
            summary = build_bundle(key, Path(args.out) / BUNDLE_FILENAMES[key], cache_dir=args.cache_dir)
            print(f"built {summary['path']} ({summary['sha256']}) top={summary['top_hypothesis']} "
                  f"p={summary['top_p']:.3f} tests={summary['performed']}")
        return 0
    except DemoCacheMissing as e:
        print(f"make_demo_bundle: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
