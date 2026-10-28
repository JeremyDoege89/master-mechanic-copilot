"""Physics rule-of-thumb unit tests (M3 step 1)."""

from mmc.engine import physics


def test_maf_5l_v8_idle():
    # SPEC §5 worked example: 5.0 L V8 at 650 rpm, MAP 30/100 kPa ≈ 4–5 g/s
    # (idle VE ~0.5 under throttled conditions).
    got = physics.maf_expected(650, 5.0, map_kpa=30.0, baro_kpa=100.0, ve=0.5)
    assert 4.0 <= got <= 5.5


def test_maf_matches_sim_scaling():
    # Sim uses (rpm/120)*disp*0.8*1.18*0.3 at idle MAP fraction 0.3.
    assert physics.maf_expected(750, 2.0) == abs(physics.maf_expected(750, 2.0))
    got = physics.maf_expected(750, 2.0, map_kpa=30.0, baro_kpa=100.0)
    assert got == (750 / 120.0) * 2.0 * 0.8 * 1.18 * 0.3


def test_maf_zero_rpm_is_zero():
    assert physics.maf_expected(0, 2.0) == 0.0
    assert physics.maf_expected(-100, 2.0) == 0.0


def test_trim_leak_signature():
    assert physics.trim_rpm_split(18.0, 4.0) == "lean_idle_normal_cruise"


def test_trim_fuel_signature():
    assert physics.trim_rpm_split(16.0, 15.0) == "lean_both"


def test_trim_normal_band():
    assert physics.trim_rpm_split(1.5, 1.0) == "normal"
    assert physics.trim_rpm_split(9.9, 9.9) == "normal"


def test_trim_rich():
    assert physics.trim_rpm_split(-12.0, -2.0) == "rich"


def test_o2_switching_healthy():
    samples = [0.1, 0.8, 0.15, 0.85, 0.12, 0.9, 0.11, 0.82]
    assert physics.o2_switching_ok(samples) is True


def test_o2_lazy_sensor_fails():
    assert physics.o2_switching_ok([0.44, 0.46, 0.44, 0.45, 0.46]) is False
    assert physics.o2_switching_ok([0.8]) is False
    assert physics.o2_switching_ok([]) is False


def test_ect_plateau_normal():
    assert physics.ect_plateau_ok([20.0, 60.0, 85.0, 93.0, 94.0, 93.6]) is True


def test_ect_plateau_stuck_open():
    assert physics.ect_plateau_ok([20.0, 50.0, 65.0, 68.0, 69.0, 69.1]) is False
    assert physics.ect_plateau_ok([]) is False


def test_voltage_rest_and_running():
    assert physics.voltage_ok(12.5, engine_on=False) is True
    assert physics.voltage_ok(12.0, engine_on=False) is False
    assert physics.voltage_ok(14.2, engine_on=True) is True
    assert physics.voltage_ok(12.1, engine_on=True) is False
