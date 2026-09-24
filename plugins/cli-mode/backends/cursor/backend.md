# Cursor backend for CLI-MODE

/cli cursor (also $cli cursor) opens this frontend, case-insensitively.
/cli bind cursor starts with saved defaults without a further menu confirmation.
/cli opens agent selection; /cli stop or /cli off shuts down. /help lists controls.
Requests to build, edit or explain this backend never activate it.

Read [the shared CLI-MODE rules](../../codex/skills/cli-mode/SKILL.md) and
[the ACPX backend guide](references/acpx.md). If the shared file is missing,
report incomplete setup instead of inventing routing rules.

Default to the current Codex thread working directory, including any attached
worktree. Use the installed ACPX `cursor` profile, preserving any configured
launcher. ACP is the only transport: Cursor expands its own slash commands
inside the same session, so nothing switches to a native CLI conversation and
no history is discarded.

## Text activation menu

Follow [shared frontends and first-time checks](../../codex/skills/cli-mode/references/frontends.md).
For /cli cursor, run frontend --agent cursor with --menu-output at a task-owned
writable HTML path. Follow `hostAccess` and `pending.onboarding` before
interpreting numbered replies. A numbered selection on the first-start page runs
`first-time-check --agent cursor`; it never means Yes to activation. Once
setupReady is true, show the activation menu and wait for Yes.

The initial requested default is **composer-2.5** with **Always approve**. Use
the exact advertised ID `composer-2.5[fast=true]`; Auto (`default[]`) remains
available in the model list. ACP advertises no reasoning-effort control for
Composer 2.5, so effort reads **Provider default**. Do not claim that High was
applied or invent a bracketed High variant.

## A long, opaque model catalog

The observed session advertised **39 models**, and the list is plan-dependent.
Model values are **opaque advertised IDs that carry bracketed settings**, for
example `composer-2.5[fast=true]` and
`claude-opus-5[thinking=true,context=300k,effort=high,fast=false]`. Treat them as
opaque strings:

- Never reconstruct, trim or edit an ID, and never build one from a display name.
- A bare name such as `composer-2.5` is refused when the catalog holds a
  bracketed variant, because ACPX rejects an ambiguous bare name before sending
  the change. Select the full advertised ID.
- Menus show the advertised display name and apply the advertised value.

Because the catalog is long, the model phase **paginates**. No menu offers more
than ten selectable rows, counting `R. Refresh models`, `B. Back`, `X. Exit` and
the paging rows. Numbering is continuous across pages, so a number always
identifies the same entry in the displayed snapshot. Use
`controller options --phase model --agent cursor --page N`; the page rows are
`> Next page (n of m)` and `< Previous page (n of m)`. `B. Back` still means the
previous phase, not the previous page.

## Access has no native setting here

This runtime advertises **no permission-mode selector**. Live validation showed
native file writes bypassing ACP permission callbacks, so ACPX's read-only
approval policy cannot enforce a Prompt choice. Only Always approve is supported:

| CLI-MODE access | Applied as |
| --- | --- |
| `allow` (default) | ACPX `--approve-all` |

`prompt` is refused, including when restored from an older catalog or session.
Stop an old restricted session and explicitly select supported access to continue.
`auto-edit` is **not offered**: there is no native auto-approve-edits-only
setting to apply or verify, and CLI-MODE does not approximate one with host
policy. Never report that a permission mode was applied on the agent.

## The mode selector is not a permission level

Cursor advertises `mode` with `agent`, `plan` and `ask`. That chooses an
interaction style, not an approval level. CLI-MODE passes whole prompts through
for work, so it **pins `agent`** and applies and verifies it on every activation.
`plan` and `ask` are not exposed as access choices and are not silently
substituted. If a user asks for them, say they are not offered by this backend.

## Three-phase setup

1. **Model — 1/3.** Paginated as above. Mark composer-2.5 as the initial default, and
   mark the saved model `(current)` on whichever page it appears.
2. **Effort — 2/3.** This runtime advertises no effort control, so show the
   single explicit `Provider default` option rather than inventing levels.
3. **Access level — 3/3.** Only `allow` (Always approve).

Verification checks the accepted model and the pinned mode. A verified model
change rotates the ACP session id while `acpxRecordId` and the conversation
persist, so identity tracks the record id and a rotated session id is never
reported as context loss.

## Inputs

The observed session reported `promptCapabilities.image = true`, so this runtime
accepts image input over ACP. CLI-MODE's sender is still text-only, so include
agent-readable local paths in the prompt rather than relying on Codex chat
attachments.

## Utilization

Follow [activation confirmation](references/confirmation.md). The installed CLI
exposes no subscription quota command, so utilization is reported as explicitly
unavailable. Do not substitute dashboard figures or a model's claim.

## Bound-agent tuning

`/cli model`, `/cli effort` and `/cli access <choice>` run controller `tune --phase <phase> --apply`, exactly as the routing hook gives it. The controller matches the typed choice against the advertised options itself and applies a unique match to the same session, preserving unrelated settings; with no choice or an ambiguous one it returns the phase menu for a numbered reply. Never interpret the catalog yourself or invent a model, effort or access level. Applying a change reconfigures the existing session; partial failure gates dispatch until off/cleanup. Cursor offers a single Provider default effort and only the allow access level, so those phases show one row.

## Shared transaction and navigation rules

See [shared menu transactions](../../codex/skills/cli-mode/references/frontends.md#shared-menu-transactions).
