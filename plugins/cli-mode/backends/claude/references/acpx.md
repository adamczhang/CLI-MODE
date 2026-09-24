# Claude Code backend through ACPX

Use the installed CLI's help to confirm syntax before first use. The configured
profile is `claude`; its launcher may differ from the built-in command.
Preserve that configuration. Do not print authentication/configuration secrets.

ACPX pins the adapter package range and launches
`npx -y @agentclientprotocol/claude-agent-acp`, so there is no separate ACP
runtime download or sign-in as there is for Antigravity. `npx` must be on PATH.
Update ACPX itself to pick up adapter fixes.

Built-in `acpx claude` sessions load Claude project and local settings but not
user settings, so a user-level `apiKeyHelper` does not apply by default while
ambient environment credentials still do. `ACPX_CLAUDE_INCLUDE_USER_SETTINGS=1`
opts back in. CLI-MODE does not set it.

## Persistent commands

The shared controller is the normal entrypoint for these operations. The raw
commands below document ACPX and are for diagnosis/recovery of already-owned
sessions; do not bypass the controller's state/admission gate for ordinary work.

```powershell
acpx --version
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all claude sessions ensure --name $session
acpx --cwd $project --format json claude sessions show $session
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json claude -s $session --file $promptFile
acpx --cwd $project claude status -s $session
acpx --cwd $project claude cancel -s $session
acpx --cwd $project claude sessions close $session
```

Write exact prompt text to a UTF-8 file and supply its absolute path through
`--file`. Do not shell-escape arbitrary prompts manually. Structured output
provides real progress events.

## Settings

Model, effort and access are three separate advertised config options. Apply
each with the exact advertised value and verify acceptance:

```powershell
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json claude -s $session set model opus
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json claude -s $session set effort high
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json claude -s $session set mode bypassPermissions
```

`set model` returns `action=model_set`; `set effort` and `set mode` return
`action=config_set`. An observed session advertised:

| Selector | Advertised values |
| --- | --- |
| `model` | `default`, `sonnet` (Sonnet 5), `claude-fable-5-1[1m]` (Fable 5.1), `opus` (Opus 5), `haiku` (Haiku 4.5) |
| `effort` | `default`, `low`, `medium`, `high`, `xhigh`, `max` |
| `mode` | `default` (Manual), `acceptEdits`, `plan`, `auto`, `bypassPermissions` |
| `agent` | `default` plus any configured subagent |

The catalog maps advertised native modes to CLI-MODE access labels:

| CLI-MODE access | Native mode | Advertised behavior |
| --- | --- | --- |
| `allow` (default) | `bypassPermissions` | Bypass all permission checks |
| `auto-edit` | `acceptEdits` | Auto-approve file edit tools |
| `prompt` | `default` | Default permission prompt flow |

`plan` and `auto` are advertised but unmapped, and `agent` stays at `default`.
Do not substitute one for a requested access level. If the user requests a
stricter access choice, remove `--approve-all` and align the ACPX policy with
that choice. `--auth-policy skip` lets this runtime use its existing login.

Two behaviors matter when verifying:

- Config options are rebuilt on a model switch. A `fast` selector appears only
  after a model is set, so verify the three options that were requested rather
  than asserting a fixed option set.
- A model change rotates `acpxSessionId` while `acpxRecordId` and the
  conversation persist. Track the record id; a rotated session id is not
  evidence of context loss.

## Live model selection

Read controller `catalog` first for a fast model menu. It prefers the writable
catalog under the state directory and falls back to `assets/models.json`. Its
entries are a timestamped observation of this configured profile, not permanent
model availability. Include R Refresh and B Back. Refresh reads the main
session's advertised metadata when one exists; only before activation can a
temporary setup inspection open and close a session. Missing authenticated
metadata is a blocker, not an empty list to fill by guess.

ACPX does not opt into the adapter's `recommendedValue` extension, so every
selector still advertises an ambiguous `default` row. CLI-MODE omits it and
requires a concrete verified choice.

## What persists

CLI-MODE sets `--ttl 1800`: the owner and agent remain alive between prompts,
then shut down after 30 minutes idle once queued work drains. Saved model and
config selections are restored after reconnect without replacing the
conversation. Explicit `/cli stop` disables routing, cancels active owned work,
closes the session and verifies shutdown; `status` then reports `no-session`.

## Claude Code constraints

- Slash commands are expanded in-session. Terminal-only commands are not
  advertised and are refused before dispatch.
- `AskUserQuestion` is routed through ACP elicitation when the client supports
  it and is otherwise disabled at session creation. Report a blocked interactive
  question rather than pretending to have answered it.
- Bypass mode does not auto-allow every callback. Safety-sensitive, interactive
  or explicitly asked rules still raise a permission request.
- Do not change credentials, sign in, download a runtime, or switch providers
  silently when startup fails.

References: [ACPX Claude adapter](https://github.com/openclaw/acpx/blob/main/agents/Claude.md),
[claude-agent-acp](https://github.com/agentclientprotocol/claude-agent-acp).
