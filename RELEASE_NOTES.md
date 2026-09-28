# CLI-MODE v0.4.0 — AUTO: Claude leads, a cheaper engine works

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Claude Code** or **Codex**, over ACPX.

This release brings **AUTO mode** to Claude Code, and makes it Claude Code's default.

**Two modes.**

- **DIRECT** is the pass-through you orchestrate: every `/d` goes to the agent you
  name, and you decide how many agents run and how the work is split. It keeps
  Claude's own usage to a minimum. Codex keeps DIRECT only.
- **AUTO** is a partnership: you talk to Claude only. Claude keeps quick work and
  anything that needs its judgment, and hands your agent the work worth handing
  over: long jobs, long pasted messages, big independent parts (each on its own
  agent) and reviews. It writes each task, the agent works in the background, and
  Claude reads the result, checks it and tells you how it went.

**Pair Claude Opus with a cheaper engine.** AUTO's use case is Claude Opus as the
lead, planning and checking, with a less expensive model from another provider
doing most of the work. In usage tests with Gemini Flash (Antigravity) as the
engine, against Claude alone on the same prompts:

| Prompt | Work moved off Claude | Time, Claude alone → AUTO |
|---|---|---|
| 2.5k tokens, 3 independent parts | 36% | 1.7 → 5.5 min |
| 12.5k tokens, 4 parts | 60% | 5.7 → 7.3 min |
| 50k tokens, 5 parts | 77% | 16.7 → 6.5 min |
| 5k tokens, one sequential job | 66% | 2.3 → 3.7 min |

Every AUTO run passed its hidden tests, saved every data file byte for byte and
gave every exact answer; on the 50k prompt, Claude alone re-typed the data and
got one file wrong. With Claude Code agents instead, AUTO was fastest (2.3 min and
2.7 min on the two larger prompts), but that work stays on Claude's plan.

**What makes it work.**

- One writer per file: each task names the files it may change, so several
  agents write at once without colliding; up to 6 agents run in AUTO.
- A handoff is one tool call, and the result arrives with Claude's wake-up,
  opened by CLI-MODE's own verdict (`CHECK: ok`, or what to look at), a per-file
  change summary and the test result, so Claude reports without re-checking.
- Long messages are saved and tasks point to their part of them; parts sent
  together wake Claude once.
- Delegation strength (Normal by default, Strong, Max), the AUTO agent's model,
  effort and Codex's fast mode are set on the Mode page (`/cli mode`).
- The safety controls stay yours: `/cli cancel`, `/cli list`, `/cli usage`,
  `/cli view`, `/cli access`, `/cli approve|deny` and `/cli off`.

Also in this release: each agent starts once, on its chosen settings; canceled
turns show at once; `/cli usage` fixes; and the full check suite runs in about
75 seconds in parallel.

See the [changelog](CHANGELOG.md) for everything, and the README's "Which to use".
