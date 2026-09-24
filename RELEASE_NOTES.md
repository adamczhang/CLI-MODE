# CLI-MODE v0.3.0 — A fresh start

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Codex** or **Claude Code**, over ACPX.
This release restarts the repository's history: one snapshot of the Codex plugin
and its Claude Code port, with the earlier releases summarised in the
[changelog](CHANGELOG.md).

New since v0.2.1:

- **Agent viewer.** `/cli view on` opens a read-only PowerShell window that shows
  each agent turn live: its text, tool activity, plans and the result, in colour.
  Off by default; closing the window is safe.
- **Safer setup.** Installing an agent, or activating it with wider access than
  Prompt, asks first. Stopping setup cancels a running installer.
- **Clearer states.** A busy or failed control is reported as itself, a request
  that was not sent says why, and nothing is relayed or cancelled twice after a
  compaction.
- **Claude agent.** The Opus 5.5 default uses its canonical model ID, older model
  caches migrate, Haiku works without an effort selector, and usage has readable labels.
- **Codex views.** Final answers render headings, nested lists, tables and code;
  activation uses responsive rows; work groups show short workspace-relative paths.
- **Reliability.** The hook reads its input as UTF-8 on every Windows code page, and
  the activation usage lookup no longer holds up the turn.

## Install

Pick your host and run its block in PowerShell.

**Codex** (with the Codex CLI installed):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.0
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.0.zip` from this
release, extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub, then run `/cli-mode:cli shortcuts` once:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.0 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

The zip's installer also checks Python, Node and your Claude Code version, and adds
`/cli` and `/d` to autocomplete.

**Using both?** Run both. They share CLI-MODE's ACPX copy and each agent's own
sign-in; conversations and settings stay separate per host. See the
[README](README.md) for provider prerequisites and sign-in.

## Validation and artifacts

The [v0.3.0 validation report](checks/v0.3.0-validation.md) records the tests and
the install checks on both hosts. Cursor's account is plan-limited, so Cursor is
verified offline.

Both archives and their SHA256 checksums are attached to the GitHub Release:
`cli-mode-codex-0.3.0.zip` (Codex) and `cli-mode-claude-0.3.0.zip` (Claude Code).
