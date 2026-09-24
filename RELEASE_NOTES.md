# CLI-MODE v0.3.1 — Hooks that run on current Codex

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Codex** or **Claude Code**, over ACPX.

**Codex users: update.** Codex 0.155 runs Windows hook commands through
PowerShell, where v0.3.0's hook command is a parse error. Every CLI-MODE hook
exits with an error, so `/cli`, `/d` and `/help` are handled by Codex reading the
skill instead of by CLI-MODE. v0.3.1's hook command works in cmd.exe, PowerShell 7
and Windows PowerShell 5.1. Codex asks you to trust the updated hooks once.

Also in this release:

- **Codex menus.** Replies 1 and 2 on an agent's activation menu run their exact
  controls, and a `/d` task typed while Agent Settings is open is sent, as on
  Claude Code.
- **Claude Code access.** Raising an agent's access with `/cli access allow` or
  the settings access menu asks first. A message the open settings page cannot
  take says it was not sent, instead of disappearing.
- **Cleaner relays.** An answer that ends inside a code block no longer turns
  CLI-MODE's own lines into code, and terminal escape sequences from an agent are
  removed from chat and views.
- **Clearer errors.** Failed controls show their reason rather than Node's
  warnings, and Copilot says when `GH_TOKEN` overrides its own sign-in.
- **README.** It now leads with what CLI-MODE is and what it needs, and has a
  short FAQ; internals are in `docs/ARCHITECTURE.md`.

## Install

Pick your host and run its block in PowerShell.

**Codex** (runs in the Codex desktop app):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.1
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.1.zip` from this
release, extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub, then run `/cli-mode:cli shortcuts` once:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.1 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

**Using both?** Run both. They share CLI-MODE's ACPX copy and each agent's own
sign-in; conversations and settings stay separate per host.

## Upgrading from v0.3.0

A GitHub install is pinned to its tag, so updating it in place keeps v0.3.0. Move
it to the new tag instead; your saved CLI-MODE settings are kept.

**Codex:**

```powershell
codex plugin marketplace remove cli-mode
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.1
codex plugin add cli-mode@cli-mode
```

Then open **Plugins → CLI-MODE → Hooks** in the Codex desktop app and choose
**Trust all** (or review the updated definitions).

**Claude Code from the zip:** run the new zip's `install-claude.ps1`; it updates
in place and keeps your data.

**Claude Code from GitHub:** uninstall with `--keep-data` first, because removing
the marketplace otherwise deletes CLI-MODE's saved data:

```powershell
claude plugin uninstall cli-mode@cli-mode --keep-data
claude plugin marketplace remove cli-mode
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.1 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

## Validation and artifacts

The [v0.3.1 validation report](checks/v0.3.1-validation.md) records this
release's checks. The [0.3.0 full validation](checks/v0.3.0-full-validation.md)
covers both hosts and all six agents and found the defects fixed here. Cursor's
account is plan-limited, so Cursor is verified offline.

Both archives and their SHA256 checksums are attached to the GitHub Release:
`cli-mode-codex-0.3.1.zip` (Codex) and `cli-mode-claude-0.3.1.zip` (Claude Code).
