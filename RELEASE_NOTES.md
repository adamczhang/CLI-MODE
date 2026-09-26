# CLI-MODE v0.3.5 — Passing work between agents

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Claude Code** or **Codex**, over ACPX.

**A copy box under every answer.** Each agent's full answer is also saved in its
working folder, and the answer ends with a small box listing that file and the
files the turn created, changed or mentioned. Copy it into another agent's `/d`
and that agent reads the exact answer and files itself:

```text
Codex RESEARCH answer: Agent_Working_Folder/RESEARCH/answers/003-compare-3d-engines.md
Files: docs/engine-report.md
```

**Safety nets for several agents in one project:**

- **`/cli undo [name]`** puts back the files an agent's last turn changed, only
  if none of them changed since.
- **Same-file warning:** when two agents working at once edit the same file,
  the later answer says so.
- **Tests after every coding turn, on by default:** CLI-MODE finds your
  project's test command (`npm test`, `python -m pytest`, `cargo test`,
  `go test ./...`) and each answer that changed files says whether they passed.
  `/cli test <command>` sets another, `/cli test off` turns them off.
- **A shared brief:** `/cli brief-add <text>` adds a point every agent reads
  before its task; `/cli brief` shows it, `/cli brief clear` removes it.

Also: `/cli dir` counts saved answers apart from an agent's own files, and an
unknown word after `/cli` is named instead of a suggestion to activate.

## Install

Pick your host and run its block in PowerShell.

**Codex** (runs in the Codex desktop app):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.5
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.5.zip` from this
release, extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub, then run `/cli-mode:cli shortcuts` once:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.5 --sparse .claude-plugin plugins
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
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.5
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
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.5 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

## Validation and artifacts

The [v0.3.5 validation report](checks/v0.3.5-validation.md) records this
release's checks: the full offline suite, both install smokes, and a live run on
Claude Code of the copy box with Grok Build and Codex CLI agents.

Both archives and their SHA256 checksums are attached to the GitHub Release:
`cli-mode-codex-0.3.5.zip` (Codex) and `cli-mode-claude-0.3.5.zip` (Claude Code).
