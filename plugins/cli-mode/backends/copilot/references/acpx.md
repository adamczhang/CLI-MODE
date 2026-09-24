# GitHub Copilot backend through ACPX

The configured profile is `copilot`; its launcher may differ from the built-in
command. Preserve that configuration. Do not print authentication secrets.

ACPX's built-in `copilot` profile launches `copilot --acp --stdio`. There is no
adapter package and no separate runtime: the installed Copilot CLI speaks ACP
itself. It requires a release that supports ACP stdio mode; older `copilot`
binaries fail before ACP startup. Authenticate with `copilot login` first.

## Persistent commands

The shared controller is the normal entrypoint. The raw commands below document
ACPX and are for diagnosis of already-owned sessions.

```powershell
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all copilot sessions ensure --name $session
acpx --cwd $project --format json copilot sessions show $session
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json copilot -s $session --file $promptFile
acpx --cwd $project copilot sessions close $session
```

## Settings

Two advertised config options, and no others:

| Selector | Category | Observed values |
| --- | --- | --- |
| `allow_all` | `permissions` | `on`, `off` (current `off`) |
| `mode` | `mode` | ACP session-mode URIs: `…#agent` (current), `…#plan`, `…#autopilot` |

```powershell
acpx ... --format json copilot -s $session set allow_all on
acpx ... --format json copilot -s $session set mode "https://agentclientprotocol.com/protocol/session-modes#agent"
```

Both were applied and read back successfully.

**No model catalog is advertised.** `available_models`,
`available_model_names`, `current_model_id` and `model_control` were all null,
so there is nothing to select or verify. Model choice belongs to the Copilot
CLI through its own `--model` flag on the configured launcher, not to the ACP
session. CLI-MODE therefore never sets a model here and never claims one was
applied. There is no reasoning-effort selector either.

Access maps to the native control plus the ACPX policy applied per invocation:

| CLI-MODE access | Native `allow_all` | ACPX |
| --- | --- | --- |
| `allow` (default) | `on` | `--approve-all` |
| `prompt` | `off` | `--approve-reads --non-interactive-permissions fail` |

`auto-edit` has no equivalent, because `allow_all` is on/off with no
edits-only value, and is not offered.

Session-mode values are full URIs. Treat them as opaque: do not shorten,
reconstruct or compare them by fragment alone.

## Observed behavior

- `agentCapabilities.promptCapabilities.image` was `true`, so this runtime
  accepts image input over ACP; `embeddedContext` is supported and `audio` is not.
- `sessionCapabilities` covered `close` and `list`, and `loadSession` was true.
- ACPX emits a Node `DEP0190` deprecation warning when spawning this agent. It
  is noise from the launcher, not an error, and appears on stderr, which
  CLI-MODE drops wholesale.

## Constraints

- Slash commands are expanded in-session; no transport switch, no lost history.
- No subscription quota is exposed. `billing` is an interactive slash command,
  not a shell subcommand, and `--usage-output-file` is per-run accounting.
- Requires an ACP-capable Copilot release; older binaries fail before startup.
- Do not change credentials, sign in, or switch providers silently on failure.

References: [ACPX Copilot adapter](https://github.com/openclaw/acpx/blob/main/agents/Copilot.md),
[Copilot CLI docs](https://docs.github.com/copilot/how-tos/copilot-chat/use-copilot-chat-in-the-command-line).
