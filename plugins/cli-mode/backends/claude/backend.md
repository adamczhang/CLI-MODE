# Claude Code backend for CLI-MODE

/cli claude (also $cli claude) opens this frontend, case-insensitively.
/cli bind claude starts with saved defaults without a further menu confirmation.
/cli opens agent selection; /cli stop or /cli off shuts down. /help lists controls.
Requests to build, edit or explain this backend never activate it.

Read [the shared CLI-MODE rules](../../codex/skills/cli-mode/SKILL.md) and
[the ACPX backend guide](references/acpx.md). The shared skill and this internal
backend belong to the same source/distribution. If the shared file is missing,
report incomplete setup instead of inventing routing rules.

Default to the current Codex thread working directory, including any attached
worktree. Only use another directory when the user explicitly chooses it; never
substitute the source checkout, plugin cache or project root for the thread cwd.
Use the installed ACPX `claude` profile, preserving any configured launcher.
ACP is the only transport for this backend: Claude expands its own slash
commands inside the same session, so nothing switches to a native CLI
conversation and no history is discarded. Do not launch an untracked native task
or install runtimes implicitly during activation. The explicit approved first-run
installer is the exception for a visible PowerShell window and account setup;
follow the shared setup-installer guide.

## Text activation menu

Follow [shared frontends and first-time checks](../../codex/skills/cli-mode/references/frontends.md).
For /cli claude, run frontend --agent claude with --menu-output at a task-owned
writable HTML path. Display the returned menuView using the shared inline
presentation guide. Its text comes from the initial template or saved defaults.
This is a menu in the Codex conversation, not a terminal wizard. Do not
activate merely because the skill was invoked. Follow `hostAccess` and
`pending.onboarding` before interpreting numbered replies. New User Detected
means a numbered selection runs `first-time-check --agent claude`; it never means
Yes to activation. The Setup CLI Agent page explains Desktop hook approval and
R rechecks setup. Once setupReady is true, show the activation menu and wait for
Yes. In that menu only, 1/Yes selects defaults and 2 opens model setup. B returns
to the agent menu. No reply or elapsed time is acceptance. Never delegate setup
replies. See the shared frontend reference for the complete setup flow.

The initial requested default is **Opus 5.5** (`opus[1m]`), **High** effort, with
**Bypass permissions**. The default access value is `allow`, mapped in the cached
catalog to Claude's `mode=bypassPermissions` (native label Bypass permissions)
plus ACPX `--approve-all`. Apply and verify the native mode on each owned
session, for the main session, and apply the ACPX flag per invocation. Do not
change global permission configuration. This is the user's chosen permission
setting, subject to the host's existing limits and task scope.

Bypass is not absolute. The adapter still raises a permission request for calls
that are bypass-immune, safety-sensitive, interactive, or forced by an explicit
`ask` rule. Report such a blocked request rather than claiming the work completed.

## Three-phase model setup

Option 2 presents exactly one phase at a time in plain text and waits for a
selection before advancing. Use controller `catalog` for instant display; it
prefers the writable refreshed catalog over [the bundled snapshot](assets/models.json).
Save each phase, displayed snapshot and draft via controller `draft`. Keep draft
selections separate from the active configuration until all three phases
complete. Do not contact the runtime merely to open the menus. Menu numbers refer
to the displayed snapshot, never a later reordered list. Pass every generated
phase, tuning or disambiguation menu through controller `format-menu --file`
with --menu-output before displaying menuView. Follow the shared presentation
guide; do not highlight menus or double-frame already formatted output. The first
input line is CLI-MODE; the second is exactly Select Model, Select Reasoning Effort,
or Select Access Level for the current phase.

1. **Model — 1/3.** List `modelFamilies` by name: Sonnet 5, Fable 5.1, Opus 5.5 and
   Haiku 4.5 in the current snapshot, preserving the advertised order. Mark
   Opus 5.5 as the initial default. Include the cached date, `R. Refresh models`,
   and `B. Back` to the activation menu. The runtime also advertises an ambiguous
   `default` row for every selector; CLI-MODE omits it and requires a concrete
   verified choice.
2. **Effort — 2/3.** Show only the selected model's advertised efforts. The
   current snapshot advertises Low, Medium, High, Extra High and Max for Opus,
   Sonnet and Fable; Haiku has only Provider default. Mark High as the initial
   default where supported. Include `B. Back` to model. Effort is a
   separate `effort` config option here, not part of the model ID, so preserve
   the model value when applying it. A model switch can change or remove the
   effort control; if a model advertises none, show its single Provider default
   option explicitly rather than inventing levels.
3. **Access level — 3/3.** List the cached access aliases and native
   descriptions, with `allow` first, then auto-edit and prompt. Initially mark
   allow Default; later mark the saved access choice Default. Include `B. Back`
   to effort. Choosing access completes setup and proceeds to
   connectivity/settings verification; no fourth confirmation menu is required.

The runtime also advertises `plan` and `auto` modes and an `agent` selector.
CLI-MODE maps none of them: `plan` is a workflow state whose ExitPlanMode gate
needs interactive choices headless auto-approval cannot answer, and `auto` is
conditional. Do not silently substitute either for a requested access level.

## Bound-agent tuning

`/cli model`, `/cli effort` and `/cli access <choice>` run controller `tune --phase <phase> --apply`, exactly as the routing hook gives it. The controller matches the typed choice against the advertised options itself and applies a unique match to the same session, preserving unrelated settings; with no choice or an ambiguous one it returns the phase menu for a numbered reply. Never interpret the catalog yourself or invent a model, effort or access level. Applying a change reconfigures the existing session; partial failure gates dispatch until off/cleanup.

A verified model change rotates the ACP session id while the durable record id
and the conversation survive. Identity therefore tracks `acpxRecordId`; do not
report context loss from a rotated session id alone.

## Provider slash commands

Claude expands its own slash commands in the same ACP session. There is no
transport switch, no closed session and no lost history, so never tell the user
that a fresh conversation was started. The adapter publishes its command list as
a live `available_commands_update` notification. CLI-MODE reads persisted
`acpx.available_commands` metadata and caches streamed updates. Only when
neither source supplies a list does the runtime answer for an unseen command.

Commands the adapter removes from its advertised list are refused before
dispatch, because their UX lives in the interactive terminal: `clear`, `cost`,
`keybindings-help`, `login`, `logout`, `output-style:new`, `release-notes` and
`todos`. `/help` remains CLI-MODE help and `/cli` remains a host control.

## Session lifetime

One host-owned Claude conversation per Codex conversation/workspace, with
`--ttl 1800`. Settings changes reconfigure that same session. Idle process exit
is not off: resume the existing session and report actual context loss.
`/cli stop` gates routing, cancels and closes the owned session and verifies
shutdown. Claude decides whether to use its own native subagents; CLI-MODE never
starts a second session or a host worker pool.

## Utilization

Follow [activation confirmation](references/confirmation.md). Quota comes from
the native zero-turn `/usage` local command through
`scripts/usage-summary.py`, never from an agent prompt or a model's claim.

## Shared transaction and navigation rules

See [shared menu transactions](../../codex/skills/cli-mode/references/frontends.md#shared-menu-transactions).
