# CLI-MODE

## Install

CLI-MODE runs in **Codex** and in **Claude Code**. Pick your host and run its block in PowerShell.

**Codex** (with the Codex CLI installed):

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.0
codex plugin add cli-mode@cli-mode
```

**Claude Code** (2.1.147 or later): download `cli-mode-claude-0.3.0.zip` from the
[v0.3.0 release](https://github.com/adamczhang/CLI-MODE/releases/tag/v0.3.0), extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.0 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

The zip's installer also checks Python, Node and your Claude Code version, and adds `/cli` and `/d` to
autocomplete; see [Claude Code](#claude-code) for the details.

**Using both?** Run both. The two installs share CLI-MODE's ACPX copy and each agent's own sign-in, so
agents set up for one host are ready in the other. Conversations and settings stay separate per host.

Release **0.3.0** · [Release notes](RELEASE_NOTES.md) · [Changelog](CHANGELOG.md)

## The Problem

People often have multiple AI subscriptions: Codex, Grok, Claude, Antigravity, Cursor, GitHub Copilot, and more. Switching environments every time you want to use a different credit pool is annoying. Open-source multi-agent apps are useful, but you may prefer Codex's tools and workflow. What you really want is to use those other subscriptions inside Codex, without changing how you already work.

## Introducing CLI-MODE

CLI-MODE is a Codex plugin that lets you drive **other coding agents from inside Codex**, over the open [Agent Client Protocol (ACP)](https://agentclientprotocol.com/).

You stay in Codex. Your prompts go to the agent you picked, unchanged. That agent runs on **its own subscription and its own harness**, keeping its full capabilities, and Codex relays its plan, its progress and its answer back into the conversation you were already in.

No second app. No second chat window. No terminal to babysit. One place, and whichever credit pool you feel like spending today.

The same plugin also runs inside **Claude Code**, with the same agents, menus and commands. See [Claude Code](#claude-code) for what differs there.

## Supported CLIs

- **Antigravity** — `/cli agy`
- **Claude Code** — `/cli claude`
- **Grok Build** — `/cli grok`
- **Cursor** — `/cli cursor`
- **GitHub Copilot** — `/cli copilot`
- **Codex CLI** — `/cli codex`

Use the account and model access configured for your chosen CLI. One agent runs
per task, using that task's working folder. To switch agents, run `/cli stop`
first; conversations do not transfer between providers.

## Get started

1. Install the plugin using the commands above, then open a new Codex task with **Full Access**.
2. Run **`/cli`** and choose an agent. Setup checks its dependencies and guides installation and sign-in where needed.
3. If prompted, approve the hooks under **Codex Desktop → Plugins → CLI-MODE → Hooks → Review / Trust all**, then recheck setup.
4. Accept the defaults or choose your model, effort, access and routing mode.
5. Send your prompt. CLI-MODE announces `Passing to Claude...`, for example, then relays public updates under `Claude says...`.

Windows is the supported setup path. Shared dependencies include ACPX **0.18.0**, Node.js
22.13+ and Python 3.10+. Setup runs on the Windows PowerShell 5.1 built into Windows;
PowerShell 7 is not required. You only need the provider CLIs you intend
to use. Antigravity also requires its separate ACP runtime and sign-in.

Setup installs CLI-MODE's own copy of ACPX with `npm ci` from the lockfile in
`plugins/cli-mode/runtime/acpx`, under `%LOCALAPPDATA%\CLI-MODE\acpx\0.18.0`.
CLI-MODE uses that copy first. An existing global `acpx@0.18.0` from npm still
works as a fallback, and a global upgrade to another version never changes
which ACPX a binding uses.

## Passthrough or Direct

**Passthrough** forwards ordinary prompts to the active CLI when selected.

**Direct** is the default and keeps ordinary prompts with Codex. Prefix a request with `/d` or `$d`
to send it to the active CLI:

```text
/cli mode direct
/d Explain how authentication works in this project.
```

Changing routing mode keeps the agent's session and other settings intact.
`/d` does not activate an agent on its own.

The routing hook captures delegated text exactly, queues it in arrival order,
and starts a detached worker for the active conversation. The worker forwards
one message at a time to the saved ACPX session even if the Codex response is
interrupted by a new user message. Codex reads each receipt and public events
through `observe --request <id>`; it never rewrites or resubmits the prompt.
Captured input is removed after submission or shutdown. `/cli cancel` stops the
active turn; `/cli stop` discards queued follow-ups and closes the session. The
host relays public output into chat while its response is active; the worker
cannot independently post new chat messages after that response ends.

Both routing modes and file-based submissions use one controller request lifecycle.
A small Node bridge calls ACPX's public shared runtime; ACPX keeps the agent
connection alive between controller invocations. Each binding saves its tested
ACPX installation, so a PATH change cannot silently switch its runtime. Follow-up
turns require the original provider conversation to resume successfully.

## Settings and commands

- **`/cli`** — choose an agent or run setup.
- **`/cli bind <agent>`** — activate with saved defaults, after readiness checks.
- **`/cli menu`**, **`/cli mode`** or **`/cli model`** — open the same active Agent Settings page.
- **`/cli model <choice>`**, **`/cli effort <choice>`**, **`/cli access <choice>`** — change a setting. CLI-MODE matches your wording (for example `opus`, `extra high` or `bypass permissions`) against the agent's advertised options and applies a unique match; if the choice is unclear it shows the menu.
- **`/cli mode direct`** or **`/cli mode passthrough`** — change routing directly.
- **`/cli progress activity`** or **`/cli progress quiet`** — show tool activity and usage (default), or keep messages and plans. Changes apply to the next turn.
- **`/cli view on`** or **`/cli view off`** — watch each agent turn live in its own PowerShell window (PowerShell 7 when installed): the agent's text, tool activity, plans and the result, in colour. Read-only, off by default, saved for every conversation. Closing the window is safe; the next turn reopens it until you turn it off.
- **`/cli queue`** — inspect queued, running and completed request IDs; **`/cli resume`** reattaches status monitoring to existing turns and restarts waiting work when safe, without resending a prompt.
- **`/cli cancel`** — cancel the active turn while preserving queued follow-ups.
- **`/cli stop`** — stop the agent and return to ordinary Codex work.
- **`/help`** — show the command card, framed like every other menu. Reply X to close help.

`$` works in place of `/`, and controls are case-insensitive. Help replies
stay local, even in Passthrough. `X` after help closes only help; any
active agent or pending menu remains. Closing active
settings also keeps the agent running. When no agent is active, the three settings
menu commands reply: `CLI-MODE: Agent not activated. /CLI to setup`.

Available settings depend on the provider. Claude supports Allow, Auto-edit and
Prompt; Antigravity, Grok and Copilot support Allow and Prompt; Cursor and Codex
support Allow only. CLI-MODE cannot show you an agent's approval prompt, so every
level except Allow is labeled "approval requests stop the turn": when the agent
asks for a permission its access level does not grant, that turn ends with a
message saying so, and queued follow-ups continue. Choose Allow with
`/cli access allow` for work that edits files or runs commands. Every agent shows
access the same way: the shared level first, the agent's own name for it in
brackets, for example `Allow (Bypass permissions)` or `Allow (YOLO)`. Effort levels
are listed from lowest to highest under one spelling. Model and effort menus show the choices supported by the
integration. Refresh reads the owned session's advertised metadata and tells you
when cached choices must be retained.

## What to expect

- **Persistent context:** follow-up prompts use the same conversation. Settings changes reuse that session.
- **Public updates:** while the agent works, its words arrive as regular chat updates in readable batches (about 800 characters, or after a short pause), each with a one-line summary of its work. When the turn ends, one view shows the agent's final message and a collapsible **work** section: the plan with a progress bar and tool activity grouped by kind. Private reasoning and raw tool inputs/outputs stay filtered.
- **Theme:** menus and views use Codex's own theme colours and icons, so they follow light and dark mode.
- **Consistent presentation:** activation, passing announcements and agent attribution use green accents; answer text uses the normal chat colour.
- **Provider commands:** supported slash commands are handled by the selected CLI as commands, not as text. For every agent, CLI-MODE refuses commands that would sign you out or change the model, effort or access behind its back (`/logout`, `/model`, `/permissions`, `/allow-all` and similar) and points to the `/cli` control instead; unknown commands are refused before anything is sent. Antigravity's native-command handoff starts a fresh conversation and reports that context change.
- **Usage when available:** Antigravity and Claude can report subscription utilization. Unavailable usage reads “Usage not available through CLI” for every provider.
- **Text inputs:** include accessible local file paths in your prompt. Chat attachments are not forwarded automatically.

Local state and public response logs live under
`$CODEX_HOME/plugin-data/cli-mode`. Public logs and ACPX history can contain task
content. Private reasoning is excluded from CLI-MODE's relay logs.

ACPX 0.18.0's shared runtime does not support injecting `mcpServers` or interactive
permission callbacks. Configure MCP tools in the provider CLI itself; a nonempty
ACPX `mcpServers` configuration is rejected before submitting a prompt. This
integration uses the selected static access policy and fails requests needing
unavailable interactive approval.

After a turn, the conversation's worker stays idle for up to five minutes so the
next message starts at once, then exits. `/cli stop` ends it immediately.

Stopping verifies closure of owned sessions and settlement of local submitters.
It does not certify that every provider-managed descendant or background task exited.
On Windows, each submitter process (the ACPX bridge, ACPX CLI or native CLI)
is tied to the worker that started it, so it cannot outlive that worker. The
ACPX session owner is deliberately left out of that tie: a worker that dies
detaches from its turn rather than canceling it, and the request is then shown
as uncertain for you to inspect.

## Claude Code

One source tree ships two installers: the Codex plugin above and a Claude Code plugin. The agents,
setup, menus, settings, routing modes and commands are the same; this section covers what differs.

### Install

Requirements: Windows, **Claude Code 2.1.147 or later** (tested on 2.1.278 and 2.1.280), Python 3.10+
and Node.js 22.13+, the same shared dependencies as above. The commands are in [Install](#install).

- **From the zip,** `install-claude.ps1` checks those requirements and copies the plugin to
  `%LOCALAPPDATA%\CLI-MODE\claude-marketplace`. It adds or updates the `cli-mode` marketplace and
  installs `cli-mode@cli-mode`, or reinstalls it keeping your saved CLI-MODE data (`--keep-data`). It
  never removes a marketplace, since that would uninstall the plugin and its data. It also adds `/cli`
  and `/d` to autocomplete, as described below.
- **From GitHub,** you get the same plugin. Claude Code offers its commands as `/cli-mode:cli` and
  `/cli-mode:d`, and typing `/cli …` or `/d …` in full works too. Run **`/cli-mode:cli shortcuts`** once
  to add `/cli` and `/d` to autocomplete, so both routes end up the same.

### Get started

1. Start a new Claude Code session in your project (or run `/reload-plugins`), and accept the folder's
   **workspace trust** prompt. Claude Code runs no plugin hooks before that.
2. Run **`/cli`** (or `/cli-mode:cli`) and choose an agent, as in [Get started](#get-started). There is
   no Full Access step on Claude Code.
3. Send work with **`/d <task>`** (or `/cli-mode:d`), or switch to Passthrough. The agent's words
   arrive under **`Claude says...`**, **`Codex says...`** and so on, in green.

Help is **`/cli help`**, because Claude Code's own `/help` is built in. Everything else in
[Settings and commands](#settings-and-commands) works as written.

### What differs

- **Replies are chat messages.** Menus and confirmations are posted as normal chat, each costing one
  small Claude turn. **`/cli display instant`** shows them at once with no model turn, as a hook notice,
  which the desktop app frames as "blocked by hook". **`/cli display chat`** switches back. The choice
  applies to every session.
- **The answer arrives whole.** Claude posts "Passing to …" first. While the agent works, Claude checks on it
  about every 25 seconds (one short Claude turn each) and posts nothing in between, because the desktop app
  folds text between tool calls out of view. When the agent finishes, its whole output arrives as the turn's
  last message, under "… says…", with a one-line work summary. If you pick the **Claude Code agent**
  (`/cli claude`) inside Claude Code, the agent and the relaying both draw on the same Claude plan.
- **`/cli` and `/d` autocomplete as typed.** Claude Code prefixes a plugin's commands with its name, so the
  zip's installer also adds `/cli` and `/d` as personal commands (`cli.md` and `d.md` in
  `~/.claude/commands`, marked as CLI-MODE's). After a GitHub install, `/cli-mode:cli shortcuts` does the
  same. A file of your own with either name is left alone. `/cli-mode:cli` and
  `/cli-mode:d` work too.
- **Help is a card.** Any message after it closes it and is then handled as usual; X just closes it.
- **Interrupting is safe.** If you stop Claude mid-relay (Esc), the agent keeps working. Your next `/d`,
  or **`/cli resume`**, shows what you missed first, oldest first, without resending anything.
- **Attachments stay with Claude Code.** Only the prompt's text reaches the agent, and Claude says so
  when a message had images or files.
- **Green titles and names.** "Passing to …", "… says…" and the activation card's title are forest green
  and bold, just larger than the text around them, readable in light and dark mode. Claude Code has no
  themed views, so this uses the LaTeX its chat renders on the desktop and in the mobile app. Menus keep
  their ASCII box, and their two title rows show green through the chat's diff highlighting, in the same
  monospace letters (a code block can't be bold). The terminal shows the LaTeX as raw text:
  **`/cli color off`** switches to plain bold (and `on` back). Menus stay 40 characters wide, the width of
  a phone's code block.
- **State** lives in Claude Code's plugin data folder: `~/.claude/plugins/data/cli-mode-cli-mode`, or
  `cli-mode-inline` for sessions in the desktop app.
  **`/cli reset`** sets aside this session's saved state if it ever becomes unreadable (which would
  otherwise hold every prompt), then `/cli` starts fresh.
- **Every session runs the hook,** but it answers in about 35–50 ms and writes nothing in sessions that
  don't use CLI-MODE.

### Troubleshooting on Claude Code

- **`/cli` does nothing, or Claude says the hook did not run:** check that the plugin is enabled
  (`/plugin`), accept the folder's workspace trust prompt, check `/hooks` (and that `disableAllHooks` and
  `--bare` are off), then run `/reload-plugins` or start a new session.
- **"This session loaded an older plugin":** run `/reload-plugins` or start a new session, then `/cli`.

## Troubleshooting

- **Hooks need attention:** review/trust the plugin hooks, then recheck setup. Start a new task if an approved update has not loaded.
- **Full Access needs attention:** select Full Access for the current task and rerun `/cli`.
- **Activation fails:** check the selected CLI's sign-in, model access and quota. Antigravity's CLI and ACP runtime have separate setup requirements.
- **Switching agents is blocked:** run `/cli stop`, then select the other agent.
- **Commands behave oddly after an upgrade:** from a source checkout, run `python plugins/cli-mode/scripts/doctor.py` to inspect installation conflicts, prerequisites and which ACPX package CLI-MODE would use.
- **A turn stopped on a permission request:** the agent needed an approval that the current access level cannot give. Use `/cli access allow`, or ask for work that needs no approval.

## Development

```powershell
python -m unittest discover -s checks -p 'test_*.py'
python scripts/package_plugin.py
python checks/package_smoke.py
```

The controller, adapters and menus live in `plugins/cli-mode/scripts`; provider
guides and catalogs live in `plugins/cli-mode/backends`. `controller.py` is the
CLI entry point; its `Controller` combines `menus.py` (setup and settings),
`binding.py` (owned-session lifecycle), `dispatch.py` (one provider turn) and
`queue_worker.py` (the detached FIFO worker, receipts and the `relay` command,
rendered by `relay_view.py`). Start with the
[backend contract](plugins/cli-mode/codex/skills/cli-mode/references/backend-contract.md)
when adding a CLI.

See the [v0.3.0 validation report](checks/v0.3.0-validation.md) for this
release and the [ACPX hardening report](checks/acpx-hardening.md) for its
implementation evidence. The suite exercises the real pinned ACPX runtime with an
offline fixture agent when ACPX is installed; those tests report a skip if it is
unavailable. Live provider tests consume quota; the [validation plan](checks/five-cli-validation-plan.md)
describes them. A CI workflow template is included in `scripts/github-actions-validate.yml`;
it is not enabled automatically.

Bug reports should include your OS, selected CLI, versions and the error you saw.
Requests for additional CLIs and focused pull requests are welcome.

## Author and license

Built by **Adam Zhang** · [@dad__vibes](https://x.com/dad__vibes)

Released under the [MIT License](LICENSE). Copyright © 2026 Adam Zhang.

CLI-MODE is independent and is not affiliated with or endorsed by OpenAI,
Google, Anthropic, xAI, Cursor or GitHub.
