"""Make tests/fixtures importable (sim_scenarios) for engine tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
