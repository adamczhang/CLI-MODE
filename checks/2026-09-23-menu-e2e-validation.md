# Menu rendering repair and six-CLI validation — 2026-09-23

## Incident and fixes

Inspected the last two user turns in **Dev (Angry Cats)**, task
`01a0b7a8-cacb-7151-a241-1e2e37599171`: “commit and publish” and the CLI-MODE
skill invocation. The invocation successfully ran the installed controller and
wrote its HTML. Its final answer was plain `visualize{...}`, which is text, not
a Codex rendering reference. The previous tests asserted that incorrect format.

The shared formatter now includes the renderer's U+E200, U+E202 and U+E201
delimiters. This fixes menus, activation confirmations and final relay views.
Regression checks validate the complete reference, a path containing spaces,
JSON decoding and a Windows cp1252 stdout configuration. Provider text cannot
inject a rendering reference. The hook, skill and presentation references now
describe preserving the complete reference.

The same task's receipt recorded hooks from `1.0.8+codex.20260922130421`, while
the invoked controller was `0.1.16`. Full Access was enabled, but routing was
correctly blocked as `different-plugin`. The home and setup menus now explain
that the task loaded an older plugin and must use a new task before `/cli`.
The fix does not manufacture a fresh hook receipt or bypass trust/readiness.

## Candidate and installation

- Base: `main` at `63e2373f32ab682c3bad56fde39bd7c258a82c18`.
- Local hotfix: `0.1.16+codex.20260923045108`, installed and enabled as
  `cli-mode@personal`.
- Archive SHA256:
  `1a9a5c021c5a0317cc189dd9d3e66a90e8ed9ae94b14d55779c1a4e09aac15e6`.
- All 83 installed source/cache files matched the archive. Previous source is
  preserved under `%USERPROFILE%\.codex\.tmp\cli-mode-menu-hotfix-20260923\previous-source`.
- Runtime: Windows, Python 3.14, ACPX 0.18.0 (verified global installation).
  Doctor found no conflicting standalone skills.

## Automated and browser checks

The stable source suite passed 497 tests. Its first fresh archive extraction
also passed 497 tests. After adding the stale-hook diagnostic, all 28 targeted
frontend, menu-rendering and hook-context tests passed, including a new test
covering the home menu and every backend's stale-hook setup screen.

Final fresh-package suite: **498 tests passed** in 243.349 seconds, including the
new stale-hook regression. The complete log and extracted-package evidence are
retained with the live evidence. Existing subprocess-fixture ResourceWarnings
remain visible in those logs; there were no failed checks.

`presentation_browser_probe.cjs` opens the exact emitted HTML in headless Edge
at 360px and 900px widths in light and dark themes. It checks fragment presence,
visible text and horizontal overflow, and saves representative screenshots.
All **396 layout checks** passed: 392 across the six agents' emitted views and
four for the existing Angry Cats task's corrected stale-plugin screen.

## Live sequence

`live_coding_queue.py` now invokes the real controller CLI for the home menu,
agent choice, prerequisites, activation menu, routing menu, model/effort/access
choices, help, settings and relay. Every returned view is checked against the
renderer contract and its actual HTML file. The controller remains responsible
for numbering, paging and selecting the displayed defaults.

Each isolated account-backed run exercises:

1. Home, prerequisite checks and activation through model/effort/access menus.
2. Help/settings without changing the owned provider conversation.
3. Three FIFO coding turns, with two captured while the first is running.
4. Marker retention and independent tests of the generated API, CLI, invalid
   inputs and provider-authored tests.
5. Passthrough, Direct host-only routing, stop, repeat stop and off-state refusal.
6. Rebinding, a fresh response, and final cleanup of ownership/in-flight state.

| CLI | Menu through coding, routing, stop/reopen | Cancel and queued follow-up |
| --- | --- | --- |
| Antigravity | PASS | PASS |
| Claude Code | PASS | PASS |
| Grok Build | PASS | PASS |
| Cursor | Menus PASS; activation BLOCKED | BLOCKED by activation |
| GitHub Copilot | PASS | PASS |
| Codex CLI | PASS | PASS |

All 15 coding stages passed on the five accessible providers, including
independent acceptance checks. All five preserved context across queued turns,
completed the Passthrough and reopen probes, and passed cancellation with a
queued follow-up. Every attempted backend ended inactive, with no owned sessions
or in-flight operations. Cursor's failed activation also cleaned up correctly.

Cursor returned **“Upgrade your plan to continue”** for both its configured
Composer 2.5 model and a separate Auto (`default[]`) readiness probe. The Auto
probe also shut down cleanly. This is an external account-access blocker, not a
passing coding result; no provider substitution or account change was made.
Its diagnostic evidence is under
`%USERPROFILE%\.codex\validation\cli-mode\2026-09-23-cursor-auto`.

| CLI | Setup/activation | Coding stages 1 / 2 / 3 |
| --- | ---: | --- |
| Antigravity | 54.68 s | 17.03 / 20.07 / 47.76 s |
| Claude Code | 16.35 s | 22.33 / 40.12 / 47.78 s |
| Grok Build | 15.81 s | 275.14 / 67.51 / 178.17 s |
| GitHub Copilot | 18.58 s | 15.02 / 11.65 / 14.97 s |
| Codex CLI | 24.06 s | 33.85 / 36.79 / 63.72 s |

Grok emitted public activity during its longer coding run. These times
include provider work and queue settlement; they are not menu-rendering times.

## Evidence and limits

- Live evidence: `%USERPROFILE%\.codex\validation\cli-mode\2026-09-23-menu-e2e`.
- Cancellation evidence: `%USERPROFILE%\.codex\validation\cli-mode\2026-09-23-menu-cancel`.
- Each backend has exact prompts, public response/event evidence, generated
  artifacts, command JSON, HTML fragments and terminal cleanup state.
- Tests use disposable workspaces and controlled hook events with real provider
  sessions. They do not certify actual Desktop hook loading/trust or a fresh
  machine installation. Browser fragment rendering and the Desktop reference
  contract are verified separately; no post-update Desktop screenshot is claimed.
- The existing Angry Cats task still needs a fresh task to load updated hooks.
  Its gameplay files, deployment and provider conversation were not changed.
- Account access, quota, every advertised model, all provider-native workflows,
  attachment forwarding and live idle expiry are not inferred from a successful
  coding run. Blockers are recorded rather than silently switching providers.
