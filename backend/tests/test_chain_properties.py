"""Property tests: random chains always verify; any single content-byte mutation fails.

Our JSONL writer emits canonical JSON with no insignificant whitespace, so
every byte of a line except the trailing newline is covered by the chain
hashes: mutating any one of them must break verification.
"""

import hashlib
import os
import random
import tempfile

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mmc.store.event_store import EventStore

TYPES = [
    "session_started",
    "adapter_fingerprinted",
    "evidence_added",
    "observation_added",
    "test_requested",
    "safety_ack",
    "test_performed",
    "test_result",
    "hypothesis_proposed",
    "posterior_updated",
    "llm_call",
    "sam_call",
    "asr_segment",
    "codes_clear_requested",
    "codes_cleared",
    "report_built",
]

payloads = st.dictionaries(
    st.text(min_size=1, max_size=12),
    st.one_of(st.integers(), st.floats(allow_nan=False, allow_infinity=False), st.text(max_size=20)),
    max_size=5,
)

event_lists = st.lists(st.tuples(st.sampled_from(TYPES), payloads), min_size=1, max_size=30)


@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
@given(event_lists)
def test_random_sequences_always_verify(events):
    with tempfile.TemporaryDirectory() as d:
        store = EventStore(os.path.join(d, "events.jsonl"))
        for type, payload in events:
            store.append(type, dict(payload))
        try:
            assert store.verify_chain()
            assert len(store) == len(events)
            assert [e["seq"] for e in store.iter_replay()] == list(range(1, len(events) + 1))
        finally:
            store.close()


@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
@given(event_lists)
def test_single_byte_mutation_fails_verification(events):
    with tempfile.TemporaryDirectory() as d:
        store = EventStore(os.path.join(d, "events.jsonl"))
        for type, payload in events:
            store.append(type, dict(payload))
        try:
            raw = store.path.read_bytes()
            assert raw.endswith(b"\n")
            # Every byte except the trailing newline is hash-covered content.
            seed = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")
            pos = random.Random(seed).randrange(len(raw) - 1)
            mutated = bytearray(raw)
            mutated[pos] ^= 0x01
            store.path.write_bytes(bytes(mutated))
            assert not store.verify_chain()
        finally:
            store.close()
