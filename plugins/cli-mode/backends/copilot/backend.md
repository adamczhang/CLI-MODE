# GitHub Copilot backend for CLI-MODE

/cli copilot (also $cli copilot) opens this frontend, case-insensitively.
/cli bind copilot starts with saved defaults without a further menu confirmation.
/cli opens agent selection; /cli stop or /cli off shuts down. /help lists controls.
Requests to build, edit or explain this backend never activate it.

Read [the shared CLI-MODE rules](../../codex/skills/cli-mode/SKILL.md) and
[the ACPX backend guide](references/acpx.md). If the shared file is missing,
report incomplete setup instead of inventing routing rules.

Default to the current Codex thread working directory, including any attached
worktree. Use the installed ACPX `copilot` profile, preserving any configured
launcher. ACP is the only transport: Copilot expands its own slash commands
inside the same session, so nothing switches to a native CLI conversation and
no history is discarded.

## The narrowest catalog of any backend

An observed session advertised **no model catalog and no reasoning-effort
control**: `available_models`, `available_model_names`, `current_model_id` and
`model_control` were all null. Only two selectors exist, `allow_all` and `mode`.

Consequently:

- **Model — 1/3** shows one explicit `Provider default` row. CLI-MODE does not
  choose a Copilot model and never sets one on the session. Model choice belongs
  to the Copilot CLI itself, through its own `--model` flag on the configured
  ACPX launcher.
- **Effort — 2/3** shows one explicit `Provider default` row. Never invent
  levels for a runtime that advertises none.
- **Access — 3/3** is the only phase with a real choice.

Activation verifies the settings it requested. Because no model is requested,
no model is verified, and the confirmation must not claim one was applied.

## Access is a real native control here

Unlike Grok Build and Cursor, this runtime advertises a genuine permission
selector, `allow_all`, in the `permissions` category. CLI-MODE applies and
verifies it:

| CLI-MODE access | Native `allow_all` | Also applied |
| --- | --- | --- |
| `allow` (default) | `on` | ACPX `--approve-all` |
| `prompt` | `off` | ACPX `--approve-reads --non-interactive-permissions fail` |

`auto-edit` is **not offered**: `allow_all` is on/off with no
auto-approve-edits-only value, so there is nothing native to apply or verify.
CLI-MODE does not approximate one with host policy.

## The mode selector is not a permission level

Copilot advertises `mode` with ACP session-mode URIs:
`…/session-modes#agent`, `#plan` and `#autopilot`. That chooses an interaction
style, not an approval level. CLI-MODE passes whole prompts through for work, so
it **pins Agent** and applies and verifies it on every activation. Plan and
Autopilot are not exposed as access choices and are never silently substituted.
Treat the URIs as opaque values; do not shorten or reconstruct them.

## Bound-agent tuning

`/cli access` is the only tuning phase with advertised choices here. `/cli model`
and `/cli effort` show their single Provider default row rather than failing, so
the shared controls stay consistent across agents. Never invent a model or
effort this runtime does not advertise. `/cli model`, `/cli effort` and `/cli access <choice>` run controller `tune --phase <phase> --apply`, exactly as the routing hook gives it.

## Inputs

The observed session reported `promptCapabilities.image = true`, so this runtime
accepts image input over ACP. CLI-MODE's sender is still text-only, so include
agent-readable local paths in the prompt rather than relying on Codex chat
attachments.

## Session lifetime

One host-owned Copilot conversation per Codex conversation/workspace, with
`--ttl 1800`. Settings changes reconfigure that same session. Idle process exit
is not off. `/cli stop` gates routing, cancels and closes the owned session and
verifies shutdown.

## Utilization

Follow [activation confirmation](references/confirmation.md). `billing` is an
interactive slash command inside the Copilot client, not a shell subcommand, so
it is unreachable over ACP. Utilization is reported as explicitly unavailable.

## Shared transaction and navigation rules

See [shared menu transactions](../../codex/skills/cli-mode/references/frontends.md#shared-menu-transactions).
