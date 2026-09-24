# Cursor backend through ACPX

The configured profile is `cursor`; its launcher may differ from the built-in
command. Preserve that configuration. Do not print authentication secrets.

ACPX's built-in `cursor` profile launches `cursor-agent acp`. There is no
adapter package and no separate ACP runtime: the installed Cursor CLI speaks ACP
itself. Install it and complete `cursor-agent login` before use. On Windows the
installer places the CLI in `%LOCALAPPDATA%\cursor-agent` and adds that folder to
the user PATH, with `cursor-agent.cmd` shims over a versioned `versions\<build>`
folder. It also creates `agent.*` aliases, which can collide with another
vendor's `agent` on PATH; CLI-MODE always invokes the `cursor` ACPX profile, so
the collision does not affect routing.

If an install exposes ACP as `agent acp` instead, override the built-in command
in `~/.acpx/config.json` under `agents.cursor.command`.

## Persistent commands

The shared controller is the normal entrypoint. The raw commands below document
ACPX and are for diagnosis of already-owned sessions.

```powershell
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all cursor sessions ensure --name $session
acpx --cwd $project --format json cursor sessions show $session
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json cursor -s $session --file $promptFile
acpx --cwd $project cursor status -s $session
acpx --cwd $project cursor sessions close $session
```

## Settings

Two advertised config options, applied and verified per session:

```powershell
acpx ... --format json cursor -s $session set model "composer-2.5[fast=true]"
acpx ... --format json cursor -s $session set mode agent
```

`set model` returns `action=model_set`; `set mode` returns `action=config_set`.

| Selector | Observed values |
| --- | --- |
| `model` | The bundled snapshot has 39 advertised IDs and recorded runtime current `default[]` (Auto); CLI-MODE now requests `composer-2.5[fast=true]` by default. Examples: `grok-4.7[context=256k,reasoning_effort=high,fast=true]`, `claude-opus-5[thinking=true,context=300k,effort=high,fast=false]`, `gpt-5.6-sol[context=272k,reasoning=medium,fast=false]`, `gemini-3.1-pro[]` |
| `mode` | `agent` (current), `plan`, `ask` |

There is **no `reasoning_effort` selector and no permission mode**. Cursor's
native writes bypass ACP permission callbacks, so only allow is supported:

| CLI-MODE access | Applied as |
| --- | --- |
| `allow` (default) | `--approve-all` |

`prompt` cannot enforce a restriction in this runtime and is refused, including
in old cached catalogs. `auto-edit` has no equivalent and is not offered.

## Opaque model IDs

Advertised IDs carry bracketed settings and are opaque. ACPX accepts a bare name
only when exactly one bracketed variant matches, and rejects it as ambiguous
otherwise; an exact advertised ID always wins. CLI-MODE therefore stores and
sends the full advertised value and refuses a bare name that is not itself
advertised. Model changes use the connected session's current catalog, including
after reconnect, so a refreshed catalog can change which names are ambiguous.

## Observed behavior

- A verified model change rotates `acpSessionId` while `acpxRecordId` and the
  conversation persist. Identity tracks the record id; a rotated session id is
  not evidence of context loss.
- `agentCapabilities.promptCapabilities.image` was `true`, so this runtime
  accepts image input over ACP.
- `sessions close` then `status` reported `no-session`.

## Constraints

- Slash commands are expanded in-session; no transport switch, no lost history.
- No subscription quota is exposed by the CLI, so utilization is unavailable.
- Team-level MCP servers configured in the Cursor dashboard are not available in
  ACP mode, per Cursor's documentation.
- Do not change credentials, sign in, or switch providers silently on failure.

References: [ACPX Cursor adapter](https://github.com/openclaw/acpx/blob/main/agents/Cursor.md),
[Cursor CLI ACP](https://cursor.com/docs/cli/acp).
