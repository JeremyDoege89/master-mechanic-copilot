# Decisions

Newest first. Each entry: date, decision, why. Add one whenever scope, a dependency or an architecture choice changes.

## 2026-10-28 — Full scope restored (supersedes the cut below)

The owner chose to make up the two-day slip with three all-nighters instead of cutting. Every feature in AGENTS.md
ships. The former cuts are now only a fallback order if a day's exit criteria are missed. See `docs/PLAN.md`.

## 2026-10-28 — Cut Mode 22, BLE and Wi-Fi transports up front (superseded)

Setup slipped two days. Classic RFCOMM covers the demo adapters and Fords; the Sim and Replay transports cover the
judges. Muse Wonder review, SAM video tracking and PDF upload are stretch goals. See `docs/PLAN.md`.

## 2026-10-26 — Native Android + web replay viewer + Python backend

Android is the only platform where cheap Bluetooth Classic ELM327 adapters work; judges will have no adapter or
car, so a deployed web Replay & Report viewer is the judge path. iOS waits for a Kotlin Multiplatform core after
the hackathon. Full reasoning: `docs/research/2026-10-26-platform-verdict-and-plan.md` §1.

## 2026-10-26 — The LLM proposes, deterministic code decides

Probabilities change only in `backend/mmc/engine/`. Muse Spark turns messy input into typed Observations and
writes explanations, but it never sets a posterior. This makes every conclusion auditable and replay deterministic.
