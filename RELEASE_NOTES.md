# CLI-MODE v0.3.6 — Approvals in the chat, attachments on /d

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Claude Code** or **Codex**, over ACPX.

**Approve an agent's requests from the chat.** At Prompt or Auto-edit access, an
agent that needs a permission its level does not grant stops and asks you:

```text
Grok GRO-4K asks to run commands: npm install
Its turn stopped for your answer (Prompt access). /cli approve lets it run commands and carry on,
/cli approve always lets it run commands from now on, /cli deny tells it no.
```

- **`/cli approve [name]`** sends the agent on with that kind of request
  (editing files, running commands, deleting, moving, fetching) allowed for that
  turn. Anything else it asks for stops the turn and asks again.
- **`/cli approve [name] always`** keeps that kind allowed while the agent runs.
- **`/cli deny [name]`** tells the agent no; it carries on without it.

Before this release those levels simply ended the turn with "approval requests
stop the turn". Allow access, every agent's default, is unchanged.

**Files and images attached to a `/d` reach the agent.** In the Claude Code and
Codex desktop apps, files and pasted images attached to a `/d` are copied into
each named agent's folder, `Agent_Working_Folder/<NAME>/attachments/`, and named
in its task. A `/d` with a file attached is no longer mistaken for a message to
your host. `/cli dir` lists an agent's attachments; they are removed when the
agent closes, while its own files and saved answers stay.

Also: the README's host table now lists CLI-MODE's features by what they do.

## Install

Pick your host and run its block in PowerShell.

**Codex** (runs in the Codex desktop app):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.6
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.6.zip` from this
release, extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub, then run `/cli-mode:cli shortcuts` once:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.6 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

**Using both?** Run both. They share one ACPX installation and each agent's own
sign-in; conversations and settings stay separate per host.

## Upgrading from an earlier 0.3 release

A GitHub install is pinned to its tag, so updating it in place keeps the old version. Move
it to the new tag instead; your saved CLI-MODE settings are kept.

**Codex:**

```powershell
codex plugin marketplace remove cli-mode
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.6
codex plugin add cli-mode@cli-mode
```

Coming from v0.3.0, then open **Plugins → CLI-MODE → Hooks** in the Codex
desktop app and choose **Trust all** (or review the updated definitions); from
v0.3.1 or later the hooks are unchanged.

**Claude Code from the zip:** run the new zip's `install-claude.ps1`; it updates
in place and keeps your data.

**Claude Code from GitHub:** uninstall with `--keep-data` first, because removing
the marketplace otherwise deletes CLI-MODE's saved data:

```powershell
claude plugin uninstall cli-mode@cli-mode --keep-data
claude plugin marketplace remove cli-mode
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.6 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

## Validation and artifacts

The [v0.3.6 validation report](checks/v0.3.6-validation.md) records this
release's checks: the full offline suite (approvals through a real ACPX with a
test agent), both install smokes, and a live run of a Grok Build agent at Prompt
access that asked, was approved and finished, then read an attached file.

Both archives and their SHA256 checksums are attached to the GitHub Release:
`cli-mode-codex-0.3.6.zip` (Codex) and `cli-mode-claude-0.3.6.zip` (Claude Code).
