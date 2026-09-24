# Successful activation confirmation

The shared activation-message command invokes this backend's native usage-summary.py
helper after verified activation. Do not run the helper a second time.

This uses the native AGY read-only `/usage` command, not an agent prompt through
ACPX. This is an exception to using ACPX for delegated work: it only retrieves
account metadata. On the installed CLI it returns `command.name=usage`, structured
quota groups, `num_turns=0`, and zero tokens. Never ask a model to guess quota or
parse its conversational claims as account data. Unsupported versions or failed
queries must produce unavailable, not an inferred percentage.

The utilization below is retrieved from the signed-in native CLI account. Keep
that source in the helper metadata; do not add an account-match warning to the
activation message or claim that the ACP and native accounts have been matched.
A known account mismatch must produce unavailable utilization.


## Shared activation message

After verified activation, run controller `--message-output <path> activation-message`. Display its returned
messageView without rewriting it. The [shared activation contract](../../../codex/skills/cli-mode/references/activation.md)
owns the template, styling and usage fallback for every backend. Do not compose
another confirmation or run a separate quota query; the controller calls the
native helper where supported. Usage failure does not turn successful activation
into a failure. No provider work or extra model prompt is used to retrieve quota.
