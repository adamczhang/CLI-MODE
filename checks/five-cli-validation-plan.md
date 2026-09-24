# Five-CLI functional validation plan

Status: planned; no new live results are asserted by this document.

Validate the source at `f67b874` and the installed CLI-MODE 0.1.8 copy against the
actual installed provider versions. Record any source changes before execution.
The objective is five working research conversations, each with three connected
research prompts, plus verified opening, controls, recovery and closure.

## 1. Coverage and evidence rules

| CLI | Backend ID | Open menu | Direct activation | Model / effort expectation |
| --- | --- | --- | --- | --- |
| Antigravity | agy | /cli agy | /cli bind agy | Advertised model; effort encoded in model ID |
| Claude Code | claude | /cli claude | /cli bind claude | Separate advertised model and effort |
| Grok Build | grok-build | /cli grok | /cli bind grok | Separate advertised model and effort; test grok-build alias |
| Cursor | cursor | /cli cursor | /cli bind cursor | Exact advertised model ID; no effort selector |
| GitHub Copilot | copilot | /cli copilot | /cli bind copilot | Provider default model and effort unless runtime support changes |

Run providers sequentially, with only one owned main conversation active.
Use the same research topic and acceptance rules for all five; generate a
different random marker for each. Do not transfer transcripts between providers.
Use runtime-advertised choices rather than assuming historical model names.

Maintain separate results for:

1. Offline regression and fresh-package validation.
2. Live controller/provider integration using isolated test state and workspace.
3. Installed Codex Desktop integration using real task identity, installed files,
   genuine hook observations and user-visible controls.

A controller run cannot establish Desktop hook execution, menu rendering or host
adherence. Synthetic hook receipts must never satisfy an installed-host gate.
Use temporary workspaces for file operations. Keep account secrets and private
reasoning out of evidence. Preserve existing user sessions and history.

Result vocabulary: PASS, FAIL, BLOCKED, NOT RUN, or N/A with a specific reason.
An account/paywall refusal is BLOCKED for functional research, even if transport
returns `end_turn`. A missing advertised capability can be N/A only for that
capability; inability to research is never a pass. Mark functionality confirmed
only from observations in this run.

## 2. Preflight and offline baseline

- Record commit, installed plugin version/path, source-to-installed file parity
  (excluding intentional manifest cachebuster), OS, Python, PowerShell, ACPX and
  all five CLI versions. Record selected models and redacted account readiness.
- Run `python plugins/cli-mode/scripts/doctor.py`; identify conflicting skills.
- Run `python -m unittest discover -s checks -p 'test_*.py'`, then
  `python scripts/package_plugin.py`, then `python checks/package_smoke.py`.
  Capture exit codes and exact failures; an environment failure is still a gap.
- Verify the built archive contains five registered runtime adapters and their
  guides, assets and hooks, without credentials or runtime state.
- Run each backend's prerequisite scan. Verify actual authenticated response,
  not merely an executable or token directory. Test research entitlement below.
- For Desktop, verify Full Access and fresh hooks from this installed copy.
  Missing dependencies, login or hook approval remain explicit setup blockers;
  do not bypass gates or install/sign in implicitly.

Existing `live_smoke.py` and `idle_smoke.py` are Antigravity-specific. Extend or
replace their assumptions before using them to certify all five. Direct
activation with hook checks disabled is controller evidence only.

## 3. Identical three-turn research sequence for each CLI

Run this sequence in one persistent provider conversation per CLI. Expand
`<MARKER>` and `<OUTPUT_PATH>` once in prompt 1 only. OUTPUT_PATH is an absolute
path to `research.md` inside that backend's disposable test workspace.
Save the exact UTF-8 prompts and filtered public responses. No host rewriting,
extra context injection or hidden reconstruction between turns is allowed.

### Prompt 1 — research and remember

> Research Python's standard-library module `venv` and the `pip` package installer using the official
> Python and pip documentation. In at most 180 words, explain what each does,
> give one Windows PowerShell command for creating a virtual environment, and
> provide two direct documentation links supporting your explanation. Remember
> the exact marker `<MARKER>` and output path `<OUTPUT_PATH>` for later turns.
> Include the marker in your reply. Do not create files yet.

