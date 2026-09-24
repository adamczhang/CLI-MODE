# Successful activation confirmation

The shared activation-message command invokes this backend's native usage-summary.py
helper after verified activation. Do not run the helper a second time.

This uses the native Claude Code read-only `/usage` local command, not an agent
prompt through ACPX. This is an exception to using ACPX for delegated work: it
only retrieves account metadata. The installed CLI reports `local_command_run.command=usage`,
`num_turns=0` and zero cost, and the helper rejects anything else. `--output-format
stream-json --verbose` is required: the plain JSON envelope carries only rendered
markdown, while the stream carries the structured `usage_report`. Never ask a
model to guess quota or parse its conversational claims as account data.
Unsupported versions or failed queries must produce unavailable, not an inferred
percentage.

The helper reports percent used exactly as the CLI reports it; no remaining-fraction
conversion is applied. Reset timestamps are timezone-aware ISO 8601 values, and
countdowns are computed at display time. Windows are iterated rather than assumed:
a subscription with a separate Opus or Sonnet weekly cap returns extra rows, and
those render as additional lines without a code change.

The utilization below is retrieved from the signed-in native CLI account. Keep
that source in the helper metadata; do not add an account-match warning to the
activation message or claim that the ACP and native accounts have been matched.
A known account mismatch must produce unavailable utilization.

## Who is paying

`account.billedTo` distinguishes `subscription` from `api-key`. Claude Code ranks
an `apiKeyHelper` or environment key above a `/login` subscription, so when
`billedTo` is `api-key` the turn is billed to that key and subscription rate
limits may be absent. CLI-MODE exists to spend a subscription, so report this
plainly instead of showing an empty utilization block:

Use the shared unavailable-usage message; retain billing details in diagnostic metadata.


## Shared activation message

After verified activation, run controller `--message-output <path> activation-message`. Display its returned
messageView without rewriting it. The [shared activation contract](../../../codex/skills/cli-mode/references/activation.md)
owns the template, styling and usage fallback for every backend. Do not compose
another confirmation or run a separate quota query; the controller calls the
native helper where supported. Usage failure does not turn successful activation
into a failure. No provider work or extra model prompt is used to retrieve quota.
