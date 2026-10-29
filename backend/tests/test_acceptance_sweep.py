"""M7 review #4: the acceptance sweep must FAIL on any failing suite.

The sweep used to grep pytest output for the word "passed", so
"1 failed, 19 passed" reported PASS. Every command-based row must now be
decided by the underlying command's exit code (plus its failure summary),
never by grepping output text.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SWEEP = REPO_ROOT / "scripts" / "acceptance_sweep.sh"
LIB = REPO_ROOT / "scripts" / "sweep_lib.sh"


def _read(name: Path) -> str:
    return name.read_text(encoding="utf-8")


def test_sweep_never_greps_for_a_pass_word():
    """Static guard: no output-text grep may decide a PASS row."""
    sweep = _read(SWEEP)
    lib = _read(LIB) if LIB.exists() else ""
    for text, name in ((sweep, "acceptance_sweep.sh"), (lib, "sweep_lib.sh")):
        assert 'grep -q "passed"' not in text, f"{name} still greps for a pass word"
        assert "grep -qE" not in text, f"{name} still greps for a pass word"
        assert 'grep -q "check OK"' not in text, f"{name} still greps for a pass word"


def _run_probe(lib_snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", lib_snippet],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _release_env(extra: dict | None = None) -> dict:
    import os

    env = dict(os.environ)
    env["MMC_KEYSTORE_PATH"] = "/tmp/mmc-sweep-test.jks"
    env["MMC_KEYSTORE_ALIAS"] = "sweep-test"
    env.pop("MMC_KEYSTORE_PASSWORD", None)
    env.pop("MMC_KEY_PASSWORD", None)
    if extra:
        env.update(extra)
    return env


def test_release_check_requires_signing_passwords(tmp_path):
    """M7 review #13: --check fails fast without signing passwords."""
    import subprocess

    script = str(REPO_ROOT / "scripts" / "build_release_apk.sh")
    Path("/tmp/mmc-sweep-test.jks").write_bytes(b"fake-keystore")
    try:
        proc = subprocess.run(
            ["bash", script, "--check"],
            capture_output=True,
            text=True,
            timeout=60,
            env=_release_env(),
        )
        assert proc.returncode != 0
        assert "MMC_KEYSTORE_PASSWORD" in proc.stderr or "MMC_KEY_PASSWORD" in proc.stderr
    finally:
        Path("/tmp/mmc-sweep-test.jks").unlink(missing_ok=True)


def test_release_check_passes_with_passwords_set():
    """Passwords present (even dummies for --check) pass the env gate."""
    import subprocess

    script = str(REPO_ROOT / "scripts" / "build_release_apk.sh")
    Path("/tmp/mmc-sweep-test.jks").write_bytes(b"fake-keystore")
    try:
        proc = subprocess.run(
            ["bash", script, "--check"],
            capture_output=True,
            text=True,
            timeout=60,
            env=_release_env(
                {"MMC_KEYSTORE_PASSWORD": "x", "MMC_KEY_PASSWORD": "x"}
            ),
        )
        assert proc.returncode == 0, proc.stderr
        assert "--check OK" in proc.stdout
    finally:
        Path("/tmp/mmc-sweep-test.jks").unlink(missing_ok=True)


def test_release_script_verifies_signing_for_real():
    """M7 review #13: mandatory apksigner verify, staged+untracked scan."""
    text = (REPO_ROOT / "scripts" / "build_release_apk.sh").read_text()
    assert "MMC_KEYSTORE_PASSWORD" in text
    assert "MMC_KEY_PASSWORD" in text
    verify_lines = [ln for ln in text.splitlines() if "apksigner verify" in ln]
    assert verify_lines, "apksigner verify call missing"
    assert all("|| true" not in ln for ln in verify_lines)
    assert "diff HEAD" in text
    assert "??" in text or "untracked" in text.lower()


def test_failing_command_reports_fail(tmp_path):
    """Feed the sweep's own row helper a failing command: must print FAIL."""
    stub = tmp_path / "failing-pytest"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "tests/test_x.py F"\n'
        'echo "1 failed, 19 passed in 2.00s"\n'
        "exit 1\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    probe = (
        f'FAIL=0\nsource "{LIB}"\n'
        f'run_row "probe failing suite" "{stub}"\n'
        'echo "sweep_exit=$FAIL"\n'
    )
    proc = _run_probe(probe)
    assert proc.returncode == 0, proc.stderr
    assert "| FAIL |" in proc.stdout, proc.stdout
    assert "1 failed, 19 passed" in proc.stdout, proc.stdout
    assert "sweep_exit=1" in proc.stdout, proc.stdout


def test_passing_command_reports_pass(tmp_path):
    """A green command through the same helper must still print PASS."""
    stub = tmp_path / "passing-pytest"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo "20 passed in 2.00s"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    probe = (
        f'FAIL=0\nsource "{LIB}"\n'
        f'run_row "probe passing suite" "{stub}"\n'
        'echo "sweep_exit=$FAIL"\n'
    )
    proc = _run_probe(probe)
    assert proc.returncode == 0, proc.stderr
    assert "| PASS |" in proc.stdout, proc.stdout
    assert "sweep_exit=0" in proc.stdout, proc.stdout
