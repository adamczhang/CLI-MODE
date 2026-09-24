# Grok Build backend for CLI-MODE

/cli grok (also $cli grok, /cli grok-build) opens this frontend, case-insensitively.
/cli bind grok starts with saved defaults without a further menu confirmation.
/cli opens agent selection; /cli stop or /cli off shuts down. /help lists controls.
Requests to build, edit or explain this backend never activate it.

Read [the shared CLI-MODE rules](../../codex/skills/cli-mode/SKILL.md) and
[the ACPX backend guide](references/acpx.md). The shared skill and this internal
backend belong to the same source/distribution. If the shared file is missing,
report incomplete setup instead of inventing routing rules.

Default to the current Codex thread working directory, including any attached
worktree. Only use another directory when the user explicitly chooses it; never
substitute the source checkout, plugin cache or project root for the thread cwd.
Use the installed ACPX `grok-build` profile, preserving any configured launcher.
ACP is the only transport: Grok expands its own slash commands inside the same
session, so nothing switches to a native CLI conversation and no history is
discarded. Do not launch an untracked native task or install runtimes implicitly
during activation.

## Text activation menu

Follow [shared frontends and first-time checks](../../codex/skills/cli-mode/references/frontends.md).
For /cli grok, run frontend --agent grok-build with --menu-output at a task-owned
writable HTML path. Display the returned menuView using the shared inline
presentation guide. This is a menu in the Codex conversation, not a terminal
wizard. Do not activate merely because the skill was invoked. Follow `hostAccess`
and `pending.onboarding` before interpreting numbered replies. New User Detected
means a numbered selection runs `first-time-check --agent grok-build`; it never
means Yes to activation. Once setupReady is true, show the activation menu and
wait for Yes. In that menu only, 1/Yes selects defaults and 2 opens model setup.
B returns to the agent menu. No reply or elapsed time is acceptance.

The initial requested default is **Grok 4.7**, **High** reasoning effort, with
**Always approve**.

## Access has no native setting here

This runtime advertises **no permission-mode selector and no ACP session modes**.
Access is therefore enforced entirely by the ACPX client-side approval policy:

| CLI-MODE access | Applied as | Advertised behavior |
| --- | --- | --- |
| `allow` (default) | ACPX `--approve-all` | Auto-approve tool requests within host limits |
| `prompt` | ACPX `--approve-reads --non-interactive-permissions fail` | Default prompt flow; non-interactive requests are reported |

`auto-edit` is **not offered**. There is no native auto-approve-edits-only
setting to apply or verify, and CLI-MODE does not approximate one with host
policy. If the user asks for auto-edit, say it is unavailable for this backend
rather than substituting `allow` or `prompt`.

Because nothing native is set, activation verifies model and reasoning effort
only. Never report that a permission mode was applied and verified on the agent.

## Three-phase model setup

Option 2 presents exactly one phase at a time and waits for a selection. Use
controller `catalog` for instant display; it prefers the writable refreshed
catalog over [the bundled snapshot](assets/models.json). Save each phase and
draft via controller `draft`. Menu numbers refer to the displayed snapshot.
Pass every generated menu through controller `format-menu --file` with
--menu-output before displaying menuView.

1. **Model — 1/3.** List `modelFamilies` by name: Grok 4.7, Grok 4.7 Fast,
   Grok 4.6 and Grok 4.5 in the current snapshot, preserving the advertised
   order. Mark Grok 4.7 as the initial default. Include the cached date,
   `R. Refresh models`, and `B. Back`. Unlike the other backends, this runtime
   advertises no ambiguous `default` row: every value is concrete.
2. **Effort — 2/3.** Show the selected model's advertised efforts: Extra High,
   High, Medium and Low. Mark High as the initial default, which is also the
   runtime's own current value. Include `B. Back` to model. Effort is the
   separate `reasoning_effort` config option, not part of the model ID, so
   preserve the model value when applying it.
3. **Access level — 3/3.** List only the two supported aliases, with `allow`
   first and then prompt. Mark the saved access choice Default. Include
   `B. Back` to effort. Choosing access completes setup and proceeds to
   connectivity/settings verification.

## Bound-agent tuning

`/cli model`, `/cli effort` and `/cli access <choice>` run controller `tune --phase <phase> --apply`, exactly as the routing hook gives it. The controller matches the typed choice against the advertised options itself and applies a unique match to the same session, preserving unrelated settings; with no choice or an ambiguous one it returns the phase menu for a numbered reply. Never interpret the catalog yourself or invent a model, effort or access level. Applying a change reconfigures the existing session; partial failure gates dispatch until off/cleanup.

## Provider slash commands

Grok expands its own slash commands in the same ACP session. There is no
transport switch, no closed session and no lost history, so never tell the user
a fresh conversation was started. Where the runtime publishes an
`available_commands_update` notification, CLI-MODE caches it for the owned
session and refuses a command outside that observed list; before any list is
observed it admits the command and Grok answers for itself. `/help` remains
CLI-MODE help and `/cli` remains a host control.

## Inputs

This runtime advertises `promptCapabilities.image = false`: it does not accept
image input over ACP. Report that limitation rather than describing an attached
image or pretending it was delivered. Embedded text context is supported.

## Session lifetime

One host-owned Grok conversation per Codex conversation/workspace, with
`--ttl 1800`. Settings changes reconfigure that same session; an observed model
change reported `resumed: true` and kept the same session identity. Idle process
exit is not off: resume the existing session and report actual context loss.
`/cli stop` gates routing, cancels and closes the owned session and verifies
shutdown. Grok decides whether to use its own subagents; CLI-MODE never starts a
second session or a host worker pool.

## Utilization

Follow [activation confirmation](references/confirmation.md). The installed CLI
exposes no subscription rate-limit windows to a non-interactive caller, so
utilization is reported as explicitly unavailable.

`usage` without a leading slash is an interactive TUI modal and is unreachable
over ACP. `grok usage <SESSION_ID>` returns per-session token and cost totals
with no utilization percentage and no reset time. Do not substitute either for
subscription quota, do not send `usage` as a prompt to obtain it (that is billed
as ordinary model turns), and do not infer limits from a model's claim.

## Shared transaction and navigation rules

See [shared menu transactions](../../codex/skills/cli-mode/references/frontends.md#shared-menu-transactions).
