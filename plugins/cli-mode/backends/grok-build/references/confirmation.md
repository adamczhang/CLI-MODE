# Successful activation confirmation

Only after the main ACP session responds successfully and accepted model,
reasoning effort and workspace have been verified, render the confirmation.

## Utilization is unavailable for this backend

The installed Grok Build CLI exposes **no subscription rate-limit windows**
to a non-interactive caller. Two usage surfaces exist and neither is quota:

- `usage` (no leading slash) is a **TUI modal** inside the interactive client.
  It cannot be reached headlessly, and CLI-MODE drives the agent over ACP.
- `grok usage <SESSION_ID>` is a shell subcommand that prints **persisted token
  and cost totals for one session**. Verified output carries `inputTokens`,
  `outputTokens`, `cachedReadTokens`, `reasoningTokens`, `totalTokens`,
  `modelCalls`, `costUsdTicks` and per-turn rows. It contains no utilization
  percentage and no reset timestamp. It also requires a session id, so it
  cannot be queried before the first turn.

There is therefore no helper script for this backend and no quota query is made
at activation. Sending `usage` as a prompt is **not** a substitute: it is not a
local command and is billed as ordinary model turns.

Show no utilization estimate; use the shared unavailable-usage fallback.


## Shared activation message

After verified activation, run controller `--message-output <path> activation-message`. Display its returned
messageView without rewriting it. The [shared activation contract](../../../codex/skills/cli-mode/references/activation.md)
owns the template, styling and usage fallback for every backend. Do not compose
another confirmation or run a separate quota query; the controller calls the
native helper where supported. Usage failure does not turn successful activation
into a failure. No provider work or extra model prompt is used to retrieve quota.
