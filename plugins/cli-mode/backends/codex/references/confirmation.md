# Codex activation

Provider-account quota retrieval is not implemented; never substitute host-account usage.

## Shared activation message

After verified activation, run controller `--message-output <path> activation-message`. Display its returned
messageView without rewriting it. The [shared activation contract](../../../codex/skills/cli-mode/references/activation.md)
owns the template, styling and usage fallback for every backend. Do not compose
another confirmation or run a separate quota query; the controller calls the
native helper where supported. Usage failure does not turn successful activation
into a failure. No provider work or extra model prompt is used to retrieve quota.
