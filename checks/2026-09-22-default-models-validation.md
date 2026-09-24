# Default model and help update — 2026-09-22

The requested initial defaults for new CLI-MODE conversations are:

| Backend | Model | Effort | Access |
| --- | --- | --- | --- |
| Codex | GPT-6 Sol (`gpt-6-sol`) | High | Full access |
| Grok Build | Grok 4.7 | High | Always approve |
| Cursor | Composer 2.5 (`composer-2.5[fast=true]`) | Provider default | Always approve |
| Antigravity | Gemini 3.8 Flash | High | Allow on |
| Claude Code | Opus 5 | High | Bypass permissions |
| GitHub Copilot | Provider default | Provider default | Allow all |

Grok, Antigravity, and Claude already had the requested defaults. Copilot remains
on its provider default because a fresh live ACP session exposed only `mode`
and `allow_all` config options, with no model catalog or effort selector.
CLI-MODE cannot select or verify a specific Copilot model.

Cursor's installed ACP session advertises the exact Composer 2.5 ID above and
accepted a `set model` command. It exposes only `mode` and `model` config
options. It does **not** expose a High reasoning-effort selector, and the
advertised Composer ID contains only `fast=true`. CLI-MODE therefore leaves
effort as Provider default and does not synthesize an unsupported model ID.
[Cursor's model-parameter documentation](https://prod.cursor.com/docs/subagents)
also says available bracketed options vary by model.

On the current Cursor account, a fresh activation with Composer 2.5 returned
“Upgrade your plan to continue” during readiness. CLI-MODE correctly kept
routing inactive and closed the test session. The requested model remains the
default; no paid upgrade or fallback to Auto was attempted. A separate probe
had confirmed the model setting itself was accepted. Codex's live ACP session
advertised GPT-6 Sol and High; fresh activation accepted and read back both.

Adapter defaults, bundled catalogs, activation menus, shared help, and backend
guidance were updated together. Generated model menus marked the expected
current row for all six backends. An older Codex catalog missing Sol or High
is augmented with the bundled initial choice while preserving its other cached
models; live setting verification still gates routing. Saved per-conversation settings still take
precedence over initial defaults; this change does not rewrite existing tasks.
The `v0.1.11` tag and release archive remain unchanged, and nothing was pushed
or published.

The `/help` and `$help` page now opens a CSS-only tabbed inline guide: Start,
Commands, Routing, Settings, Activity & queue, Setup & agents, and Defaults.
The Commands tab has a 40-row command/description table covering every CLI-MODE control,
including all six agent selection and quick-bind commands. Tabs switch locally;
they send no provider request and do not change controller state. The 160-row
plain-text guide remains available if inline rendering is unavailable, and every
source row fits the 36-column text-menu body.
An `X` immediately after help now dismisses only help. It preserves an active
agent, any open settings or setup menu, and the routing mode; a later `X` still
performs the underlying menu's usual action. This closes a route where `X` after
help previously stopped the bound agent. Focused tests exercise help dismissal
under both routing modes, active settings, the nested mode menu, and inactive
setup. The rendered HTML help remains a read-only controller command. Chrome
visual checks confirmed mouse and keyboard tab switching, readable table text,
all seven panels, access to the last command row by scrolling, and no horizontal
page overflow at 390px mobile width.

The final candidate was built outside the repository from the complete plugin
source. It contains 82 files and six backend registrations; its SHA256 is
`e55649cab7a50a3e225cb3910504556342cccc5e483899b45b593c854ebe5627`.
Full source and fresh-package smoke test results are recorded below.

| Verification | Result |
| --- | --- |
| Focused help routing, command matrix, renderer and tab structure | Passed |
| Chrome desktop/mobile tab interaction and visual inspection | Passed |
| Final full source suite | 453 tests passed in 226.533 seconds |
| Final fresh extracted package suite | 453 tests passed in 228.000 seconds |

Probe evidence is under
`%USERPROFILE%\.codex\validation\cli-mode\2026-09-22-defaults-probe`.
The isolated candidate is under
`%USERPROFILE%\.codex\validation\cli-mode\2026-09-22-tabbed-help-candidate-04`.
The extracted-package test report is
`%USERPROFILE%\AppData\Local\Temp\cli-mode-fresh-package-_2w_fr40\evidence.json`.
