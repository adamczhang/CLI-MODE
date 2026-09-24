# CLI-MODE versus local OpenClaw ACPX — 2026-09-23

## Scope and verdict

Compared the working CLI-MODE tree (main at `63e2373` plus local changes,
installed as `0.1.16+codex.20260923062851`) with the clean local OpenClaw
checkout (`90e52dde`). This is a code and documentation audit of OpenClaw,
plus the CLI-MODE live validations in
`checks/2026-09-23-image-menu-resume-validation.md`. It is not a live
OpenClaw-versus-CLI-MODE benchmark.

**Verdict:** CLI-MODE is credible and in some ways simpler for its chosen job:
one explicitly bound CLI agent inside one Codex task. It is not yet at least as
good as OpenClaw's end-to-end ACP experience. The two use the same `acpx@0.18.0`
runtime, but OpenClaw has broader host integration for session ownership,
attachments, delivery, recovery, and permission elicitation.

## Comparison

| Area | CLI-MODE | OpenClaw ACPX | Assessment |
| --- | --- | --- | --- |
| ACPX engine | Hard-pins 0.18.0 (`plugins/cli-mode/scripts/acpx-runtime.mjs:29-36`). | Depends on 0.18.0 (`extensions/acpx/package.json:13`). | Core transport parity. |
| Entry and tuning | Six named CLIs; deterministic setup, model, effort, access and help menus; Direct and Passthrough routing (`plugins/cli-mode/skills/cli-mode/SKILL.md:26-37`). | `/acp spawn/status/model/permissions/timeout/steer/cancel/close`; conversation or thread binding (`docs/tools/acp-agents/runbook.md:12-35`). A separate native model picker covers Copilot, Kilo, OpenCode, Pi and Qwen (`extensions/acpx/README.md:22-40`). | CLI-MODE is convenient in Codex; catalogs and surfaces differ, so agent counts are not directly comparable. |
| Session topology | One provider session and one backend per Codex task (`plugins/cli-mode/skills/cli-mode/SKILL.md:102-104`). | Multiple explicit sessions, parent-owned background tasks, current-conversation and persistent channel bindings (`docs/tools/acp-agents/runbook.md:39-51`; `docs/tools/acp-agents/bindings.md:19-41,76-108`). | OpenClaw is broader. |
| Incoming media | Hook captures `event.prompt` as text (`plugins/cli-mode/hooks/route.py:33-47`); ACPX turn reads only `promptFile` text (`plugins/cli-mode/scripts/acpx-runtime.mjs:155-156,190-192`). | Builds an ordered attachment list and passes it into the ACP turn (`src/auto-reply/reply/dispatch-acp.ts:864-928,1005`), subject to harness support (`docs/tools/acp-agents/delivery.md:38`). | Concrete CLI-MODE gap for image/file-bearing user prompts. |
| Outgoing media and delivery | Relays ACP message chunks and ACP image/audio/resource events (`plugins/cli-mode/scripts/acpx-runtime.mjs:78-88`). The installed image test produced a valid PNG but no ACP artifact event, leaving a filename in the final view. The inline renderer strips local-file link syntax (`plugins/cli-mode/scripts/menu_view.py:66-82`). | Gateway owns delivery settlement, fallback, and transcript handling; next queued turn waits for output delivery (`docs/tools/acp-agents/runbook.md:47-48`; `src/auto-reply/reply/dispatch-acp-delivery.ts`). | OpenClaw delivery is more integrated. This does not prove that OpenClaw automatically previews every agent-created file. |
| Resume and failure recovery | Durable request IDs, relay cursors and `/cli resume` reattach monitoring without resubmitting; vanished submitters become uncertain and block replay (`plugins/cli-mode/scripts/queue_worker.py:21-68`; `plugins/cli-mode/hooks/route.py:241-268`). | Persistent binding and session metadata, SQLite replay ledger, safe one-time early-exit retry before prompt submission, explicit upstream `resumeSessionId` (`docs/tools/acp-agents/bindings.md:40-41`; `src/acp/event-ledger.ts`; `src/acp/control-plane/manager.runtime-resume-state.ts:49-125`; `docs/tools/acp-agents/delivery.md:85-109`). | CLI-MODE's requested monitor-resume works; OpenClaw has broader recovery semantics. Neither should replay a possibly accepted prompt. |
| Permissions | `allow` maps to ACPX `approve-all`; other access maps to `approve-reads` with noninteractive `fail` (`plugins/cli-mode/scripts/acpx-runtime.mjs:102-103`). CLI-MODE explicitly does not handle approval prompts (`plugins/cli-mode/skills/cli-mode/SKILL.md:62-65`). | Same default `approve-reads`/`fail` limitation for shell and file permission prompts, but can choose noninteractive `deny`, and channel-delivered form/URL elicitation works (`docs/tools/acp-agents-setup.md:393-440`). Native model-picker approval behavior is a separate route (`extensions/acpx/README.md:22-27,63`). | OpenClaw has more options; neither classic ACPX path has interactive shell/file approval by default. |

## Finding fixed during this audit

`/cli resume` previously let an `OSError` from detached-worker launch escape the
hook, even though ordinary delegated requests reported this failure. It now
returns `start-failed`, identifies the captured request, and tells the user to
retry monitoring after fixing process launch, without resubmitting inference
(`plugins/cli-mode/scripts/queue_worker.py:21-38`,
`plugins/cli-mode/hooks/route.py:241-256`). The regression is in
`checks/test_captured_requests.py:178-190`.

## Validation and limits

CLI-MODE's full Python suite passed: **500 tests**. Plugin validation and
`git diff --check` passed. All **83** non-cache installed plugin files match
the source tree. Earlier live work exercised menus on all six CLIs,
model/effort changes where supported, image requests on five ready providers,
and interruption plus `/cli resume` monitoring. Cursor remained blocked by its account plan; Claude Code and
Copilot did not expose native image generation. The live checks were source
hook/controller and installed-cache smoke, not a fresh Codex Desktop task after
plugin reload. OpenClaw was inspected locally, not run against live providers.

## Recommended parity work

1. Carry supported attachments from the Codex hook into ACPX turns, with a
   capability-aware error when the host does not expose them.
2. Make provider-created local media discoverable and openable in Codex without
   depending on an ACP artifact event or a model rewriting the filename.
3. Add a supervised delivery/monitoring mechanism that does not depend on the
   Codex model repeatedly executing relay instructions; preserve the existing
   fail-closed receipt rules.
4. Offer a noninteractive `deny` permission mode and clearer permission-stop
   recovery; only add richer elicitation if Codex host APIs permit it.
5. Keep the one-session design as the simple default, but offer explicit
   background/session controls if OpenClaw-level orchestration is the goal.
