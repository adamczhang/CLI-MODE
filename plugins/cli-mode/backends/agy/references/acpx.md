# AGY backend through ACPX

Use the installed CLI's help to confirm syntax before first use. The configured
profile is `antigravity`; its launcher may differ from the built-in executable.
Preserve that configuration. Do not print authentication/configuration secrets.

## Persistent commands

The shared controller is the normal entrypoint for these operations. The raw
commands below document ACPX and are for diagnosis/recovery of already-owned
sessions; do not bypass the controller's state/admission gate for ordinary work.

These PowerShell examples assume `$project` is an absolute target workspace and
`$session` is the unique name recorded for this conversation. Use the actual
chosen values as safely quoted arguments; never interpolate user prose into
shell code.

```powershell
acpx --version
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all antigravity sessions ensure --name $session
acpx --cwd $project --format json antigravity sessions show $session
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json antigravity -s $session --file $promptFile
acpx --cwd $project antigravity status -s $session
acpx --cwd $project antigravity sessions history $session
acpx --cwd $project antigravity cancel -s $session
acpx --cwd $project antigravity sessions close $session
```

Write exact prompt text to a UTF-8 file using a file-writing API, and supply its
absolute path through `--file`. Do not shell-escape arbitrary prompts manually.
Use a task-specific temporary directory outside tracked project files. Retain
files while requests may still be reading them; remove only owned temporary
files after completion. Structured output provides real tool/progress events.
Start commands with a short yield and poll their execution handle in bounded
intervals so Codex can relay meaningful updates and respond to steering.

The activation menu's default access is `allow`. It selects native `mode=yolo`
and ACPX `--approve-all` for owned main-session calls, including session creation
and reconnecting controls. Apply the native setting and verify acceptance:

```powershell
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json antigravity -s $session set mode yolo
```

The catalog maps actual advertised native modes to CLI-MODE access labels:

| CLI-MODE access | Native mode | Advertised behavior |
| --- | --- | --- |
| `allow` (default) | `yolo` | Auto-approve all tools |
| `prompt` | `default` | Default permission prompt flow |

Auto-edit is unsupported: native `auto_edit` still routes file writes through
ACPX client permissions, where approve-reads denies them. Do not substitute
approve-all because it would also authorize non-edit operations.

These labels are CLI-MODE aliases; native names/values remain in the cache.
Refresh updates both model and access metadata. If the user requests a stricter
access choice, remove `--approve-all` and align the ACPX policy with that choice;
do not leave host auto-approval defeating native prompts. Unsupported interactive
permission flows must be surfaced rather than silently approved.
Keep it explicit on calls rather than relying on a remembered global default.
It does not authorize out-of-scope actions or bypass host restrictions. A later
user permission change must update the mode's calls. `--auth-policy skip` allows
this runtime to use its existing internal OAuth login. ACPX's `fail` policy
rejects advertised auth methods when explicit ACPX credentials are absent even
if the runtime has its own login. Neither policy guarantees that an arbitrary
custom launcher cannot initiate browser login internally. A launcher requiring
interactive login must be configured and signed in separately before activation.

## Live model selection

Read controller `catalog` first for a fast model menu. It prefers the writable
catalog under the state directory and falls back to `assets/models.json`. Its entries
are a timestamped observation of this configured profile, not permanent model
availability. Include R Refresh and B Back. R uses controller `refresh`. It reads
owned-session metadata, preserves the old cache on failure, and never opens an
inspection session or changes the model. Stored metadata can be stale; the result
reports that limitation. No eligible session means cached choices are retained.

Missing authenticated metadata is a blocker, not an empty list to fill by guess.

Apply the selected model with the exact returned ID:

```powershell
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json antigravity -s $session set model $modelId
acpx --cwd $project --auth-policy skip --ttl 1800 --approve-all --format json antigravity -s $session set $reasoningKey $highValue
```

Run the second command only if the live adapter advertises a separate reasoning
control, using its actual key and value. A model switch may change or remove
reasoning choices, so read its accepted configuration before selecting High.
An advertised combined model/effort variant needs no invented second control.
Verify accepted settings before reporting readiness. The current snapshot
advertises `gemini-3.8-flash-high`; it encodes model and High reasoning together.
The runtime's advertised current model was `gemini-3.7-flash-high`, which is
distinct from CLI-MODE's requested default. Native session modes advertised were
`default`, `auto_edit`, and `yolo`. Catalog discovery records the observed native
mode; it does not activate or change it. Activation must separately set and verify
`yolo` before reporting the default access as applied.

Use `sessions new --name $session` only for a deliberate reset/new conversation.
`sessions ensure` preserves the existing main conversation. Never create host
worker sessions. Antigravity decides whether to use its own native subagents.
Keep the main conversation available for follow-ups.

## What persists

CLI-MODE sets `--ttl 1800`: the owner and agent remain alive between prompts,
then shut down after 30 minutes idle once queued work drains. This overrides
ACPX's five-minute default for owned mode sessions only. Use it on every prompt,
including readiness, and any control that may launch the owner. Do not close
the main session after an answer. Catalog refresh reuses owned main-session metadata only;
without it, report cached choices rather than opening an inspection session.

Saved session records survive idle shutdown under `~/.acpx/sessions`; the next
prompt attempts to resume them. Explicit `/cli stop` disables routing,
cancels active owned work, closes main and any legacy owned sessions, and verifies shutdown. `sessions
close` soft-closes history rather than deleting it. After explicit off, require
reactivation; do not auto-resume closed sessions as if they merely timed out.

ACPX can attempt provider resume/load on reconnect and may fall back to a fresh
provider conversation. Observe returned session identity and context continuity;
disclose a fresh conversation without silently altering future prompts or
claiming successful memory restoration. Status describes process health, not proof
that a task finished. Inspect streamed completion/error events and artifacts.

## Antigravity constraints

- The official ACP runtime uses its own installation and sign-in, separately
  from the interactive Antigravity CLI and IDE. The local configured profile
  already points to a launcher; source creation must not replace it.
- Only use models/configuration options actually advertised by the session.
  Host Codex tools and native AGY UI commands do not automatically exist in ACP.
- Antigravity fixed-choice user questions arrive as `interaction_` permission
  requests. ACPX cancels these even under approve-all: selecting a tool permission
  is not answering a question. Report the limitation and gather missing intent
  through the host. Send that intent in a subsequent ordinary prompt when it can
  resolve the issue; otherwise report that a compatible interactive client is
  required. Do not pretend to have completed the canceled interaction.
- Do not change credentials, sign in, download a runtime, or switch providers
  silently when startup fails.

References: [ACPX sessions](https://github.com/openclaw/acpx/blob/main/docs/sessions.md),
[Antigravity adapter](https://github.com/openclaw/acpx/blob/main/agents/Antigravity.md).
