# CLI-MODE v0.3.2 — Several named agents

CLI-MODE drives six coding agents (Antigravity, Claude Code, Grok Build, Cursor,
GitHub Copilot and Codex CLI) from inside **Claude Code** or **Codex**, over ACPX.

**Run several agents at once, each with a name.** `/cli spawn gro` starts Grok
Build as `GRO-4K`, for example (or `/cli spawn gro ELON` with a name of your
own), even while other agents work. Up to four run side by side, each with its
own queue. `/d gro-4k <prompt>` sends to one, `/d gro-4k,cod-7k <prompt>` to
several at once, and a plain `/d` goes to the current agent. Each answer arrives
under its agent's name: `Grok GRO-4K says...`.

Also in this release, on both hosts:

- **Change receipts.** In a git repository, each answer ends with what changed
  in the folder during the turn: files, lines added in green and removed in red.
  `/cli diff` shows the full diff. Your staging area is never touched.
- **Agents from earlier sessions.** An agent you didn't close keeps its
  conversation: `/cli attach` in a new session in the same folder brings it back.
- **Agent timeout.** An idle agent now stops after 1 hour (was 30 minutes) and
  restarts on its next `/d`, in the same conversation. `/cli timeout` changes it.
- **New commands:** `/cli list`, `/cli use <name>`, `/cli close <name|all>`
  (asks which when several run), `/cli diff`, `/cli timeout`, `/cli attach`.
  Agents answer to three-letter tags everywhere: `agy`, `cla`, `cod`, `gro`,
  `cop`, `cur`.
- **Passthrough mode is removed.** Only `/d` reaches an agent; a conversation
  saved in Passthrough opens in Direct mode.
- **Changed:** `/cli bind` (now also `/cli spawn`) and the activation page start
  a new agent instead of reconfiguring the running one; change a running agent's
  settings with `/cli menu` or `/cli model|effort|access`.

On **Claude Code**, an agent's turn runs as a row in Claude Code's background
tasks: Claude posts "Passing to …", ends its turn, and posts the whole answer
when the agent finishes, with no turns spent checking in between. Starting an
agent runs inside CLI-MODE's hook, so it never adds a row of its own.

## Install

Pick your host and run its block in PowerShell.

**Codex** (runs in the Codex desktop app):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.2
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.2.zip` from this
release, extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub, then run `/cli-mode:cli shortcuts` once:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.2 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

**Using both?** Run both. They share CLI-MODE's ACPX copy and each agent's own
sign-in; conversations and settings stay separate per host.

## Upgrading from v0.3.1 or v0.3.0

A GitHub install is pinned to its tag, so updating it in place keeps the old version. Move
it to the new tag instead; your saved CLI-MODE settings are kept. Agents saved by an
earlier version get a name the first time this one reads them.

**Codex:**

```powershell
codex plugin marketplace remove cli-mode
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.2
codex plugin add cli-mode@cli-mode
```

Coming from v0.3.0, then open **Plugins → CLI-MODE → Hooks** in the Codex
desktop app and choose **Trust all** (or review the updated definitions); from
v0.3.1 the hooks are unchanged.

**Claude Code from the zip:** run the new zip's `install-claude.ps1`; it updates
in place and keeps your data.

**Claude Code from GitHub:** uninstall with `--keep-data` first, because removing
the marketplace otherwise deletes CLI-MODE's saved data:

```powershell
claude plugin uninstall cli-mode@cli-mode --keep-data
claude plugin marketplace remove cli-mode
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.2 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

## Validation and artifacts

The [v0.3.2 validation report](checks/v0.3.2-validation.md) records this
release's checks: the full offline suite, both install smokes, and live runs on
both hosts with Grok Build and Codex CLI agents.

Both archives and their SHA256 checksums are attached to the GitHub Release:
`cli-mode-codex-0.3.2.zip` (Codex) and `cli-mode-claude-0.3.2.zip` (Claude Code).
