# CLI-MODE image, menu and resume validation — 2026-09-23

The live checks used isolated workspaces and real provider sessions through the
source hook and controller. Two distinct native image-generation requests were
submitted to every provider that passed readiness. The prompts explicitly
forbade code, SVG, HTML, Pillow and canvas substitutes. Exact prompts,
responses, public events, generated files, rendered menu fragments and cleanup
state are under
`%USERPROFILE%\.codex\validation\cli-mode\2026-09-23-image-menu`.

| CLI | Model menu | Effort menu | Image requests | Final state |
| --- | --- | --- | --- | --- |
| Antigravity | Gemini 3.7 Flash then restore 3.8 Flash | Low then High | Two valid, visually inspected PNGs | Closed; no owned session |
| Claude Code | Sonnet 5 then restore Opus 5 | Low then High | Both completed with explicit native image-tool unavailability; no files | Closed; no owned session |
| Grok Build | Grok 4.7 Fast then restore 4.7 | Low then High | Two valid, visually inspected PNGs; first turn hit the harness's 180-second limit after writing the file, then the same prompt completed in a fresh session with a 360-second limit | Closed; no owned session |
| Cursor | Model and effort menus rendered | Provider default | Readiness blocked: “Upgrade your plan to continue”; no image prompts sent | Closed; no owned session |
| GitHub Copilot | Provider default only | Provider default only | Both completed with explicit native image-tool unavailability; no files | Closed; no owned session |
| Codex CLI | GPT-6 Astra then restore GPT-6 Sol | Low then High | Two valid, visually inspected PNGs | Closed; no owned session |

The initial menu harness incorrectly expected the prior effort value to exist
on every alternate model. A corrected run selected an effort offered by the
new model and verified the model/effort change and restore for Claude, Grok and
Codex. Antigravity passed on the initial run. Copilot advertised no alternate
model or effort. Cursor's menus rendered, but its account could not activate.

For monitoring recovery, a real Antigravity coding turn reached `submitting`.
A separate status-observer process was then terminated. `/cli resume` returned
the same captured request ID and a running worker; a fresh observer received
the final public events and completion marker, and `relay` returned `done`.
The provider prompt was not resubmitted. A source change makes `/cli resume`
uniform across all six backend routes: it returns existing request IDs,
reattaches relay monitoring, and reports uncertain/blocked work without
replaying it. The command cannot continue a deliberately canceled inference;
it resumes observation of an existing uncanceled turn.

The checks confirm source hook/controller behavior and real provider sessions,
not that an already-open Codex Desktop task has reloaded the newly installed
plugin hooks. A new task is required for that host-level check.

The updated personal plugin is installed and enabled as
`0.1.16+codex.20260923052453` from `main` at `63e2373` plus the local fixes.
All 83 installed files match the source. Plugin validation and `git diff
--check` passed; the fresh extracted archive ran 499 tests successfully.
Archive SHA-256: `db3432f9eac8f9324018a94e1aaf204b1a4deeca1e44551f3b04c3866b321c42`.
An additional smoke check ran the **installed cache copy** of the controller:
home, agent, model, effort, access and help menus rendered for all six CLIs;
both `/cli resume` and `$CLI RESUME` routed uniformly; idle resume returned
without launching a provider. Its evidence is under
`%USERPROFILE%\.codex\validation\cli-mode\2026-09-23-installed-smoke`.

Finally, the installed plugin captured a real `/d` image request through the
detached queue, relayed it to completion, rendered the final inline view, and
left exactly one completed request with no owned session after shutdown. The
generated PNG is valid. This provider emitted no ACP image artifact event, so
the final view showed the workspace filename rather than an inline image
preview; the PNG remained available in the workspace. Evidence is under
`%USERPROFILE%\.codex\validation\cli-mode\2026-09-23-installed-image-relay`.
