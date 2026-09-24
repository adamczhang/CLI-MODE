# Successful activation confirmation

Only after the main ACP session responds successfully and the accepted model,
pinned interaction mode and workspace have been verified, render the
confirmation.

## Utilization is unavailable for this backend

The installed Cursor CLI exposes **no subscription quota command**. Its
subcommands cover authentication (`login`, `logout`, `status`/`whoami`), model
listing (`models`, `--list-models`), MCP, plugins and workers. There is no usage
or limits command, so no quota query runs at activation and there is no helper
script for this backend.

Use the shared unavailable-usage fallback.


## Shared activation message

After verified activation, run controller `--message-output <path> activation-message`. Display its returned
messageView without rewriting it. The [shared activation contract](../../../codex/skills/cli-mode/references/activation.md)
owns the template, styling and usage fallback for every backend. Do not compose
another confirmation or run a separate quota query; the controller calls the
native helper where supported. Usage failure does not turn successful activation
into a failure. No provider work or extra model prompt is used to retrieve quota.
