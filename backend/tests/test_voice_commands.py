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


def test_rpm_tokens_never_become_the_value():
    """M7 review #5: 2500/rpm is the condition, never the measurement."""
    cruise = vc.parse("log cruise trim plus four at 2500")
    assert cruise["intent"] == "log_trim"
    assert cruise["part"] == "cruise"
    assert cruise["value"] == 4.0
    volts = vc.parse("log voltage twelve volts and 2500 rpm")
    assert volts["intent"] == "log_voltage"
    assert volts["voltage_v"] == 12.0


def test_voice_corpus_parity():
    """Shared Python/Kotlin grammar corpus (M7 review #5)."""
    import json
    from pathlib import Path

    corpus = json.loads(
        (Path(__file__).parent / "fixtures" / "voice_corpus.json").read_text()
    )
    assert corpus["cases"], "corpus must not be empty"
    for case in corpus["cases"]:
        intent = vc.parse(case["transcript"])
        expect = case["expect"]
        assert intent["intent"] == expect["intent"], case["transcript"]
        for key in ("value", "voltage_v", "part", "level"):
            if key in expect:
                assert intent.get(key) == expect[key], (case["transcript"], key)


def test_android_corpus_mirror_matches():
    """The Android resource copy of the corpus must equal the canonical one."""
    from pathlib import Path

    canonical = Path(__file__).parent / "fixtures" / "voice_corpus.json"
    mirror = (
        canonical.parents[3]
        / "android"
        / "app"
        / "src"
        / "test"
        / "resources"
        / "voice_corpus.json"
    )
    assert mirror.exists(), f"{mirror} missing: copy backend/tests/fixtures/voice_corpus.json"
    assert mirror.read_bytes() == canonical.read_bytes(), "corpus mirror drifted"


def test_asr_fixtures_are_live():
    """Live spike fixtures: provisional flipped to false, still key-free."""
    import json
    from pathlib import Path

    from mmc.meta import voice as voice_mod

    base = Path(__file__).parent / "fixtures" / "asr"
    config = json.loads((base / "session_config.json").read_text())
    assert config["provisional"] is False
    # Documented handshake shape, redacted: no credential field names or
    # values anywhere in the committed fixtures.
    assert set(config) - {"captured", "provisional"} == set(voice_mod.session_config())
    stub = json.loads((base / "pcm_stub.json").read_text())
    assert stub["provisional"] is False
    assert stub["encoding"] == "PCM_24KHZ" and stub["sample_rate_hz"] == 24000
    for name in ("session_config.json", "pcm_stub.json", "stream_shapes.jsonl"):
        text = (base / name).read_text()
        assert "Bearer " not in text
        assert "authorization" not in text
        assert "accessToken" not in text
    transcripts: list[dict] = []
    for line in (base / "stream_shapes.jsonl").read_text().splitlines():
        shape = json.loads(line)
        assert shape["provisional"] is False
        if shape["type"] == "transcript":
            transcripts.append(json.loads(shape["preview"]))
    # The owner spike's real-speech turn, pinned: partials build up, one final.
    partials = [m["transcript"] for m in transcripts if m.get("final") is not True]
    finals = [m["transcript"] for m in transcripts if m.get("final") is True]
    assert partials == ["one,", "one, three,", "one, three, five."]
    assert finals == ["one, three, five."]
