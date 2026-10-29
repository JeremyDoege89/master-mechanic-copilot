"""Safety gates as code (M6 §2, SPEC §8, docs/SAFETY.md).

Checklists, stop-and-see-a-pro triggers and the gated Mode 04 clear-codes
flow are plain code: no prompt text decides any of this. The planner blocks
unacknowledged gates, the result route answers 409 until a ``safety_ack``
event names the gate, and the backend never emits Mode 04 itself — the
confirmed token is handed to the Android driver's ``ClearToken`` for one
driver-side Mode 04.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time

# -- hazard gate checklists ---------------------------------------------------
# Each gate: title + items every user must tick + L1 extras in plainer
# words. Served on gated next_tests/blocked_tests as ``gate_checklist``;
# clients disable Run/Record until every box is ticked, then POST the ack.

GATE_CHECKLISTS: dict[str, dict] = {
    "engine_off": {
        "title": "Engine off and cool",
        "items": [
            "Key off, parking brake on, vehicle in Park or Neutral.",
            "Engine cool to the touch before opening anything or unplugging sensors.",
            "Hands, hair, sleeves and tools clear of belts and fans — electric fans can start with the key on.",
            "Phone mounted or set down; never hold it over a running bay.",
        ],
        "l1_extra": [
            "Wait at least 30 minutes after driving before touching metal parts.",
            "If you are unsure what a part is, photograph it and stop.",
        ],
    },
    "engine_running_outdoors": {
        "title": "Engine running — outdoors only",
        "items": [
            "Outdoors with open air, never in a garage (exhaust gas kills in enclosed spaces, door open or not).",
            "Park or Neutral, parking brake set, wheels chocked.",
            "Phone mounted before the rpm hold; never touch the phone while holding rpm.",
            "Hands, sleeves, rags and tools clear of belts, fans and hot exhaust.",
        ],
        "l1_extra": [
            "Keep children and pets away from the car while it runs.",
            "If the engine suddenly sounds wrong or the light starts flashing, key off.",
        ],
    },
    "fuel_system": {
        "title": "Fuel system — no open lines",
        "items": [
            "Engine off and cool; work outdoors, no smoking, flames or sparks nearby.",
            "Test-port gauge only — do NOT disconnect any fuel line (L1/L2).",
            "Wipe up spills immediately; relieve pressure exactly per the checklist first.",
        ],
        "l1_extra": [
            "If you smell strong fuel at any point, stop and see a professional.",
            "If you do not own a fuel pressure gauge, a shop does this test cheaply.",
        ],
    },
    "hot_coolant": {
        "title": "Cooling system — never open hot",
        "items": [
            "NEVER open a radiator or reservoir cap on a hot engine.",
            "Cooling-system tests need the engine off and fully cool (L1/L2).",
            "Coolant burns and is toxic; wipe spills so pets cannot reach them.",
        ],
        "l1_extra": [
            "Wait until the temperature gauge reads fully cold.",
            "If in doubt, stop — a shop pressure-tests this safely.",
        ],
    },
    "lifting": {
        "title": "Lifting — stands, never a jack alone",
        "items": [
            "Never work under a vehicle supported only by a jack.",
            "Stands on solid, level ground; wheels chocked.",
            "L1 gets no under-vehicle tests — if this gate appears, stop.",
        ],
        "l1_extra": [
            "If the car must be lifted for this test, see a professional.",
        ],
    },
    "hybrid_ev": {
        "title": "Hybrid / high voltage — look, don't touch",
        "items": [
            "Orange cables and their connectors are off-limits to everyone.",
            "No under-hood tests beyond a visual look without HV training (below L3).",
        ],
        "l1_extra": [
            "Hybrids and EVs can shock even with the key off — see a professional.",
        ],
    },
}

NONE_GATE = "none"


def gate_checklist(gate: str) -> dict | None:
    """Checklist for ``gate`` (None for 'none' or unknown gates)."""
    checklist = GATE_CHECKLISTS.get(gate)
    return dict(checklist) if checklist else None


# -- stop-and-see-a-pro triggers (code, never a prompt) ------------------------

PRO_BANNER = (
    "Stop — see a professional now. This session found a condition that is "
    "not safe to keep diagnosing at home (details below). Do not run further "
    "tests. Guidance only — you are responsible for working safely."
)

_SAFETY_FLAG_TEXT = {
    "flashing_mil": "Flashing check-engine light reported (active misfire can destroy the catalyst).",
    "overheating": "Overheating reported (temperature gauge climbing or warning on).",
    "oil_pressure": "Oil-pressure warning reported.",
    "brake_steering": "Brake or steering symptom reported.",
    "fuel_smell": "Fuel smell reported.",
    "smoke": "Smoke or steam reported.",
    "srs": "SRS/airbag fault reported.",
    "hv": "High-voltage concern reported.",
}

OVERHEAT_ECT_C = 110.0


def _dtc_triggers(dtcs: list[str]) -> list[str]:
    triggers = []
    for raw in dtcs:
        code = str(raw).upper().strip()
        if not code:
            continue
        if code.startswith("B"):
            triggers.append(
                f"SRS code {code} (airbag system — never probe SRS parts, see a professional)."
            )
        elif code.startswith("P0A") or code.startswith("P1A"):
            triggers.append(
                f"High-voltage code {code} (hybrid/EV system — orange cables off-limits, see a professional)."
            )
    return triggers


def stop_triggers_for(
    dtcs: list[str] | None = None,
    ect_max_c: float | None = None,
    safety_flags: list[str] | None = None,
) -> list[str]:
    """Trigger descriptions for raw inputs (pure function, unit-friendly)."""
    triggers = _dtc_triggers(list(dtcs or []))
    if ect_max_c is not None and float(ect_max_c) >= OVERHEAT_ECT_C:
        triggers.append(
            f"Coolant temperature {float(ect_max_c):.0f} C at or above {OVERHEAT_ECT_C:.0f} C (overheating)."
        )
    for flag in safety_flags or []:
        key = str(flag).strip().lower()
        if key in _SAFETY_FLAG_TEXT:
            triggers.append(_SAFETY_FLAG_TEXT[key])
        elif key:
            triggers.append(f"Reported safety concern: {flag}.")
    # De-duplicate, keep order.
    seen: set[str] = set()
    unique = []
    for trigger in triggers:
        if trigger not in seen:
            seen.add(trigger)
            unique.append(trigger)
    return unique


def stop_triggers_from_events(
    test_results: list[dict], observations: list[dict]
) -> dict:
    """Fold test_result + observation payloads into the stop-and-see-a-pro state."""
    dtcs: list[str] = []
    ect_max: float | None = None
    for result in test_results:
        measured = result.get("measured", {})
        value = measured.get("value", {}) if isinstance(measured, dict) else {}
        if not isinstance(value, dict):
            continue
        if result.get("test_id") == "dtc_read":
            for code in value.get("dtcs", []) or []:
                dtcs.append(str(code))
        series = value.get("ect_series_c")
        if isinstance(series, list) and series:
            try:
                local_max = max(float(v) for v in series)
            except (TypeError, ValueError):
                continue
            ect_max = local_max if ect_max is None else max(ect_max, local_max)
    flags: list[str] = []
    for obs in observations:
        if str(obs.get("feature", "")).startswith("safety:"):
            value = obs.get("value")
            flags.append(str(value) if not isinstance(value, dict) else str(value.get("flag", value)))
    triggers = stop_triggers_for(dtcs, ect_max, flags)
    return {"triggers": triggers, "banner": PRO_BANNER if triggers else None}


# -- gated clear-codes tokens (Mode 04) ----------------------------------------
# Single-use, 5-minute, session-bound tokens, stored hashed. The plaintext
# token is returned once at request time and once more inside the confirm
# response as the driver's one-shot ClearToken; it is never logged.

TOKEN_TTL_S = 300.0

CONSEQUENCES_TEXT = (
    "Clearing codes erases the stored fault codes AND the freeze-frame "
    "snapshot taken when the fault set, and it resets the emissions "
    "readiness monitors. After clearing, an emissions inspection can FAIL "
    "until enough drive cycles complete to rerun the monitors. Clearing "
    "codes never fixes a fault — it only erases the evidence. A pre-clear "
    "snapshot (codes, freeze frame, Mode 06) is saved in this session first."
)

_TOKENS: dict[str, dict] = {}
_TOKENS_LOCK = threading.Lock()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(session_id: str, now: float | None = None) -> str:
    """Mint a single-use clear-codes token bound to ``session_id``."""
    token = secrets.token_urlsafe(32)
    issued = now if now is not None else time.time()
    with _TOKENS_LOCK:
        _TOKENS[_token_hash(token)] = {
            "session_id": session_id,
            "expires_at": issued + TOKEN_TTL_S,
            "used": False,
        }
    return token


def consume_token(session_id: str, token: str, now: float | None = None) -> str:
    """Validate and burn ``token``; returns its sha256. Raises ValueError."""
    if not token:
        raise ValueError("missing token")
    at = now if now is not None else time.time()
    with _TOKENS_LOCK:
        record = _TOKENS.get(_token_hash(token))
        if record is None:
            raise ValueError("unknown token")
        if record["session_id"] != session_id:
            raise ValueError("token bound to another session")
        if record["used"]:
            raise ValueError("token already used")
        if at > record["expires_at"]:
            raise ValueError("token expired")
        record["used"] = True
    return _token_hash(token)


def backdate_token_for_test(token: str, new_expiry: float) -> None:
    """White-box hook for the expiry test (never used on the live path)."""
    with _TOKENS_LOCK:
        record = _TOKENS.get(_token_hash(token))
        if record is None:
            raise ValueError("unknown token")
        record["expires_at"] = new_expiry
