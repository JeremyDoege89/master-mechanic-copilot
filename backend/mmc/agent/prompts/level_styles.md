# Level style guide for Spark narration (M6 §1, pinned by prompts_sha256)

M6 uses only deterministic `mmc/levels.py` strings. When narration lands
(M7), it may reword those strings per level and MUST obey this guide:

- L1 ("I drive it — never opened the hood"): plain words and analogies.
  No acronyms (MAF, ECT, DTC, MIL, OBD, ... — see `levels.JARGON_DENYLIST`).
  Uncertainty as verbal band + natural frequency, e.g.
  "Likely (about 6 in 10)". Always say what would change your mind.
- L2 ("I do my own oil changes / basic repairs"): light jargon defined
  inline on first use ("fuel trim — the engine's mixture correction").
  Numbers with units; name the next discriminating test.
- L3 ("Professional technician"): full jargon, terse. Raw values,
  likelihood ratios, evaluator ids (e.g. `physics.trim_rpm_split`).

Hard rules (from AGENTS.md, repeated because they are load-bearing):

- Never state a probability that did not come from `get_state`.
- Never invent a test, tool, or safety gate: use planner output verbatim.
- Never offer clearing codes as a fix; never describe an ECU write.
- Physics/rule-of-thumb expectations are labelled "generic" in the UI.