### Prompt 2 — follow up without restating context

> Using the two tools and Windows environment from your previous answer,
> research whether activation is required to install a package into that
> environment. Give the explicit-interpreter command for installing `requests`,
> explain why it targets that environment, and cite the relevant official
> documentation. Repeat my exact marker. Do not install anything or create files.

### Prompt 3 — synthesize and verify an artifact

> Combine your previous two answers into a beginner checklist of at most six
> steps. Include the environment creation command, installation without
> activation, and a command that prints the Python executable in use. Preserve
> the official source links and my exact marker. Save the checklist to the
> output path I supplied earlier, then return the full path and a short summary.
> Do not install packages or run the checklist commands.

Required observations for all three turns:

- The correct backend receives each complete prompt exactly once and completes
  successfully. No host answer or other backend substitutes for it.
- Record public research/tool evidence where the runtime exposes it. Independently
  open every cited page and check that it supports the associated claims. Without
  evidence of retrieval, label live research retrieval unverified even if factual
  answers and links are correct; do not infer browsing from fluent prose.
- Prompt 2 retains the previous tools/environment and the unpredictable marker.
  Prompt 3 retains the marker and output path without either being repeated.
- Track stable logical conversation/ownership identity, backend-specific record
  IDs and provider IDs. A process restart or allowed transport-ID rotation alone
  is not context loss; unexplained identity changes require investigation.
- Inspect the output file: it exists at the requested path, has at most six
  steps, contains the exact marker, valid source links and correct commands.
  The explicit interpreter should target the created environment's
  `Scripts/python.exe` (or equivalent valid Windows path). The executable check
  should use that interpreter to print `sys.executable`.
- No packages are installed and no checklist command is executed. Artifact
  creation is the only requested workspace mutation.
- Observe latency, public progress when emitted, final completion and errors.
  Absence of a public plan is acceptable; invented progress is not.

Between prompts 1 and 2, show host help and reopen/cancel settings without
changing them; verify the pending menu is resolved before sending prompt 2.
Between prompts 2 and 3, use an isolated test idle timeout, verify the provider
process actually expires where supported, and reconnect through the saved
session. Do not alter production defaults. If idle expiry cannot be induced,
record that subtest NOT RUN and complete prompt 3 normally.

## 4. Opening, controls, shutdown and reopening — all five

Perform these checks for each backend. Additional short control/continuity
probes below are separate from the three required research prompts.

| Area | Required test and pass condition |
| --- | --- |
| Home and setup | All five agents visible; opening a menu does not activate. Each backend has its own prerequisite receipt. First-start numbered replies select setup, never dispatch research. |
| Menus | Inspect model, effort and access phases; Back, Refresh, Next/Previous and Exit behave correctly. At most ten selectable rows, stable numbering, no missing/duplicated choices. Cursor's long model list must exercise paging. |
| Activation | Accept displayed defaults through the menu; verify readiness, accepted settings, correct backend and exactly one owned session before reporting activated. |
| Tuning | Apply a supported alternate model and effort where available, then restore. Change advertised access and restore. Read back native settings where supported; unchanged fields persist and ownership is reused. |
| Unsupported controls | Cursor effort and Copilot model/effort show Provider default; unsupported choices are refused without invented settings. Grok/Cursor access is ACPX policy, not a verified native permission selector. Copilot allow_all is applied/read back. |
| Access behavior | In the disposable workspace, exercise a harmless requested write under prompt policy and allow policy; confirm approval requests/decisions are honored. Test auto-edit only when advertised. Restore settings afterward. |
| Help and parser | /help and $help preserve state; / and $ controls, mixed case and leading whitespace work. Invalid controls are never forwarded. Quoted controls, /client and controls embedded in prose remain content. Exhaustive combinations run offline. |
| Presentation | Correct backend name in activation, passing, attribution and errors. One passing announcement per dispatch; provider content unchanged. Two-voice rendering follows current SKILL.md; fallback styling limitations are disclosed. |
| Closure | /cli stop immediately gates routing, cancels/closes owned sessions and reports shutdownComplete. Verify inactive state, empty ownership and settled inflight operations plus backend closure evidence; unrelated sessions survive. |
| Idempotence | Repeat /cli off; it succeeds without creating sessions. X exits both setup and active menus and performs the required cleanup. |
| Off routing | Submit a harmless ordinary message after stop; no provider dispatch occurs. |
| Reopen | /cli bind <agent> reopens with that backend's saved verified defaults and one owned session. A short response works. Record actual new/resumed identity; do not claim history was erased or retained without evidence. Close again. |
| Switch | Complete cleanup before moving to the next backend. No inherited model, effort, access, marker or owned session from the previous backend. |

