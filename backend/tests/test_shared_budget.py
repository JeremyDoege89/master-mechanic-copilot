"""Findings #1 + #12 (budget half): one process-wide tracker per provider.

#1: every MetaClient / bind() must share a single process-wide $120 Meta
tracker (and every SparkOpenClient a single $80 Baseten tracker), so spend
accumulates across requests and the hard stop is reachable.
#12: BudgetTracker.check/add must be safe under concurrent threads.
"""

from __future__ import annotations

import threading

from mmc.meta.budget import BudgetTracker, get_tracker


def test_get_tracker_returns_shared_meta_instance():
    assert get_tracker("meta") is get_tracker("meta")


def test_get_tracker_returns_shared_baseten_instance():
    assert get_tracker("baseten") is get_tracker("baseten")


def test_get_tracker_names_are_distinct_ledgers():
    assert get_tracker("meta") is not get_tracker("baseten")


def test_get_tracker_hard_stops_match_env(monkeypatch):
    import mmc.meta.budget as budget_mod

    budget_mod._TRACKERS.clear()
    try:
        monkeypatch.setenv("BUDGET_USD_HARD_STOP", "120")
        monkeypatch.setenv("BASETEN_BUDGET_USD_HARD_STOP", "80")
        assert get_tracker("meta").hard_stop_usd == 120.0
        assert get_tracker("baseten").hard_stop_usd == 80.0
    finally:
        budget_mod._TRACKERS.clear()


def test_two_default_meta_clients_share_spend(tmp_path):
    from mmc.meta.client import MetaClient

    a = MetaClient(api_key="k", cache_dir=tmp_path / "a", replay_cached=False)
    b = MetaClient(api_key="k", cache_dir=tmp_path / "b", replay_cached=False)
    assert a.budget is b.budget
    a.budget.add(50.0)
    try:
        assert b.budget.spend_usd == 50.0
    finally:
        # Cleanup so the process-wide ledger does not leak into other tests.
        a.budget.spend_usd -= 50.0


def test_two_vision_binds_share_spend(tmp_path, monkeypatch):
    """The finding #1 probe: two successive bind() calls, $50 on the first."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from mmc import sessions as sess_mod
    from mmc.api import routes_vision as rv

    registry = sess_mod.get_registry()
    session = registry.create(user_level="L1", tools=[], mode="live", vehicle=None)
    analyzer = rv.LiveVisionAnalyzer()
    first = analyzer.bind(session)
    second = analyzer.bind(session)
    assert first._client_or_default().budget is second._client_or_default().budget
    first._client_or_default().budget.add(50.0)
    try:
        assert second._client_or_default().budget.spend_usd >= 50.0
    finally:
        second._client_or_default().budget.spend_usd -= 50.0


def test_two_upload_binds_share_spend(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from mmc import sessions as sess_mod
    from mmc.api import routes_uploads as ru

    registry = sess_mod.get_registry()
    session = registry.create(user_level="L1", tools=[], mode="live", vehicle=None)
    analyzer = ru.LiveUploadAnalyzer()
    assert analyzer.bind(session)._client_or_default().budget is (
        analyzer.bind(session)._client_or_default().budget
    )


def test_budget_tracker_thread_safe_add():
    tracker = BudgetTracker(hard_stop_usd=10_000_000.0)
    threads = [
        threading.Thread(target=lambda: [tracker.add(1.0) for _ in range(100)])
        for _ in range(16)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert tracker.spend_usd == 16 * 100 * 1.0
