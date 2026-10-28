# Decisions

Newest first. Each entry: date, decision, why. Add one whenever scope, a dependency or an architecture choice changes.

## 2026-10-28 — M0/M1 implementation decisions

- Canonical JSON is `sort_keys, separators=(",", ":"), ensure_ascii=True`, UTF-8 encoded. ASCII-safe
  form stays encodable for every JSON string value (property test found lone surrogates break
  `ensure_ascii=False`) and is byte-identical across implementations. Hash rule otherwise per plan:
  `payload_sha256 = sha256(canonical(payload))`,
  `hash = sha256(canonical({seq, ts, type, payload_sha256, prev_hash}))`, genesis prev 64 zeros.
- SQLite index is non-durable (`journal_mode=MEMORY`, `synchronous=OFF`) with in-memory next-seq/head.
  The JSONL file is the source of truth and the index rebuilds on open. Per-append `COUNT(*)` made
  10k appends quadratic (64.9 s); now < 1 s. `verify_chain()` returns bool, False on any field tamper,
  deleted/reordered/inserted lines, or unreadable/non-UTF-8 bytes.
- Android pins: Gradle 8.10.2, AGP 8.5.2, Kotlin 2.0.21, compileSdk/targetSdk 35,
  buildToolsVersion 35.0.0 (AGP 8.5 defaults to 34.0.0, not installed), minSdk 26, package
  `dev.dreamz.mmc`. Wrapper jar generated from the cached 8.10.2 distribution; properties use the
  standard https URL.
- `session.json` references `vehicle.json` via `$ref`; tests resolve local refs with a
  `referencing` registry. `backend/uv.lock` is gitignored (deps float in CI).

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
