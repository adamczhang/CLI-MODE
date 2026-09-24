# Five-CLI validation — 2026-09-22

An earlier complete research pass succeeded: all 15 research turns, all five
artifacts and all five close/reopen cycles passed. The final-build rerun hit
Claude's session quota during turn 2 after turn 1 passed; cleanup succeeded.
The provider reports a reset at 03:10 America/Chicago. No reset credit, paid
upgrade or fallback model was used. Other final-build results are recorded below.

Earlier complete pass (`cli-mode-five-final-20260922`):

| CLI | Research turns | Retained context | Checklist | Close/reopen/final close |
| --- | --- | --- | --- | --- |
| Antigravity | 3/3 PASS | PASS | 6 steps, PASS | PASS |
| Claude Code | 3/3 PASS | PASS | 6 steps, PASS | PASS |
| Grok Build | 3/3 PASS | PASS | 5 steps, PASS | PASS |
| Cursor (Auto) | 3/3 PASS | PASS | 6 steps, PASS | PASS |
| GitHub Copilot (provider default) | 3/3 PASS | PASS | 6 steps, PASS | PASS |

Final-build rerun (`cli-mode-five-release-20260922`):

| CLI | Research | Artifact | Close/reopen | Final cleanup |
| --- | --- | --- | --- | --- |
| Claude Code | 1/3, then quota-blocked | Not reached | Not reached | PASS |
| Grok Build | 3/3 PASS | 4 steps, PASS | PASS | PASS |
| Cursor Auto | 3/3 PASS | 6 steps, PASS | PASS | PASS |
| Copilot default | 3/3 PASS | 5 steps, PASS | PASS | PASS |
| Antigravity | 3/3 PASS | 6 steps, PASS | PASS | PASS |

Offline regression: **313 tests pass**. Fresh extraction of the rebuilt package:
**313 tests pass**. Five backend registrations and archive integrity pass.
Each completed research workspace contains only the requested `research.md`.
The final Claude workspace is empty because quota stopped it before the file step.

## Scope and environment

Source: `f67b874` plus the fixes described below. Windows, Python 3.14, ACPX
0.18.0; Antigravity 1.2.7, Claude Code 2.1.278, Grok Build 1.0.40, Cursor Agent
2026.09.18-9a7762b, GitHub Copilot CLI 1.0.87. All five prerequisite scans passed.
No subscriptions were changed and no provider was installed or signed in by this
test. Cursor Auto and Copilot's provider default both completed real research on
the existing accounts despite the historical Cursor paywall result.

Live tests use isolated controller identities, disposable workspaces and named
owned sessions. They do not activate passthrough for this Codex development task.

## Reproduced defects and fixes

1. **Windows PATH growth:** every ACPX invocation appended registry directories
   again. Repeated calls exceeded the Windows environment limit; even earlier,
   cmd.exe stopped finding PowerShell. This reproduced two offline hook failures
   and interrupted the initial live run. PATH refresh is now idempotent and
   preserves unique entries.
2. **Failed-creation cleanup:** when creation failed before registering a session,
   close returned “no named session,” leaving phantom ownership indefinitely.
   Cleanup now confirms `status=no-session` before releasing that ownership;
   unknown or existing owners remain protected.
3. **Recoverable file RPC treated as prompt failure:** Grok read a nonexistent
   output file, received a resource-not-found response, then created it correctly
   and completed. The controller falsely failed the turn. The stream now tracks
   file-RPC IDs separately from prompt IDs and filters correlated tool replies.
   Unknown errors and ambiguous ID collisions remain errors. Recorded-stream,
   real-pipe regression and a fresh live Grok file-creation turn exercise the fix.
4. **False readiness from a refusal:** a message such as “Upgrade your plan to
   continue” with normal completion previously passed activation. The existing
   readiness probe now requires a fresh marker in the public response. All-five
   refusal fixtures confirm routing stays inactive and new ownership is cleaned.
5. **Wrong backend setup identity:** setup headers and successful prerequisite
   messages always named Antigravity. They now name the selected backend, and
   manual setup passes that backend to the installer.
6. **Historical permission exit:** ACPX 0.18 accumulates permission counters for
   the owner lifetime. A prior denied write poisoned later successful read-only
   turns with exit 5. A narrowly scoped recovery requires an observed prompt,
   public reply, normal completion and no current permission request, terminal
   operation or error. File writes additionally require correlated success replies;
   denied, ambiguous or unresolved writes remain failures.
7. **Cursor Prompt bypass:** a real file write succeeded under Prompt because
   native Cursor writes bypass ACP callbacks. Prompt is removed and saved
   restricted sessions cannot dispatch work. Allow remains supported.
8. **Antigravity Auto-edit cannot write:** accepted native `auto_edit` still
   forwards writes to ACPX, whose approve-reads policy denies them. Auto-edit is
   removed from ACP choices, including refreshed/old catalogs and saved sessions.
   It is never silently broadened to approve-all. Claude Auto-edit passed live.
9. **Missing startup command catalogs:** newer ACPX persists `available_commands`.
   The controller now reads that metadata before validating commands, as well as
   listening for updates. Unknown commands are rejected when a catalog exists.
10. **Successful empty command output:** Grok `/context` returned normal completion
    without public text. Advertised commands may now return `noPublicOutput:true`;
    ordinary research and readiness still require a public reply.
