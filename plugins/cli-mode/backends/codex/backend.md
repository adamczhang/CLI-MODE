# Codex CLI backend for CLI-MODE

Use /cli codex or $cli codex for setup, /cli bind codex for saved defaults,
and /cli stop for shutdown. Codex CLI is the sixth agent, distinct from the
Codex host handling the menu. Discussing development does not activate it.
Read [shared controls](../../codex/skills/cli-mode/SKILL.md),
[ACPX](references/acpx.md) and [confirmation](references/confirmation.md).

## Setup and settings

Install with `npm install -g @openai/codex`; authenticate with `codex login`.
`codex login status` checks sign-in without a model task. ACPX supplies
@agentclientprotocol/codex-acp. Preserve any configured codex launcher.
Use the exact host working directory, including its worktree.

The bundled catalog is a live snapshot from 2026-09-22. Initial defaults are
6 Sol / High / Full access. Model, reasoning_effort and mode are separate
ACP config selectors. Follow the shared model -> effort -> access setup flow.
Use each model's advertised effort options; never apply another model's list.
Use controller refresh to read owned session metadata and save the catalog
outside the plugin under catalogs/codex.json. Other models retain their model-specific cached efforts. Do not open a second
provider session beside the main session. Read back every requested setting;
unavailable or rejected settings leave routing gated.
An older cached catalog missing Sol or High gains the bundled initial choice
without discarding other cached models; live activation still verifies that
this account accepts the requested setting.

Access allow maps to agent-full-access plus ACPX --approve-all. Prompt is not offered: the native read-only preset permitted a workspace write
without an ACP approval callback during live validation. ACPX cannot enforce
a universal approval gate around these native writes. Automatic review (agent)
is not edits-only access, so auto-edit is not offered. Native collaboration mode
and fast mode are separate controls, not routing or access settings.

## Sessions and routing

One persistent codex ACP session per host conversation/workspace; TTL 1800.
Model/effort/access changes reuse its durable identity. Idle owner exit is not
shutdown. Off gates dispatch, cancels, closes and verifies the owned session.
Readiness must include the controller's fresh challenge marker.

Only /d or $d prompts reach the agent; the trigger is removed once before
provider command validation. Commands must be advertised by the ACP
adapter; terminal UI commands are not automatically available. Remain on ACP,
with no Antigravity native handoff. Pass whole user prompts unchanged; the Codex
provider owns any internal tools or delegation. Announce Passing to Codex...
and filter private reasoning through the shared public-event translator.

Inputs currently use the shared text/file-path sender. Do not claim chat
attachments were forwarded. Use the provider's configured account; no silent
API-key fallback or account switch. Subscription utilization is unavailable in
this integration until a matching provider-account quota source is implemented.

## Bound-agent tuning

`/cli model`, `/cli effort` and `/cli access <choice>` run controller `tune --phase <phase> --apply`, exactly as the routing hook gives it. The controller matches the typed choice against the advertised options itself and applies a unique match to the same session, preserving unrelated settings; with no choice or an ambiguous one it returns the phase menu for a numbered reply. Never interpret the catalog yourself or invent a model, effort or access level. Applying a change reconfigures the existing session; partial failure gates dispatch until off/cleanup. Codex offers only the allow access level (Full access), so that phase shows one row.

## Shared transaction and navigation rules

See [shared menu transactions](../../codex/skills/cli-mode/references/frontends.md#shared-menu-transactions).