## 5. Native commands and adverse paths

Run native-command checks after the three research turns so Antigravity's
intentional transport change cannot invalidate their continuity test.

- Discover a harmless read-only command from each runtime's current advertised
  command list. Dispatch it and confirm native expansion rather than a model
  explanation. Refuse a demonstrably unadvertised command without changing state.
  Record actual command-list availability and admission behavior: the detailed
  guides permit some ACP backends to defer to the provider before a list is seen.
- Antigravity: verify one ACP closure, explicit disclosure of fresh native context,
  exact saved native ID for two subsequent short continuity probes, and native
  shutdown. Do not claim ACP history transferred.
- Claude, Grok, Cursor and Copilot: verify supported commands expand within the
  existing ACP conversation and retain a marker across the command.
- Stop during an in-flight harmless task for every backend; routing must gate
  immediately, late output must not reactivate it, and eventual cleanup must be
  verified. Use bounded timeouts; inspect uncertain work and never resend blindly.
- Offline controlled failure injection for every adapter: missing executable,
  unauthenticated response, quota/paywall response, transport timeout, partial
  setting failure, failed close, stale ownership and stop/ensure race. No silent
  fallback, false success, extra session or destructive cleanup is acceptable.
- Crash/recovery and compaction: preserve pending menu/snapshot and an inflight
  operation; observe its existing event stream after resume without redispatch.
  Verify completed work is never replayed. Use deterministic offline coverage
  for each backend and actual Desktop resume/compaction evidence when feasible;
  otherwise record the Desktop subtest NOT RUN.
- Verify per-task/workspace isolation using independent test identities offline;
  verify the installed task uses its actual identity/workspace. Workspace
  rebinding must fail until complete shutdown.
- Check usage display against actual structured provider data. Unsupported quota
  data must say unavailable, never zero or an invented percentage. Do not send
  ordinary `usage` as a research prompt to manufacture utilization information.
- Confirm filtered public relay excludes private thought/tool-status events;
  deterministic fixtures must cover these even when a live backend emits none.

Clean-machine installation needs a disposable Windows environment and the actual
installer/sign-in flow for each backend. Test it separately if certifying fresh
installation; an already installed machine cannot establish that claim. Image
generation, attachment input and every provider-native workflow are outside this
research/lifecycle certificate unless separately exercised; advertised provider
capabilities alone do not prove CLI-MODE supports them.

## 6. Report and release gate

Write a fresh dated report with source revision, installed version, per-test
statuses and evidence paths. Retain exact prompts, filtered responses, artifact
hashes, source-verification notes, settings snapshots, session identities,
completion/error events, shutdown evidence and test command exit codes.

| CLI | Open/settings | Research 1 | Follow-up 2 | Artifact 3 | Continuity/idle | Native command | Stop/reopen | Desktop hooks/UI | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Antigravity | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Claude Code | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Grok Build | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| Cursor | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |
| GitHub Copilot | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN | NOT RUN |

Confirm all-five research functionality only when all 15 research turns and all
five artifacts pass, with correct continuity and successful cleanup. Report
controller functionality separately from installed Desktop functionality.
Any required BLOCKED, FAIL or NOT RUN prevents an unqualified all-functionality
claim. Fixes require rerunning the affected backend's complete sequence; changes
to shared routing/lifecycle code require rerunning all five and the offline suite.

Historical findings motivating this plan: the old live smoke scripts target only
Antigravity; the 0.1.8 report mixes four/five-agent audits and conflicting verified/
unverified statements; Cursor previously returned an upgrade refusal with normal
completion. Older acceptance/presentation text also conflicts with the current
two-voice skill instructions. Record discrepancies explicitly rather than using
historical assertions as current evidence.
