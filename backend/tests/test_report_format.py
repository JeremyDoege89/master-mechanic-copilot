"""Report value formatting (rendering only): exact strings + JSON stability.

``mmc.report.format`` feeds the HTML template and mirrors the web Report
page. The reproducible report JSON must stay byte-exact, so these tests
pin both sides: the five live-site example strings render exactly, and
``render_html`` leaves the report dict untouched.
"""

from __future__ import annotations

import copy
import json

from mmc.report.format import coerce_value, format_expected, format_measured
from mmc.report.html import render_html


def test_measured_no_codes_stored():
    assert format_measured("dtc_read", {"value": {"dtcs": []}}) == "No codes stored"
    # Trace cells carry the Python repr of the inner value.
    assert format_measured("dtc_read", "{'dtcs': []}") == "No codes stored"
    assert format_measured("dtc_read", {"value": {"dtcs": ["P0303"]}}) == "P0303"


def test_measured_fuel_trims():
    measured = {"value": {"cruise_trim_pct": 4.244416662221763, "idle_trim_pct": 18.0}}
    assert (
        format_measured("trim_idle_vs_2500", measured)
        == "Idle +18.0%, 2,500 rpm +4.2%"
    )
    assert (
        format_measured(
            "trim_idle_vs_2500",
            "{'cruise_trim_pct': 4.244416662221763, 'idle_trim_pct': 18.0}",
        )
        == "Idle +18.0%, 2,500 rpm +4.2%"
    )


def test_measured_battery_voltage():
    measured = {"value": {"engine_on": True, "voltage_v": 14.75}}
    assert (
        format_measured("battery_voltage_rest_running", measured)
        == "14.75 V with engine running"
    )
    assert (
        format_measured(
            "battery_voltage_rest_running",
            "{'engine_on': True, 'voltage_v': 14.75}",
        )
        == "14.75 V with engine running"
    )


def test_expected_book_range():
    expected = {
        "max": 14.8,
        "min": 13.5,
        "outcome": "normal",
        "source": {
            "content_hash": "7ccf",
            "kind": "manual_page",
            "max": 14.8,
            "min": 13.5,
            "ref": "p.1",
            "units": "V",
        },
        "units": "V",
    }
    assert format_expected(expected) == "Book range 13.50-14.80 V (manual p.1)"


def test_measured_engine_sound_outcome():
    assert (
        format_measured("engine_sound_screen", {"value": {"outcome": "vacuum_hiss"}})
        == "Hissing (vacuum hiss)"
    )
    assert (
        format_measured("engine_sound_screen", "{'outcome': 'vacuum_hiss'}")
        == "Hissing (vacuum hiss)"
    )


def test_measured_misfire_counts():
    measured = {"value": {"misfire_counts": {"1": 0, "2": 0, "3": 45, "4": 0}}}
    assert (
        format_measured("mode06_misfire_counts", measured)
        == "Misfire counts: cyl 3 = 45"
    )


def test_expected_outcome_slugs():
    assert format_expected({"outcome": "none", "source": {"kind": "kp"}}) == "No codes"
    assert format_expected("lean_idle_normal_cruise") == "Lean at idle, normal at cruise"


def test_formatters_never_raise_and_do_not_mutate():
    junk = [None, 42, float("nan"), [], {}, "???", "{unclosed", "{'a': object()}"]
    for value in junk:
        assert isinstance(format_measured("dtc_read", value), str)
        assert isinstance(format_expected(value), str)
    payload = {"value": {"dtcs": [], "note": "<b>evil</b>"}}
    snapshot = copy.deepcopy(payload)
    format_measured("dtc_read", payload)
    assert payload == snapshot
    assert coerce_value("{unclosed") == "{unclosed"


def _minimal_report() -> dict:
    return {
        "report_id": "r" * 32,
        "session_id": "s" * 8,
        "conclusion": "Most likely: X.",
        "summary_by_level": {"L1": "a", "L2": "b", "L3": "c"},
        "hypotheses_final": [
            {
                "hypothesis_id": "vacuum_leak_post_maf",
                "label": {"L1": "Air leak", "L2": "Air leak", "L3": "Air leak"},
                "prior": 0.15,
                "current": 0.95,
                "history": [],
                "trace": [
                    {
                        "seq": 1,
                        "evidence_id": "e1",
                        "test_id": "dtc_read",
                        "expected": "none",
                        "measured": "{'dtcs': []}",
                        "prior": 0.15,
                        "posterior": 0.2,
                        "likelihood_ratio": 1.54,
                        "lr_source": "rule.dtc_match",
                        "text_L1": "m",
                        "text_L3": "m3",
                    }
                ],
            }
        ],
        "tests": [],
        "expected_vs_measured": [
            {
                "test_ref": "dtc_read",
                "expected": {"outcome": "none", "source": {"kind": "kp"}},
                "measured": {"value": {"dtcs": []}},
                "outcome": "none",
                "evaluator": "rule.dtc_match",
                "origin": "deterministic",
            }
        ],
        "evidence": [],
        "evidence_index": [],
        "fallback_used": False,
        "second_opinions": [],
        "sam_overlays": [],
        "stop_triggers": [],
        "safety_notes": ["No safety-gated tests were run in this session."],
        "vin_last6": "ABC123",
        "wonder_review": None,
        "wonder_unavailable": "offline",
        "reproducibility": {
            "engine_version": "0.1.0",
            "kp_version": "ford-gen-v1",
            "log_head_hash": "h" * 64,
            "model_pins": {"spark": "muse-spark-1.3"},
            "prompts_sha256": "p" * 64,
        },
    }


def _report_with_overlays() -> dict:
    report = _minimal_report()
    report["sam_overlays"] = [
        {
            "evidence_id": "e" * 32,
            "blob_ref": "blobs/" + "a" * 64,
            "kind": "image",
            "note": "n",
            "overlays": [
                {
                    "concept": "battery",
                    "used_concept": "battery",
                    "found": True,
                    # Recorded SAM stream: no per-mask score.
                    "masks": [
                        {"object_id": "0", "score": None},
                        {"object_id": "1", "score": None},
                        {"object_id": "2", "score": None},
                    ],
                },
                {
                    "concept": "terminal",
                    "used_concept": "terminal",
                    "found": True,
                    "masks": [
                        {"object_id": "o2", "score": 0.93},
                        {"object_id": "o1", "score": 0.61},
                    ],
                },
            ],
            "masks": [],
        }
    ]
    return report


def test_render_html_scoreless_masks_use_ordinals_and_no_confidence_caption():
    report = _report_with_overlays()
    before = json.dumps(report, sort_keys=True)
    html = render_html(report)
    assert json.dumps(report, sort_keys=True) == before
    # Scoreless candidates are ordinals, never a "—" confidence label.
    assert "battery? (1)" in html
    assert "battery? (2)" in html
    assert "battery? (3)" in html
    assert ("battery? —" not in html) and ("battery? -" not in html)
    assert html.count("no per-candidate confidence") == 1
    assert "none is marked as the match" in html
    # Scored candidates keep the score wording.
    assert "0.93" in html
    assert "scores are SAM&#39;s own confidence, not a confirmed match" in html


def test_render_html_uses_human_strings_and_leaves_json_byte_exact():
    report = _minimal_report()
    before = json.dumps(report, sort_keys=True)
    html = render_html(report)
    assert json.dumps(report, sort_keys=True) == before
    assert "No codes stored" in html
    assert "No codes" in html
    # Raw stays available in the collapsed detail (escaped), not as cell text.
    assert "&#39;dtcs&#39;" in html
    assert "{'dtcs': []}" not in html
