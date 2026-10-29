"""Finding #12 (store half): concurrent EventStore.append must not fork the chain.

M5 is the first threaded caller of EventStore.append (keyframe second-opinion
callbacks, video mask delivery). Without a lock, the seq/head
read-modify-write races: two threads read the same seq/head and one event is
lost or the hash chain forks.
"""

from __future__ import annotations

import threading

from mmc.store.event_store import EventStore


def test_concurrent_appends_keep_chain_intact(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    n_threads, per_thread = 8, 25
    barrier = threading.Barrier(n_threads)
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=10)
            for i in range(per_thread):
                store.append("asr_segment", {"worker": n, "i": i})
        except Exception as exc:  # noqa: BLE001 - collected, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    store.close()
    assert not errors
    assert store.verify_chain()
    seqs = [e["seq"] for e in EventStore(tmp_path / "events.jsonl").iter_replay()]
    assert seqs == list(range(1, n_threads * per_thread + 1))
