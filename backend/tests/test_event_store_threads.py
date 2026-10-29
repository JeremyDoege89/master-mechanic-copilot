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


def test_concurrent_first_touch_shares_one_store(tmp_path, monkeypatch):
    """M7 review #9: concurrent first lookups share one EventStore.

    Two simultaneous first requests for a stored session (routine after
    a container restart) must get the same Session/ EventStore object —
    not two stores with independent cached seq/head that fork the chain.
    """
    import os

    from mmc import sessions as sess_mod

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed = sess_mod.SessionRegistry(tmp_path)
    created = seed.create(user_level="L1", tools=[], mode="live", vehicle=None)
    for s in list(seed._sessions.values()):
        try:
            s.store.close()
        except Exception:
            pass

    # A fresh registry over the same root: nothing cached (post-restart).
    registry = sess_mod.SessionRegistry(tmp_path)
    assert os.environ["DATA_DIR"] == str(tmp_path)
    n_threads = 8
    barrier = threading.Barrier(n_threads)
    got: list = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            got.append(registry.get(created.id))
        except Exception as exc:  # noqa: BLE001 - collected, asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(got) == n_threads
    first = got[0]
    assert first is not None
    assert all(s is first for s in got)
    assert all(s.store is first.store for s in got)
