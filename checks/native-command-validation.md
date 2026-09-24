# Native command validation — 2026-09-22

Development testing confirmed that ACP delivered both teamwork spellings as
prompt text. Native CLI print/stream modes perform client-side expansion.

Implemented: `/help` alone is host help; provider slash commands switch from ACP
to a tracked native conversation. The old ACP session closes before native work.
Follow-ups use the exact saved native conversation ID. The `/teamwork` token is
normalized to `/teamwork-preview`; task content is unchanged.

Live checks used isolated temporary workspaces, existing authentication, Gemini
3.8 Flash High and prompt access. They did not run the user's app-building task
or start worker teams:

- Canonical `/teamwork-preview` created a native `prompt_draft.md` interview artifact.
- A follow-up resumed the same conversation and returned the requested marker.
- `/usage` returned native quota data without changing conversation identity.
- Full ACP readiness, handoff/close, native follow-up, `/model`, `/agents`, and off succeeded.
- The installed CLI's unnormalized `/teamwork` produced no interview artifact.
  With canonical token normalization, the controller produced the expected artifact.

Offline checks cover exact task preservation, help routing, failed preflight and
close, same-conversation follow-ups/tuning, private-event filtering, native errors,
empty successful command results, and cancellation of an in-flight native process.

Limits: interview initialization is not evidence of worker-team execution. The
native conversation starts fresh and does not inherit ACP history. Terminal-only
commands can be unavailable headlessly; native errors are relayed without text
fallback. Off verifies the owned CLI process, not provider-managed background teams.

References: [native headless protocol](https://antigravity.google/docs/cli/headless/),
[command reference](https://antigravity.google/docs/cli/reference/), and
[ACPX Antigravity transport](https://github.com/openclaw/acpx/blob/main/agents/Antigravity.md).
