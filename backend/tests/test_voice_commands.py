"""M7 §1 voice grammar: every table row + number words + unknown."""

from mmc import voice_commands as vc


def test_next_test_has_no_mutation_payload():
    intent = vc.parse("next test")
    assert intent == {"intent": "next_test"}


def test_log_volts_digits():
    intent = vc.parse("log 12.4 volts")
    assert intent["intent"] == "log_voltage"
    assert intent["voltage_v"] == 12.4
    assert intent["test_ref"] == "battery_voltage_rest_running"


def test_log_volts_number_words():
    intent = vc.parse("log twelve point four volts")
    assert intent["intent"] == "log_voltage"
    assert abs(intent["voltage_v"] - 12.4) < 1e-9


def test_log_idle_trim_plus_eighteen():
    intent = vc.parse("log idle trim plus eighteen")
    assert intent["intent"] == "log_trim"
    assert intent["test_ref"] == "trim_idle_vs_2500"
    assert intent["part"] == "idle"
    assert intent["value"] == 18.0


def test_trim_two_step_staging_needs_both_halves():
    first = vc.stage_trim(None, vc.parse("log idle trim plus eighteen"))
    assert first["idle_trim_pct"] == 18.0
    assert first["complete"] is False
    second = vc.stage_trim(first, vc.parse("log cruise trim plus four"))
    assert second["cruise_trim_pct"] == 4.0
    assert second["complete"] is True


def test_repeat_and_risk_and_levels():
    assert vc.parse("repeat")["intent"] == "repeat"
    assert vc.parse("read it again")["intent"] == "repeat"
    assert vc.parse("what's the risk")["intent"] == "risk"
    assert vc.parse("level one") == {"intent": "level", "level": "L1"}
    assert vc.parse("level two") == {"intent": "level", "level": "L2"}
    assert vc.parse("level three") == {"intent": "level", "level": "L3"}


def test_confirm_cancel_and_unknown():
    assert vc.parse("yes")["intent"] == "confirm"
    assert vc.parse("confirm")["intent"] == "confirm"
    assert vc.parse("no")["intent"] == "cancel"
    assert vc.parse("cancel")["intent"] == "cancel"
    unknown = vc.parse("clear the codes please")
    assert unknown["intent"] == "unknown"
    assert vc.parse("")["intent"] == "unknown"


def test_number_words_minus_and_digits():
    assert vc.parse_number(["minus", "eighteen"]) == -18.0
    assert vc.parse_number(["twelve", "point", "four"]) == 12.4
    assert vc.parse_number(["12.4"]) == 12.4
    assert vc.parse_number([]) is None


def test_asr_fixtures_stay_provisional():
    import json
    from pathlib import Path

    base = Path(__file__).parent / "fixtures" / "asr"
    config = json.loads((base / "session_config.json").read_text())
    assert config["provisional"] is True
    for line in (base / "stream_shapes.jsonl").read_text().splitlines():
        assert json.loads(line)["provisional"] is True