11. **Conflicting instructions:** contribution docs listed three backends; older
   acceptance and presentation text applied Antigravity's transport and colours
   to other providers. Updated these to the five-backend/two-voice contract.

## Control checks

| CLI | Idle memory | Supported permissions | Model/effort | Native command | Unknown command | Stop during turn |
| --- | --- | --- | --- | --- | --- | --- |
| Claude Code | PASS | Prompt denies; Auto-edit/Allow write | PASS | `/context`, PASS | Refused | PASS |
| Grok Build | PASS | Prompt denies; Allow writes | PASS | `/context` completes with no public output | Refused | PASS |
| Cursor Auto | PASS | Allow writes | Alternate model blocked by plan; no effort selector | `/copy-request-id`, PASS | Refused | PASS |
| Copilot default | PASS | Prompt denies; Allow writes | No advertised model/effort selector | `/context`, PASS | Refused | PASS |
| Antigravity | PASS | Prompt denies; Allow writes | PASS | `/model`, native handoff, PASS | Refused | PASS |

The controls preserve one owned main session through tuning, test retained context
before and after native commands, then stop an in-flight response and confirm the
submitter settles and repeated shutdown remains clean. Antigravity's native
handoff intentionally starts a native conversation; its post-handoff continuity
is tested separately, rather than claiming ACP history transfers.

The idle harness accepts ACPX's `idle` status as well as `dead`. It temporarily
uses a five-second timeout to prove owner expiry, then restores the production
30-minute timeout. Keeping the artificial timeout during all settings calls
caused a Claude metadata mismatch; the controller correctly gated dispatch.
Claude tuning passed when rerun with the production timeout restored.

## Research checks

Each CLI receives exactly three connected research prompts per test sequence:
official Python/pip research with an unpredictable marker and output path;
follow-up on installing without activation; synthesis into a saved checklist.
Prompts 2 and 3 do not repeat the marker or path. Additional readiness and reopen
probes are separate from the research count.

The checklists are inspected for retained marker/path, at most six steps,
environment creation, explicit environment interpreter, `sys.executable`, and
official links. No package installation is requested or performed by the host.
The research workspaces are checked for unexpected files.

Independent source verification uses the official
[venv documentation](https://docs.python.org/3/library/venv.html),
[Python tutorial](https://docs.python.org/3/tutorial/venv.html),
[pip overview](https://pip.pypa.io/en/stable/),
[pip getting started](https://pip.pypa.io/en/stable/getting-started/) and
[pip user guide](https://pip.pypa.io/en/stable/user_guide/), plus the
[Python Packaging tutorial](https://packaging.python.org/en/latest/guides/installing-using-pip-and-virtual-environments/)
linked by Antigravity.
Provider stream inspection records only public tool metadata referencing these
URLs; private reasoning and credentials are excluded from the evidence report.

## Cleanup and remaining limits

All five control sequences passed their supported checks and confirmed final
shutdown. The audit found and closed one late-created Antigravity session from
an interrupted harness; `orphan-recovery.json` records that exact cleanup.
All 49 ACPX records belonging to these test directories are now closed.
Antigravity's native submitter also settled and ownership/inflight state is empty.
Provider-managed background teams were not created or independently verified.

The final-build research certificate is **four PASS, one quota-BLOCKED**; an
unqualified all-functionality or all-five final-build claim would be incorrect.
Claude's earlier complete research sequence and later full control suite passed.
Cursor Auto passed; one alternate model returned an upgrade refusal, so no
further subscription-gated models were attempted. No paid upgrade was made.

## Evidence locations

Public prompts, responses, settings, artifact hashes and cleanup outcomes are
preserved under `%USERPROFILE%\.codex\validation\cli-mode\2026-09-22`.
Provider raw streams, private reasoning and authentication profiles are not copied.
`source-hashes.json` identifies the tested plugin files. `package-evidence.json`
records the fresh-extraction run and archive digest.
`controls-index.json`, `artifact-assessment.json`, `installation.json` and
`cleanup-audit.json` summarize the final evidence.

Final archive SHA256: `fa06d6bd8c9562cc0e90e0e6d617bc238593d361f0cfc76b9fe02c963fb14977`.

## Installed-host boundary

The fixed plugin is installed and enabled as `0.1.8+codex.20260922062127`.
Its installed content matches the tested source apart from the cachebuster.
Start a new Codex task to load its updated skill instructions.

This actual Codex task contains a real `UserPromptSubmit` observation from the
installed `0.1.8+codex.20260922051740` plugin, with inactive routing and no owned
provider sessions. That confirms the installed prompt hook has run. It does not
establish all Desktop menus, fresh-install trust flow, compaction or PreToolUse
enforcement in the real app. These remain distinct from fixture tests.

Clean-machine installation, interactive sign-in, provider-internal teams,
image/attachment support and every provider-native workflow are outside the
executed research certificate. The offline suite covers routing, setup gates,
failure recovery, ownership races, compaction state and filtered presentation.

## Reproduction

```powershell
python -m unittest discover -s checks -p 'test_*.py'
python scripts/package_plugin.py
python checks/package_smoke.py
python checks/five_cli_live.py --output <new-absolute-evidence-directory>
python checks/five_cli_controls.py --skip-model-for cursor --output <another-new-absolute-evidence-directory>
```

The live commands consume provider quota. Run them sequentially. Evidence is
written after every completed turn/check; cleanup runs even on failure. A
controller result never substitutes for an installed-host result.
