# CLI-MODE v0.3.9 — One brief for every conversation, undo for every agent

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Claude Code** or **Codex**, over ACPX.

This release fixes what a code review of 0.3.8 and a full live validation on both
hosts found.

**The project brief is shared properly.**

- Text you write in `Agent_Working_Folder/BRIEF.md` yourself now stays: CLI-MODE
  edits only its own lines, under one lock.
- A Claude Code conversation and a Codex conversation working in the same folder
  now both appear in the brief's list of running agents, instead of removing each
  other's.
- With `/cli display instant` (Claude Code), an agent's start no longer leaves an
  unwritten host note behind.

**`/cli undo` works for every agent.** Copilot and Codex CLI name the file they
edit only in the edit's diff, so undo thought they had edited nothing; and with
`/cli progress quiet` no agent's edits were recorded at all. Both are fixed, and
Copilot's and Codex CLI's work rows now name the files they edit.

**Approvals ask only when they should.**

- An agent that asks first is no longer mistaken for one that acts without
  asking once its approved command runs.
- After `/cli approve`, an agent that acts without asking (Grok) may run commands
  and edit files for that turn, as its question says, even when it asks.
- Claude's commands don't say what kind of tool they are; approving one now covers
  a reworded retry in the same turn, and "approve always" is no longer offered for
  them (it would allow every unnamed tool for good).

**`/cli usage`:** Copilot and Codex CLI report an answer they can't read clearly
instead of failing.

## Install

Pick your host and run its block in PowerShell.

**Codex** (runs in the Codex desktop app):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.9
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.9.zip` from this
release, extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub, then run `/cli-mode:cli shortcuts` once:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.9 --sparse .claude-plugin plugins
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
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.9
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
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.9 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

## Validation and artifacts

The [v0.3.9 validation report](checks/v0.3.9-validation.md) records this
release's checks: the full offline suite, both install smokes, and live runs on
both hosts with five of the six agents.

Both archives and their SHA256 checksums are attached to the GitHub Release:
`cli-mode-codex-0.3.9.zip` (Codex) and `cli-mode-claude-0.3.9.zip` (Claude Code).
