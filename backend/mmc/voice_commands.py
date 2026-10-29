"""M7 deterministic voice grammar (plan .agents/plans/M7.md §1).

No LLM: lowercase/punct-normalise, number-word parser, typed intents.
Android renders the pending card; the backend re-parses on confirm (single
source of truth). ``asr_segment`` alone is audit only — nothing becomes
evidence/test_result until the confirm step calls the existing route.
"""

from __future__ import annotations

import re
import string

NUMBER_WORDS: dict[str, float] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}

LEVEL_WORDS = {
    "one": "L1", "two": "L2", "three": "L3",
    "1": "L1", "2": "L2", "3": "L3",
}

TRIM_TEST_REF = "trim_idle_vs_2500"
VOLTAGE_TEST_REF = "battery_voltage_rest_running"


def normalize(text: str) -> str:
    """Lowercase, expand punctuation to spaces (keep . + - for numbers)."""
    text = (text or "").lower()
    keep = set("+-.0123456789")
    out = []
    for ch in text:
        if ch.isalnum() or ch in keep or ch.isspace():
            out.append(ch)
        else:
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


def parse_number(tokens: list[str]) -> float | None:
    """Parse digits or number words incl. 'twelve point four', 'plus eighteen'."""
    if not tokens:
        return None
    # Direct float anywhere ("12.4", "+18", "-40").
    for tok in tokens:
        try:
            return float(tok)
        except ValueError:
            pass
    # Sign words.
    sign = 1.0
    words = list(tokens)
    if words and words[0] in ("plus", "minus", "negative", "+"):
        if words[0] in ("minus", "negative", "-"):
            sign = -1.0
        words = words[1:]
    if not words:
        return None
    # "twelve point four" / "twelve dot four".
    if "point" in words or "dot" in words:
        sep = "point" if "point" in words else "dot"
        idx = words.index(sep)
        int_part = _words_to_int(words[:idx])
        frac_words = words[idx + 1 :]
        if int_part is None or not frac_words:
            return None
        frac_digits = ""
        for w in frac_words:
            v = _words_to_int([w])
            if v is None or v > 9:
                return None
            frac_digits += str(int(v))
        try:
            return sign * float(f"{int(int_part)}.{frac_digits}")
        except ValueError:
            return None
    value = _words_to_int(words)
    return sign * float(value) if value is not None else None


def _words_to_int(words: list[str]) -> int | None:
    total: int | None = None
    current = 0
    seen = False
    for w in words:
        if w in ("and",):
            continue
        v = NUMBER_WORDS.get(w)
        if v is None:
            try:
                v = int(w)
            except ValueError:
                try:
                    # "12" inside words; floats handled by caller.
                    v = int(float(w))
                except ValueError:
                    return None
        seen = True
        current += int(v)
    if not seen:
        return None
    total = (total or 0) + current
    return total


_NUMBER_VOCAB = set(NUMBER_WORDS) | {
    "point", "dot", "plus", "minus", "negative", "+", "-", "and",
}


def _number_tokens(tokens: list[str]) -> list[str]:
    """Keep only tokens that can form a spoken number."""
    out = []
    for tok in tokens:
        if tok in _NUMBER_VOCAB:
            out.append(tok)
            continue
        try:
            float(tok)
            out.append(tok)
        except ValueError:
            continue
    return out


def _has(tokens: list[str], *phrases: str) -> bool:
    joined = " ".join(tokens)
    return any(p in joined for p in phrases)


def parse(text: str, focused_test_ref: str | None = None) -> dict:
    """Parse one utterance to a typed intent dict (never raises)."""
    raw = text or ""
    norm = normalize(raw)
    tokens = norm.split()

    if not tokens:
        return {"intent": "unknown", "text": raw}

    joined = " ".join(tokens)

    # Confirm / cancel (voice yes/confirm or tap; no/cancel discards).
    if joined in ("yes", "yeah", "yep", "confirm", "confirmed", "do it", "log it"):
        return {"intent": "confirm"}
    if joined in ("no", "nope", "cancel", "discard", "never mind", "nevermind", "stop"):
        return {"intent": "cancel"}

    # Next test: focus rank-1, no mutation.
    if "next test" in joined:
        return {"intent": "next_test"}

    # Repeat: TTS re-reads the last spoken card, no backend call.
    if joined in ("repeat", "read it again", "say again", "repeat that", "what"):
        return {"intent": "repeat"}
    if _has(tokens, "read it again", "repeat"):
        return {"intent": "repeat"}

    # Risk: TTS reads rank-1 gate checklist title + cost.
    if _has(tokens, "whats the risk", "what is the risk", "what the risk", "risk"):
        # "risk" alone could be ambiguous but the table owns it.
        if "risk" in tokens:
            return {"intent": "risk"}

    # Level switch: client render-level only, session user_level unchanged.
    if "level" in tokens:
        for tok in tokens:
            if tok in LEVEL_WORDS:
                return {"intent": "level", "level": LEVEL_WORDS[tok]}
        # "level one" with punctuation variants already normalised.
        return {"intent": "unknown", "text": raw}

    # Trim staging: "log idle trim plus eighteen" / cruise variant.
    if "trim" in tokens and "log" in tokens:
        part: str | None = None
        if "idle" in tokens:
            part = "idle"
        elif "cruise" in tokens or "2500" in joined or "revved" in tokens:
            part = "cruise"
        value = parse_number([t for t in tokens if t not in ("log", "idle", "cruise", "trim", "trims")])
        if value is None:
            # Fall back: number words may sit before "trim".
            value = parse_number(tokens)
        if value is not None:
            return {
                "intent": "log_trim",
                "test_ref": TRIM_TEST_REF,
                "part": part,
                "value": float(value),
            }
        return {"intent": "unknown", "text": raw}

    # Voltage: "log 12.4 volts".
    if "volt" in joined and "log" in tokens:
        value = parse_number(_number_tokens(tokens))
        if value is not None:
            return {
                "intent": "log_voltage",
                "test_ref": focused_test_ref or VOLTAGE_TEST_REF,
                "voltage_v": float(value),
            }
        return {"intent": "unknown", "text": raw}

    return {"intent": "unknown", "text": raw}


def stage_trim(existing: dict | None, intent: dict) -> dict:
    """Fold a log_trim intent into {idle_trim_pct?, cruise_trim_pct?, complete}.

    Confirm stays disabled until both halves are present; a part=None value
    fills the first missing half (idle first).
    """
    staged = dict(existing or {})
    if (intent or {}).get("intent") != "log_trim":
        staged["complete"] = "idle_trim_pct" in staged and "cruise_trim_pct" in staged
        return staged
    part = intent.get("part")
    value = float(intent["value"])
    if part == "idle":
        staged["idle_trim_pct"] = value
    elif part == "cruise":
        staged["cruise_trim_pct"] = value
    else:
        if "idle_trim_pct" not in staged:
            staged["idle_trim_pct"] = value
        else:
            staged["cruise_trim_pct"] = value
    staged["complete"] = "idle_trim_pct" in staged and "cruise_trim_pct" in staged
    return staged
