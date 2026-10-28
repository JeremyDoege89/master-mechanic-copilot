"""M1 acceptance: tamper detection for every field + structural attacks, perf, blobs."""

import json
import time

import pytest

from mmc.store.blobs import get, put
from mmc.store.event_store import GENESIS_PREV_HASH, EventStore

TYPES = ["session_started", "evidence_added", "observation_added"]


def make_store(tmp_path, n=3):
    store = EventStore(tmp_path / "events.jsonl")
    for i in range(n):
        store.append(TYPES[i % len(TYPES)], {"i": i, "note": f"event-{i}"})
    assert store.verify_chain()
    return store


def rewrite_line(store, seq, mutate):
    lines = store.path.read_text().splitlines()
    event = json.loads(lines[seq - 1])
    mutate(event)
    lines[seq - 1] = json.dumps(event, sort_keys=True, separators=(",", ":"))
    store.path.write_text("\n".join(lines) + "\n")


def test_clean_chain_verifies(tmp_path):
    assert make_store(tmp_path).verify_chain()


def test_genesis_prev_hash_is_zeros(tmp_path):
    store = make_store(tmp_path, n=1)
    first = json.loads(store.path.read_text().splitlines()[0])
    assert first["prev_hash"] == GENESIS_PREV_HASH == "0" * 64
    assert first["seq"] == 1


def test_tamper_seq_detected(tmp_path):
    store = make_store(tmp_path)
    rewrite_line(store, 2, lambda e: e.update(seq=99))
    assert not store.verify_chain()


def test_tamper_ts_detected(tmp_path):
    store = make_store(tmp_path)
    rewrite_line(store, 2, lambda e: e.update(ts="2001-01-01T00:00:00+00:00"))
    assert not store.verify_chain()


def test_tamper_type_detected(tmp_path):
    store = make_store(tmp_path)
    rewrite_line(store, 2, lambda e: e.update(type="report_built"))
    assert not store.verify_chain()


def test_tamper_payload_detected(tmp_path):
    store = make_store(tmp_path)

    def mutate(e):
        e["payload"] = {"i": 1, "note": "forged"}

    rewrite_line(store, 2, mutate)
    assert not store.verify_chain()


def test_tamper_prev_hash_detected(tmp_path):
    store = make_store(tmp_path)
    rewrite_line(store, 2, lambda e: e.update(prev_hash="1" * 64))
    assert not store.verify_chain()


def test_tamper_hash_detected(tmp_path):
    store = make_store(tmp_path)
    rewrite_line(store, 2, lambda e: e.update(hash="2" * 64))
    assert not store.verify_chain()


def test_raw_byte_flip_detected(tmp_path):
    store = make_store(tmp_path)
    raw = bytearray(store.path.read_bytes())
    raw[20] ^= 0x01
    store.path.write_bytes(bytes(raw))
    assert not store.verify_chain()


def test_float_last_digit_mutation_detected(tmp_path):
    """A one-digit float change that parses to the same double must fail.

    Regression test: 6.3373105101272424e+16 and ...425e+16 are the same
    IEEE-754 double, so hash recomputation alone still passes; only the
    raw-bytes-vs-canonical check catches it.
    """
    store = EventStore(tmp_path / "events.jsonl")
    value = 6.3373105101272424e16
    store.append("observation_added", {"x": value})
    assert store.verify_chain()
    raw = store.path.read_text(encoding="utf-8")
    old = "6.3373105101272424e+16"
    new = "6.3373105101272425e+16"
    assert old in raw
    assert float(old) == float(new) == value
    store.path.write_text(raw.replace(old, new), encoding="utf-8")
    mutated = json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])
    assert mutated["payload"] == {"x": value}
    assert store.verify_chain() is False


def test_non_ascii_payload_mutation_returns_false(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    store.append("observation_added", {"note": "héllo wörld"})
    raw = bytearray(store.path.read_bytes())
    pos = raw.find(b"\\u00e9")  # 'é' is stored as an ASCII \uXXXX escape
    assert pos != -1
    raw[pos + 2] ^= 0x01
    store.path.write_bytes(bytes(raw))
    assert store.verify_chain() is False


def test_deleted_line_detected(tmp_path):
    store = make_store(tmp_path)
    lines = store.path.read_text().splitlines()
    del lines[1]
    store.path.write_text("\n".join(lines) + "\n")
    assert not store.verify_chain()


def test_reordered_lines_detected(tmp_path):
    store = make_store(tmp_path)
    lines = store.path.read_text().splitlines()
    lines[0], lines[1] = lines[1], lines[0]
    store.path.write_text("\n".join(lines) + "\n")
    assert not store.verify_chain()


def test_inserted_line_detected(tmp_path):
    store = make_store(tmp_path)
    lines = store.path.read_text().splitlines()
    lines.insert(1, lines[1])
    store.path.write_text("\n".join(lines) + "\n")
    assert not store.verify_chain()


def test_truncated_file_detected(tmp_path):
    store = make_store(tmp_path, n=3)
    raw = store.path.read_bytes()
    store.path.write_bytes(raw[: len(raw) // 2])
    assert not store.verify_chain()


def test_replay_iterator_yields_in_order(tmp_path):
    store = make_store(tmp_path, n=5)
    events = list(store.iter_replay())
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5]
    assert all(store.verify_chain() for _ in [0])


def test_unknown_event_type_rejected(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    with pytest.raises(ValueError):
        store.append("ecu_write", {"mode": "2E"})


def test_append_10k_perf(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    start = time.perf_counter()
    for i in range(10_000):
        store.append("observation_added", {"i": i})
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"10k appends took {elapsed:.2f}s"
    assert store.verify_chain()
    assert len(store) == 10_000


def test_blobs_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ref, digest = put(b"\x89PNG-hello")
    assert get(ref) == b"\x89PNG-hello"
    ref2, _ = put(b"\x89PNG-hello")
    assert ref2 == ref
