# CLI-MODE v0.3.4 — Agent working folders

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Claude Code** or **Codex**, over ACPX.

**Each agent has a folder for its files.** A coding task still changes your
project's files as asked. Anything else an agent creates (research notes,
reports, art, drafts) now goes in its own folder, `Agent_Working_Folder/<NAME>/`
in the project, such as `Agent_Working_Folder/ART/`. CLI-MODE tells the agent
this with each task, and the answer ends with what it saved there:

```text
Grok ART saved 3 files in Agent_Working_Folder/ART/: marble/face-1.svg new · marble/preview.html new · notes.md new
```

- The folder is kept out of git (it holds its own `.gitignore`), so drafts never
  reach your history; copy what you keep into the project.
- The saved-files line works in folders outside git too, stays fast for images
  and audio, and names the right agent even while several work at once.
- **`/cli dir [name]`** shows an agent's folder as a full path (to open or paste
  elsewhere) and as its path in the project, with its newest files. It takes a
  name, a tag (`gro`) or the short form (`-7K`).

Also in this release:

- **A compact help card.** `/cli help` shows one short line per command,
  grouped under Agents, Send work, Results and Settings: about half as long, and
  nothing wraps on a phone.
- **Claude Code: agents that finish at the same time no longer lose an
  answer.** Their answers now arrive together in one message.
- The docs say when setup installs its own ACPX: only when no ACPX 0.18.0 is
  found; a global `acpx@0.18.0` from npm is used instead.

## Install

Pick your host and run its block in PowerShell.

**Codex** (runs in the Codex desktop app):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.4
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.4.zip` from this
release, extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub, then run `/cli-mode:cli shortcuts` once:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.4 --sparse .claude-plugin plugins
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
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.4
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
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.4 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

## Validation and artifacts

The [v0.3.4 validation report](checks/v0.3.4-validation.md) records this
release's checks: the full offline suite, both install smokes, and live runs on
Claude Code with Grok Build and Codex CLI agents.

Both archives and their SHA256 checksums are attached to the GitHub Release:
`cli-mode-codex-0.3.4.zip` (Codex) and `cli-mode-claude-0.3.4.zip` (Claude Code).
