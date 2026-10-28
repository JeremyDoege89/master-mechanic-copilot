# Hackathon rules: status and open questions

Copied from the 2026-10-26 research (section 2). Nothing here comes from the Official Rules text yet. Confirm each row in Discord `#rules` / `#faqs`, then mark it **Confirmed** with the date and source.

**Status (as of Oct 26, ~1 PM CT):**
- Both official event pages still say "Official Rules will be published before the Hackathon begins on October 26, 2026." [meta](https://dev.meta.ai/events/global-hackathon) [cerebralvalley](https://cerebralvalley.ai/e/meta-ai-developer-hackathon)
- The participant guide (`/details`) and waiver pages exist, but neither I nor the subagent could open them.
- Everything below marked **secondhand** comes from participants who read the guide or Discord, or from a blog that quoted the pre-event waiver. Verify it in Discord `#rules` and `#faqs` today.

| Topic | Best available info | Source quality | Impact on build |
|---|---|---|---|
| **Pre-existing code** | "Only work done during the event counts; the demo must show only that work." Discord `#rules`: "anything built from 26 Oct to 4 Nov counts… or the project is disqualified." [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand (participant README, read Oct 26) | **Do not import your existing ECU/UDS or manual-extractor code.** Re-implement with Muse Code during the event and say in the README that you have prior domain experience. Open-source libraries are reportedly fine if you follow their licences. |
| **Non-Meta models** | "Must use Meta models via Meta Model API or the open-weight models." Nothing found explicitly bans other models alongside them. [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand | Make every AI call a Meta model. Use no third-party LLMs, so there is nothing to dispute. |
| **Team size** | Indie: "solo or in teams of up to four people" [reconscribe](https://reconscribe.com/meta-global-ai-developer-hackathon-2026/) | Secondhand (pre-event blog) | Solo is fine |
| **Demo video** | "Three-minute demo video required." [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand | Script to 2:45 |
| **Submission fields** | "public repo, a working demo video link and every team member added" [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand (Discord) | Public GitHub repo from Day 1, with secrets excluded |
| **Judges running it** | Not stated. The official page says Indie is "Judged on creativity, technical ambition, and whether it works." [meta](https://dev.meta.ai/events/global-hackathon) | Primary (event page) | Provide the web Replay URL + APK + simulator so "whether it works" can be checked without hardware |
| **Judging weights** | **Conflict:** the pre-event blog reading of the waiver gives Impact 30 / Meta tooling 25 / Demo 25 / Technical depth 20. A participant's Oct 26 reading of the guide says "equal weight, 0 to 10" across the same four criteria. [reconscribe](https://reconscribe.com/meta-global-ai-developer-hackathon-2026/) [github](https://github.com/sholaola23/meta-hackathon-2026) | Both secondhand | The four criteria are the same either way. Optimise for all four, and weight the demo and the Meta-tooling story heavily. |
| **Anti-projects** | "AI Job Application Screener" and "Simple Image Analyzers" [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand | Don't let the camera feature look like a "simple image analyzer". Always tie vision to the diagnostic state. |
| **Prizes** | $1M pool across two tracks (primary). "Anticipated" Indie prizes of $150k/$100k/$75k/$50k/$25k, and bonus prizes up to $50k such as a community-vote award [meta](https://dev.meta.ai/events/global-hackathon) [reconscribe](https://reconscribe.com/meta-global-ai-developer-hackathon-2026/) | Primary for the pool; the rest secondhand | No automotive or hardware track found |
| **IP** | Entrant keeps ownership; organizers get a "perpetual, irrevocable, worldwide, royalty-free, non-exclusive licence" [reconscribe](https://reconscribe.com/meta-global-ai-developer-hackathon-2026/) | Secondhand (pre-event waiver) | Keep proprietary future assets (verified-fix DB, adapter firmware tricks) out of the submission |
| **Credits/billing** | $150 Meta credits; the claim form "wasn't out yet on 26 Oct". A spend-limit notification "does **not** stop billing." [github](https://github.com/sholaola23/meta-hackathon-2026) | Secondhand (Discord) | Add a client-side budget guard to the backend |
| **Data/training** | Contributor mode trains Meta models on your content. **Muse Code subscriptions default to Contributor mode.** [meta](https://dev.meta.ai/docs/muse-code/subscriptions) [meta](https://dev.meta.ai/docs/pricing-rate-limits) | Primary (Meta docs) | Use `muse-spark-1.3` (Standard) in the app. Switch Muse Code's Contributor mode off if you don't want your repo used for training. |

**Kickoff schedule (secondhand):**
- Agents 101/102 workshops on Oct 27–28 at 12 PM ET [github](https://github.com/sholaola23/meta-hackathon-2026)
- Fireworks session on Oct 30 [github](https://github.com/sholaola23/meta-hackathon-2026)
- Discord office hours on weekdays, 10–11 AM ET [github](https://github.com/sholaola23/meta-hackathon-2026)
