# Master Mechanic Copilot

A master mechanic in your pocket that shows its work. Live OBD2 data, the phone camera and your voice feed a
deterministic Bayesian diagnostic engine that ranks the next best test for your skill and tools, explains why each
suspect rose or fell, and produces a hash-chained, replayable report.

Built for the Meta AI Developer Hackathon (Indie track) with Muse Code, Muse Spark 1.3, SAM 3.1 and Muse Voice
Transcribe.

Meta model paths: Muse Spark 1.3 / SAM 3.1 / Voice Transcribe via the Meta Model API (`https://api.meta.ai/v1`,
Standard IDs from env), plus Muse Spark Open (`meta-models/muse-spark-open-1026`) via Baseten
(`https://inference.baseten.co/v1`) for second-opinion vision and text/image fallback (own $80 ledger; audio
unavailable — engine sound is stdlib DSP, never an LLM call).

**Status:** in development (Oct 26 – Nov 4, 2026). Setup, run and judge instructions will land here and in
`docs/JUDGES.md` as the build progresses.

## Docs

- `AGENTS.md`: build rules, schemas, API contracts and milestones (what Muse Code follows)
- `docs/SPEC.md`: product and engineering spec
- `docs/PLAN.md`: schedule and checklist
- `docs/RULES.md`: hackathon rules status
- `docs/DECISIONS.md`: decision log

## Prior experience

The author has prior hands-on experience with OBD2/CAN diagnostics and Android development. No code from earlier
projects is included; everything in this repository was written during the event.
