# CLI-MODE v0.3.8 — Honest approvals and /cli usage

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Claude Code** or **Codex**, over ACPX.

**`/cli usage`: what each agent has used, from its own CLI.** One command asks
every running agent at once, with no model request (`/cli usage <name>` asks
one):

```text
Claude CLA-1A:
  Five hour: 7% used | resets in 0 days 3 hours
  Weekly: 18% used | resets in 5 days 6 hours
Codex COD-2B:
  Weekly: 0% used | resets in 6 days 23 hours
Copilot COP-3C:
  Chat requests: 12% used (177 of 200 left) | resets in 4 days 6 hours
Grok GRO-4D:
  Usage reporting not supported through its CLI
```

Claude, Antigravity, Codex CLI and GitHub Copilot report their limits (Copilot's
when the GitHub CLI is signed in as the same account); Grok Build and Cursor
don't report theirs through their CLIs, and say so.

**Honest approvals for agents that act without asking.** Grok Build runs
commands and edits files without asking first, so an approval can't be limited
to one kind for it. Its question now says that `/cli approve` lets it act
freely for that one turn, and `/cli approve always` is refused (use
`/cli access allow` for that). Agents that ask first keep kind-limited
approvals, as before.

**Fixes found by a full live validation on the Codex host:**

- A command Grok runs without asking is now a proper question ("asks to run
  commands: git status"), so `/cli approve` works for it.
- With `/cli progress quiet`, an agent's text on either side of a tool call no
  longer runs together mid-line.

Also: `checks/codex_release_validation.py` and a report of the validation.

## Install

Pick your host and run its block in PowerShell.

**Codex** (runs in the Codex desktop app):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.8
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.8.zip` from this
release, extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub, then run `/cli-mode:cli shortcuts` once:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.8 --sparse .claude-plugin plugins
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
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.8
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
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.8 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

## Validation and artifacts

The [v0.3.8 validation report](checks/v0.3.8-validation.md) records this
release's checks: the full offline suite, both install smokes, the live Codex
validation (33 of 33 feature steps), and `/cli usage` run live across all six
agent kinds.

Both archives and their SHA256 checksums are attached to the GitHub Release:
`cli-mode-codex-0.3.8.zip` (Codex) and `cli-mode-claude-0.3.8.zip` (Claude Code).
