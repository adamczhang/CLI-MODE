# Grok Build backend through ACPX

Use the installed CLI's help to confirm syntax before first use. The configured
profile is `grok-build`; its launcher may differ from the built-in command.
Preserve that configuration. Do not print authentication/configuration secrets.

ACPX's built-in `grok-build` profile launches the installed `grok` CLI through
its own ACP stdio entrypoint (`grok agent stdio`). There is no adapter package
to download and no separate ACP runtime, unlike Antigravity. Install Grok Build
and complete its normal authentication flow before using it through ACPX.

Where Grok advertises `cached_token`, ACPX authenticates with the agent-managed
cached login. For non-browser and automation environments where Grok advertises
`xai.api_key`, ACPX also accepts `XAI_API_KEY`. CLI-MODE does not set either.

## Persistent commands

The shared controller is the normal entrypoint for these operations. The raw
commands below document ACPX and are for diagnosis/recovery of already-owned
sessions; do not bypass the controller's state/admission gate for ordinary work.

```powershell
acpx --version
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all grok-build sessions ensure --name $session
acpx --cwd $project --format json grok-build sessions show $session
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json grok-build -s $session --file $promptFile
acpx --cwd $project grok-build status -s $session
acpx --cwd $project grok-build cancel -s $session
acpx --cwd $project grok-build sessions close $session
```

Write exact prompt text to a UTF-8 file and supply its absolute path through
`--file`. Do not shell-escape arbitrary prompts manually.

## Settings

This runtime advertises exactly two config options. Apply each with the exact
advertised value and verify acceptance:

```powershell
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json grok-build -s $session set model grok-4.7
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json grok-build -s $session set reasoning_effort high
```

`set model` returns `action=model_set`; `set reasoning_effort` returns
`action=config_set`. An observed session advertised:

| Selector | Advertised values |
| --- | --- |
| `model` | `grok-4.7` (Grok 4.7, current), `grok-4.7-build-fast` (Grok 4.7 Fast), `grok-4.6`, `grok-4.5` |
| `reasoning_effort` | `xhigh` (Extra High), `high` (High, current), `medium`, `low` |

There is **no `mode` selector and no ACP session modes**, so there is nothing
native to set or verify for access. CLI-MODE maps its access labels to the ACPX
approval policy applied per invocation:

| CLI-MODE access | Applied as |
| --- | --- |
| `allow` (default) | `--approve-all` |
| `prompt` | `--approve-reads --non-interactive-permissions fail` |

`auto-edit` has no equivalent here and is not offered. Do not approximate it.

Two behaviors observed while verifying:

- An accepted model change reported `resumed: true` and left `acpxRecordId` and
  `acpSessionId` unchanged, so no context-loss warning is warranted. Identity
  still prefers the durable record id.
- Every advertised value is concrete: this runtime exposes no ambiguous
  `default` row for either selector.

## Capabilities

The observed `agentCapabilities` reported `promptCapabilities.image = false`,
so image input is not accepted over ACP; `embeddedContext` is supported.
Session capabilities covered list, resume and close. The runtime also advertised
xAI-specific hook and tool-override metadata that CLI-MODE does not use.

## What persists

CLI-MODE sets `--ttl 1800`: the owner and agent remain alive between prompts,
then shut down after 30 minutes idle once queued work drains. Explicit
`/cli stop` disables routing, cancels active owned work, closes the session and
verifies shutdown; `status` then reports `no-session`.

## Constraints

- Slash commands are expanded in-session; no transport switch and no lost history.
- No subscription quota is exposed; utilization is reported unavailable.
  `grok usage <SESSION_ID>` is per-session tokens and cost, not account limits.
- Do not change credentials, sign in, download a runtime, or switch providers
  silently when startup fails.

References: [ACPX Grok Build adapter](https://github.com/openclaw/acpx/blob/main/agents/GrokBuild.md),
[xAI Grok Build](https://docs.x.ai/build/overview).
