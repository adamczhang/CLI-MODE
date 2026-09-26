# CLI-MODE v0.3.7 — Agents that know each other

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Claude Code** or **Codex**, over ACPX.

**The project brief tells every agent who else is working, and what the
conversation is about.** `Agent_Working_Folder/BRIEF.md`, which every agent reads
before its task, now has three parts:

- **Agents running now:** each agent's name, working folder, whether it is
  working, and its last saved answer. CLI-MODE keeps it current: an agent you
  close drops out, a new one appears.
- **From the host:** when an agent starts, Claude Code or Codex writes a short,
  dated note on what the conversation has been working on (or "Nothing yet").
  Notes are kept, so they read as a history.
- **Your points**, added with `/cli brief-add`, as before.

Each task also tells the agent which one it is ("you are Grok GRO-4K"). On
Claude Code, CLI-MODE approves only the host note's edit of the brief, so a new
agent never asks you to allow it.

**Fixes from a code review of 0.3.6:**

- **`/cli undo` no longer undoes another agent's work.** It now restores only
  the files the agent's own tools edited, and names any others it left alone.
- **`/cli undo` keeps line endings.** With Git for Windows' default settings, a
  restored file used to come back with LF instead of CRLF endings.
- **The test gate's time limit ends the whole test run.** On Windows, a hung
  test run used to stall the agent's queue for good.
- **Stale approvals:** an agent's next turn settles its pending permission
  question, so a later `/cli approve` can't send an out-of-date approval.
- **Faster `/d` on Claude Code** after pasting images: the conversation
  transcript is read only when a new upload appears.

Also: two unused functions and 41 superseded validation records were removed.

## Install

Pick your host and run its block in PowerShell.

**Codex** (runs in the Codex desktop app):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.7
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.7.zip` from this
release, extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub, then run `/cli-mode:cli shortcuts` once:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.7 --sparse .claude-plugin plugins
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
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.7
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
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.7 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

## Validation and artifacts

The [v0.3.7 validation report](checks/v0.3.7-validation.md) records this
release's checks: the full offline suite, both install smokes, and live runs on
both hosts of a new agent's start: the host's note written in the brief (with no
permission prompt on Claude Code) and the agent list kept current.

Both archives and their SHA256 checksums are attached to the GitHub Release:
`cli-mode-codex-0.3.7.zip` (Codex) and `cli-mode-claude-0.3.7.zip` (Claude Code).
