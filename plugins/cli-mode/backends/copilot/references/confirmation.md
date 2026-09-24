# Successful activation confirmation

Only after the main ACP session responds successfully and the accepted access
and pinned interaction mode have been verified, render the confirmation.

## Utilization is unavailable for this backend

GitHub Copilot exposes **no subscription quota to a non-interactive caller**:

- `billing` is an **interactive slash command** inside the Copilot client, not a
  shell subcommand. `copilot billing` is rejected as an invalid command format,
  and CLI-MODE drives the agent over ACP, so the interactive surface is
  unreachable.
- `--usage-output-file` writes statistics for **one run**, which is per-run
  accounting rather than subscription quota.

No quota query runs at activation and there is no helper script for this
backend. It has no subscription quota command available through this integration.
Never name a specific Copilot model when ACP reports Provider default.


## Shared activation message

After verified activation, run controller `--message-output <path> activation-message`. Display its returned
messageView without rewriting it. The [shared activation contract](../../../codex/skills/cli-mode/references/activation.md)
owns the template, styling and usage fallback for every backend. Do not compose
another confirmation or run a separate quota query; the controller calls the
native helper where supported. Usage failure does not turn successful activation
into a failure. No provider work or extra model prompt is used to retrieve quota.
