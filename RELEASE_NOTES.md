# CLI-MODE v0.3.3 — Antigravity's temp folder

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Claude Code** or **Codex**, over ACPX.

**Antigravity no longer fills your temp folder.** Antigravity's ACP server
unpacks about 1.25 GB into `%TEMP%` every time it starts, and deletes that copy
only when it exits by itself. CLI-MODE ends it when an agent closes or its idle
timeout passes, which skipped that clean-up, so every start left another copy
behind: regular use reached hundreds of gigabytes.

Now each Antigravity server unpacks into a temp folder of its own, and every
start removes the folders of servers that have ended. At most one copy is kept
between sessions, and it goes at the next start.

**Clearing copies left by earlier versions.** They stay in `%TEMP%` as `_MEI…`
folders of about 1.25 GB each. With no Antigravity agent running, this lists
them and their sizes:

```powershell
Get-ChildItem $env:TEMP -Directory -Filter '_MEI*' |
  Select-Object Name, LastWriteTime, @{n='GB';e={[math]::Round((Get-ChildItem $_.FullName -Recurse -File | Measure-Object Length -Sum).Sum / 1GB, 2)}}
```

Other programs built with PyInstaller use the same `_MEI` names, so check the
list before deleting; Antigravity's hold a `google3` folder.

**Your own Antigravity launcher.** If `~/.acpx/config.json` points Antigravity
at a launcher of your own rather than CLI-MODE's, setup leaves it as it is, and
this fix doesn't reach it: see how `plugins/cli-mode/scripts/acp-login.py` gives
the server its own `TMP`/`TEMP`.

## Install

Pick your host and run its block in PowerShell.

**Codex** (runs in the Codex desktop app):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.3
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.3.zip` from this
release, extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub, then run `/cli-mode:cli shortcuts` once:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.3 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

**Using both?** Run both. They share CLI-MODE's ACPX copy and each agent's own
sign-in; conversations and settings stay separate per host.

## Upgrading from an earlier 0.3 release

A GitHub install is pinned to its tag, so updating it in place keeps the old version. Move
it to the new tag instead; your saved CLI-MODE settings are kept.

**Codex:**

```powershell
codex plugin marketplace remove cli-mode
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.3
codex plugin add cli-mode@cli-mode
```

Coming from v0.3.0, then open **Plugins → CLI-MODE → Hooks** in the Codex
desktop app and choose **Trust all** (or review the updated definitions); from
v0.3.1 or v0.3.2 the hooks are unchanged.

**Claude Code from the zip:** run the new zip's `install-claude.ps1`; it updates
in place and keeps your data.

**Claude Code from GitHub:** uninstall with `--keep-data` first, because removing
the marketplace otherwise deletes CLI-MODE's saved data:

```powershell
claude plugin uninstall cli-mode@cli-mode --keep-data
claude plugin marketplace remove cli-mode
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.3 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

The fix takes effect the next time an Antigravity agent starts.

## Validation and artifacts

The [v0.3.3 validation report](checks/v0.3.3-validation.md) records this
release's checks: the full offline suite, both install smokes, and live runs of
the launcher against Antigravity's real ACP server.

Both archives and their SHA256 checksums are attached to the GitHub Release:
`cli-mode-codex-0.3.3.zip` (Codex) and `cli-mode-claude-0.3.3.zip` (Claude Code).
