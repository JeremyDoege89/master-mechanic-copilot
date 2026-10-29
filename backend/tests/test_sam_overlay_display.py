"""M7 §2.3a judge fix: SAM overlay confidences, match + other candidates."""

from __future__ import annotations


def _image_evidence_with_three_masks():
    return {
        "evidence_id": "evbattery1",
        "type": "image",
        "blob_ref": "blobs/" + "a" * 64,
        "content_hash": "a" * 64,
        "overlays": [
            {
                "concept": "battery",
                "used_concept": "battery",
                "found": True,
                "masks": [
                    {"object_id": "o1", "score": 0.61, "box": {"x_min": 0, "y_min": 0, "x_max": 10, "y_max": 10}},
                    {"object_id": "o2", "score": 0.93, "box": {"x_min": 5, "y_min": 5, "x_max": 20, "y_max": 20}},
                    {"object_id": "o3", "score": 0.55, "box": {"x_min": 9, "y_min": 9, "x_max": 30, "y_max": 30}},
                ],
            }
        ],
        "ts": "2026-10-29T00:00:00+00:00",
    }


def test_rank_overlay_masks_highlights_highest():
    from mmc.report.build import rank_overlay_masks

    masks = _image_evidence_with_three_masks()["overlays"][0]["masks"]
    ranked = rank_overlay_masks(masks)
    assert [m["object_id"] for m in ranked] == ["o2", "o1", "o3"]
    assert ranked[0]["rank_label"] == "match"
    assert all(m["rank_label"] == "other candidate" for m in ranked[1:])
    assert ranked[0]["score"] == 0.93


def test_report_carries_match_plus_candidates_without_touching_events(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    from mmc import sessions as sess_mod
    from mmc.report import build as build_mod

    session = sess_mod.get_registry().create(user_level="L1", tools=[], mode="live", vehicle=None)
    evidence = _image_evidence_with_three_masks()
    sess_mod.get_registry().append(session, "evidence_added", evidence)
    sess_mod.get_registry().append(
        session,
        "observation_added",
        {
            "observation_id": "obsbattery1",
            "feature": "visual_component:battery",
            "value": "present",
            "confidence": 0.9,
            "extractor": "spark:components_v1",
            "evidence_ids": [evidence["evidence_id"]],
            "agreement": "corroborated",
        },
    )
    before = [dict(e["payload"]) for e in session.store.iter_replay()]

    state = sess_mod.compute_state(session)
    report = build_mod.build_report(session, state)
    overlays = report["sam_overlays"]
    assert overlays, "image evidence must surface overlay candidates in the report"
    masks = overlays[0]["masks"]
    assert [m["object_id"] for m in masks] == ["o2", "o1", "o3"]
    assert masks[0]["rank_label"] == "match"
    assert all("score" in m for m in masks)
    # No change to recorded data: the event log is byte-identical.
    after = [dict(e["payload"]) for e in session.store.iter_replay()]
    assert after == before
