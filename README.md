# CLI-MODE

![CLI-MODE: unlock third-party subscriptions in Claude Code and Codex](docs/images/banner.jpg)

**Use your other AI coding subscriptions without leaving Claude Code or Codex.**
CLI-MODE is a plugin that hands your prompts to Antigravity, Claude Code, Grok Build, Cursor, GitHub
Copilot or Codex CLI, and relays their answers back into the chat you were already in.

**Windows only for now** · Built for Claude Code, also runs in Codex · Six agents over the
[Agent Client Protocol](https://agentclientprotocol.com/) · MIT

## Hosts

- **Claude Code has first-class support.** It is the primary host: new features are designed for it and
  arrive there first, and every change is checked against it.
- **Codex is supported and tested,** but new features may reach it later, or work differently there.

| Feature | Claude Code | Codex |
|---|:---:|:---:|
| Six agents, each on its own subscription, with guided setup and sign-in checks | ✓ | ✓ |
| Model, effort and access chosen per agent | ✓ | ✓ |
| Several named agents working side by side; one prompt to many at once | ✓ | ✓ |
| Conversations that persist, and carry over to a new session | ✓ | ✓ |
| Change receipt, full diff and undo for every turn | ✓ | ✓ |
| Your tests run after every turn that changes files | ✓ | ✓ |
| Warning when two agents edit the same file | ✓ | ✓ |
| Hand-offs between agents: saved answers, a copy box, a shared brief | ✓ | ✓ |
| A working folder per agent for notes and drafts, kept out of git | ✓ | ✓ |
| Agents run as background tasks, like Claude's own subagents | ✓ | — |
| Live progress | Task row, viewer window | In chat, viewer window |

Claude Code needs the folder's workspace trust; Codex needs a Full Access task.

On Claude Code the whole answer arrives when the agent finishes, with a one-line work summary; see
[Claude Code](#claude-code) for details.

<!-- Screenshot: a Claude Code conversation showing "Passing to Grok GRO-4K...", the agent's row in
     background tasks, then "Grok GRO-4K says..." with its work summary. 1280 px wide, light or dark theme. -->

## Why

People often pay for several AI subscriptions: Codex, Grok, Claude, Antigravity, Cursor, GitHub Copilot.
Switching apps every time you want a different credit pool is tedious, and multi-agent apps mean giving up
the tools and workflow you already like.

CLI-MODE keeps you where you are. Your prompt goes, unchanged, to the agent you picked. That agent runs on
**its own subscription and its own harness**, with its full capabilities, and its plan, progress and answer
come back into your conversation. No second app, no second chat window, no terminal to watch.

## What you need

- **Windows 10 or 11.** Setup and the agent runtime are Windows-only for now.
- **A host:** **Claude Code 2.1.147 or later** (recommended), or the Codex desktop app (the install uses the
  Codex CLI).
- **Python 3.10+ and Node.js 22.13+.** Setup installs the rest, including a pinned copy of ACPX for CLI-MODE
  (unless a global `acpx@0.18.0` from npm is already installed, which CLI-MODE then uses).
- **At least one supported agent CLI,** installed and signed in with its own subscription. You only need the
  ones you plan to use; `/cli` checks each one and guides installation and sign-in.

## Supported agents

| Agent | Command | Install guide |
|---|---|---|
| Antigravity | `/cli agy` | [Antigravity CLI](https://antigravity.google/docs/cli/install/) (also needs its ACP runtime) |
| Claude Code | `/cli cla` | [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) |
| Grok Build | `/cli gro` | [Grok Build](https://docs.x.ai/build/overview) |
| Cursor | `/cli cur` | [Cursor CLI](https://cursor.com/docs/cli/overview) |
| GitHub Copilot | `/cli cop` | [Copilot CLI](https://docs.github.com/copilot/how-tos/copilot-chat/use-copilot-chat-in-the-command-line) |
| Codex CLI | `/cli cod` | [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) |

Every command that takes an agent accepts its three-letter tag or its full name (`cla` or `claude`, `gro`
or `grok`), and the tag also starts its generated names (`GRO-4K`). Each agent runs in the conversation's
working folder, using its own account and model access. Up to four run at once, in any mix (see
[Several agents](#several-agents)); conversations do not transfer between providers.

## Install

Pick your host and run its block in PowerShell.

**Claude Code:** download `cli-mode-claude-0.3.5.zip` from the
[v0.3.5 release](https://github.com/adamczhang/CLI-MODE/releases/tag/v0.3.5), extract it, and run:

```powershell
.\install-claude.ps1
```

Or install it straight from GitHub:

```powershell
claude plugin marketplace add adamczhang/CLI-MODE@v0.3.5 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

The zip's installer also checks Python, Node and your Claude Code version, and adds `/cli` and `/d` to
autocomplete; see [Claude Code](#claude-code).

**Codex:**

```powershell
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.3.5
codex plugin add cli-mode@cli-mode
```

**Using both?** Install both. They share one ACPX installation and each agent's sign-in, so an agent set up
for one host is ready in the other. Conversations and settings stay separate per host.

**Upgrading?** A GitHub install stays on its tag; the [release notes](RELEASE_NOTES.md#upgrading-from-an-earlier-03-release)
show how to move it to 0.3.5 and keep your settings.

Release **0.3.5** · [Release notes](RELEASE_NOTES.md) · [Changelog](CHANGELOG.md)

## Get started

1. Start a new Claude Code session and accept the folder's workspace trust prompt (on Codex, open a new task
   with **Full Access**).
2. Run **`/cli`** and choose an agent. Setup checks its dependencies and guides installation and sign-in.
3. On Codex, if prompted, approve the hooks under **Plugins → CLI-MODE → Hooks → Review / Trust all**, then
   recheck setup.
4. Accept the defaults or choose the model, effort and access.
5. Send your prompt. CLI-MODE announces `Passing to Claude CLA-4F...`, for example, and relays the answer
   under `Claude CLA-4F says...`. `CLA-4F` is the agent's name.

## Sending a prompt to an agent

Only a message that starts with `/d` (or `$d`) goes to an agent, the current one unless you name another;
everything else stays with your host, so you decide exactly what each agent is asked:

```text
/d Explain how authentication works in this project.
```

`/d` does not activate an agent on its own. Messages sent while an agent is busy queue up for it and go out
in order; see [Architecture](docs/ARCHITECTURE.md) for how the queue works.

## Several agents

Every agent has a name, shown in capitals: one you give it (`/cli spawn gro ELON`, 1-10 letters and digits)
or a generated one such as `GRO-4K`. Start another agent at any time, even while others work; the newest
becomes the current agent, the one a plain `/d` goes to. Put a name first to send to another:

```text
/d gro-4k Review the parser changes.
/d elon Write tests for the parser.
```

Names match in any case, and a generated name also as `gro4k`, or as `-4K` when only one running agent has
that ending. A tag (`gro`, `cod`) names an agent too when only one of its kind is running. Each agent has its
own queue, so they work side by side, and each answer is relayed under its own name (`Grok GRO-4K says...`).
`/cli list` shows them all, `/cli use <name>` changes the current agent, and `/cli close <name>` closes one
while the others keep working.

To ask several agents the same thing, name them all, with commas between:

```text
/d gro-4k,cod-7k Review the parser changes.
/d elon, -7K Is this migration safe?
```

Each gets its own copy and works at the same time; the answers arrive one by one, each under its own name.
If any name matches no running agent, nothing is sent.

**Where agents save files.** A coding task changes your project's files as asked. Anything else an agent
creates (research notes, reports, art, drafts) goes in its own folder, `Agent_Working_Folder/<NAME>/` in the
project, such as `Agent_Working_Folder/ART/`. CLI-MODE tells the agent this with each task, and the answer
ends with what it saved there:

```text
Grok ART saved 3 files in Agent_Working_Folder/ART/: marble/face-1.svg new · marble/preview.html new · notes.md new
```

The folder is kept out of git (it holds its own `.gitignore`), so drafts never reach your history; copy what
you keep into the project. It is reported in folders outside git too. `/cli dir [name]` shows an agent's
folder as a full path (to open or paste elsewhere) and as its path in the project, with its newest files.

**Passing work between agents.** Every answer ends with a small box listing where the full answer is saved
(`Agent_Working_Folder/<NAME>/answers/`) and the files the turn created, changed or mentioned. Copy it into
another agent's `/d`, and that agent reads the exact answer and files itself:

```text
Codex RESEARCH answer: Agent_Working_Folder/RESEARCH/answers/003-compare-3d-engines.md
Files: docs/engine-report.md
```

**Safety nets.** `/cli undo [name]` puts back the files an agent's last turn changed (only if none changed
since). When two agents working at once edit the same file, the later answer warns you. After every turn that
changes files, CLI-MODE runs your project's tests (found automatically: `npm test`, `python -m pytest`,
`cargo test` or `go test ./...`) and the answer says whether they passed; `/cli test <command>` sets another,
`/cli test off` turns them off. `/cli brief-add <text>` builds a short brief, `Agent_Working_Folder/BRIEF.md`, that every agent reads
before its task; `/cli brief` shows it and `/cli brief clear` removes it.

**Agents from earlier sessions.** An agent you didn't close keeps its conversation. In a new session in the
same folder, `/cli attach` lists them and `/cli attach <name or number>` brings one here; its next `/d`
continues where it left off, and the earlier session no longer has it.

## Settings and commands

- **`/cli`** — choose an agent or run setup.
- **`/cli spawn <agent> [name]`** (or **`/cli bind`**) — start an agent with saved defaults, after readiness
  checks. `<agent>` is its tag or full name, for example `cod` or `codex`.
- **`/cli list`** (or **`/cli agents`**) — the running agents by name; **`/cli agents max <n>`** sets how many
  can run at once (4 by default, up to 8).
- **`/cli use <name>`** — make that agent the current one.
- **`/cli diff [name]`** — what an agent's last turn changed, as a diff.
- **`/cli timeout [name] <time>`** — how long an idle agent keeps running, from 5 minutes to 24 hours
  (`90`, `90m`, `2h`; 1 hour by default). Without a name it sets the default for every conversation and this
  one's agents. `/cli timeout` shows the values.
- **`/cli attach [name]`** — bring an open agent from an earlier session in this folder here.
- **`/cli menu [name]`** (or **`/cli settings`**) — open an agent's settings page; the current agent's by
  default.
- **`/cli model <choice>`**, **`/cli effort <choice>`**, **`/cli access <choice>`** — change a setting, with an
  agent's name first for another agent (`/cli model elon opus`). CLI-MODE matches your wording (for example
  `opus`, `extra high` or `bypass permissions`) against the agent's options and applies a unique match; if the
  choice is unclear it shows the menu.
- **`/cli progress activity`** or **`/cli progress quiet`** — show tool activity and usage (default), or only
  messages and plans. Applies from the next turn.
- **`/cli view on`** or **`/cli view off`** — watch each agent turn live in its own PowerShell window: the
  agent's text, tool activity, plans and the result, in colour. Read-only, off by default, saved for every
  conversation. Closing the window is safe; the next turn reopens it until you turn it off.
- **`/cli queue`** — see queued, running and completed requests; **`/cli resume`** picks up monitoring of
  existing turns without resending anything.
- **`/cli cancel [name]`** — cancel an agent's running turn, keeping queued follow-ups.
- **`/cli close [name|all]`** (or **`/cli stop`**, **`/cli off`**) — close one agent, or all of them. With
  several running and no name, it asks which. Closing the last agent returns you to your host.
- **`/cli help`** (or **`/cli commands`**; on Codex also **`/help`**) — show the command card. Reply X to
  close it.

`$` works in place of `/`, and controls are case-insensitive. Help and controls always stay local.
Closing help or settings keeps the agent running.

**Access levels.** Claude supports Allow, Auto-edit and Prompt; Antigravity, Grok and Copilot support Allow
and Prompt; Cursor and Codex support Allow only. CLI-MODE cannot show you an agent's approval prompt, so
every level except Allow is labeled "approval requests stop the turn": when the agent asks for a permission
its level does not grant, that turn ends with a message saying so, and queued follow-ups continue. Choose
Allow (`/cli access allow`) for work that edits files or runs commands. Access is shown as the shared level
followed by the agent's own name for it, for example `Allow (Bypass permissions)` or `Allow (YOLO)`.

## What to expect

- **Persistent context:** each agent keeps its own conversation; follow-up prompts continue it, and settings
  changes keep it. An agent idle for its timeout (1 hour by default) stops its process; the next `/d` starts it
  again in the same conversation, with a slower first reply.
- **Change receipts:** in a git repository, each answer ends with what changed in the folder during the turn:
  files, lines added in green and removed in red, for example `Codex COD-7K changed 2 files +42 -7`.
  `/cli diff` shows the full diff. Agents that share a folder at the same time share its changes too. Your
  staging area is never touched.
- **How answers arrive:** on Claude Code, the agent works as a row in Claude Code's background tasks and its
  whole answer arrives when it finishes, with a one-line work summary (see [Claude Code](#claude-code)). On
  Codex, the agent's words arrive as chat updates in readable batches, and when the turn ends, one view shows
  the final message and a collapsible **work** section with the plan and tool activity.
- **Only public output:** private reasoning and raw tool inputs and outputs are never relayed.
- **Theme:** menus and views follow your host's light or dark theme; CLI-MODE's own lines are green.
- **Provider commands:** an agent's slash commands run as commands. CLI-MODE refuses ones that would sign you
  out or change the model, effort or access behind its back (`/logout`, `/model`, `/permissions`,
  `/allow-all` and similar) and points to the `/cli` control instead. Unknown commands are refused before
  anything is sent. Antigravity's native commands hand off to its own CLI in a fresh conversation, and
  CLI-MODE says so.
- **Usage when available:** Antigravity and Claude can report subscription use; other agents show "Usage not
  available through CLI".
- **Text only:** include local file paths in your prompt. Chat attachments are not forwarded.
- **MCP tools** are configured in each agent's own CLI, not through CLI-MODE.

## Claude Code

Claude Code is CLI-MODE's primary host. The agents, setup, menus, settings and commands are
the same on Codex; what is specific to Claude Code:

- **Commands.** After a zip install, `/cli` and `/d` autocomplete as typed. After a GitHub install, Claude
  Code offers them as `/cli-mode:cli` and `/cli-mode:d`; run **`/cli-mode:cli shortcuts`** once to add `/cli`
  and `/d` too. A command file of your own with either name is left alone. Help is **`/cli help`**, because
  Claude Code's own `/help` is built in.
- **Replies are chat messages.** Menus and confirmations are posted as normal chat, each costing one small
  Claude turn. **`/cli display instant`** shows them at once with no model turn, as a hook notice (the desktop
  app frames it as "blocked by hook"); **`/cli display chat`** switches back.
- **The answer arrives whole.** Claude posts "Passing to …" and ends its turn. The agent's work shows as a
  row in Claude Code's background tasks, named after the agent and the prompt, with one line per step. When
  the agent finishes, Claude posts its whole output as one message, with a one-line work summary. Very long
  answers come in parts. With background tasks turned off (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`), Claude
  checks on the agent about every 25 seconds instead.
- **Only agents in background tasks.** Activating an agent (10–45 seconds while it starts) runs inside
  CLI-MODE's hook, so your prompt shows a "CLI-MODE" status line meanwhile and the confirmation card is the
  reply; it never adds a background task of its own. Raising an agent's access is the exception: Claude
  Code's permission prompt asks you first.
- **Interrupting is safe.** If you stop Claude mid-relay (Esc), or stop an agent's row in background tasks,
  the agent keeps working; only the watching stops. Your next `/d`, or **`/cli resume`**, shows what you
  missed, oldest first, without resending anything. **`/cli cancel`** stops the agent's turn itself.
- **Attachments stay with Claude Code.** Only the prompt's text reaches the agent, and Claude says so when a
  message had images or files.
- **Green titles.** "Passing to …", "… says…" and the activation card's title are green on the desktop and
  in the mobile app. The terminal shows the colouring as raw LaTeX: **`/cli color off`** switches to plain
  bold.
- **`/cli reset`** sets aside this session's saved state if it ever becomes unreadable, then `/cli` starts
  fresh.
- **Every session runs the hook,** but it answers in about 35–50 ms and does nothing in sessions that don't
  use CLI-MODE.

## FAQ

**Whose credits does it use?** The agent you pick runs on its own account and subscription, exactly as if
you ran its CLI yourself. Your host also does some work: on Codex, relaying happens inside Codex's own turn;
on Claude Code, see the next question.

**Does relaying cost Claude turns?** On Claude Code, yes, a few small ones: each menu reply is one short turn
(none with `/cli display instant`), and each agent turn takes two: one to pass it on, one to post the answer.
Nothing runs in between, however long the agent works (with background tasks off, Claude checks on it about
every 25 seconds instead). If you pick the Claude Code agent inside Claude Code, the agent and the relaying
draw on the same Claude plan.

**Is my code sent anywhere new?** Only to the agent you choose, which talks to its own provider as it would
from your terminal. CLI-MODE has no server of its own. Its settings, queue and relay logs stay on your
machine, in your host's plugin data folder. Setup installs ACPX from npm, and ACPX fetches some agents' ACP
adapters from npm the first time they run.

**What can it do on my machine?** It installs prompt hooks in your host and runs the agent CLIs you set up.
At **Allow** access an agent can edit files and run commands without asking, the same as that CLI's own
"YOLO" or bypass mode. Choose Prompt access if you want approval requests to stop the turn instead.

## Troubleshooting

- **Hooks need attention (Codex):** review or trust the plugin hooks, then recheck setup. Start a new task if
  an approved update has not loaded.
- **Full Access needs attention (Codex):** select Full Access for the current task and rerun `/cli`.
- **`/cli` does nothing (Claude Code):** check that the plugin is enabled (`/plugin`), accept the folder's
  workspace trust prompt, check `/hooks` (and that `disableAllHooks` and `--bare` are off), then run
  `/reload-plugins` or start a new session. "This session loaded an older plugin" means the same.
- **Activation fails:** check the agent CLI's sign-in, model access and quota. Antigravity's CLI and ACP
  runtime are set up separately.
- **Starting another agent is refused:** the agent limit is reached (four by default). Close one with
  `/cli close <name>`, or raise the limit with `/cli agents max <n>`.
- **A turn stopped on a permission request:** the agent needed an approval its access level cannot give. Use
  `/cli access allow`, or ask for work that needs no approval.
- **Odd behaviour after an upgrade:** from a source checkout, run
  `python plugins/cli-mode/scripts/doctor.py` to see installation conflicts, prerequisites and which ACPX
  CLI-MODE would use.

Bug reports should include your OS, host, agent, versions and the error you saw.

## Development

How it works, where state lives and how to run the tests: [Architecture](docs/ARCHITECTURE.md). Contributions:
[CONTRIBUTING.md](CONTRIBUTING.md). Requests for more agents and focused pull requests are welcome.

## Author and license

Built by **Adam Zhang** · [@dad__vibes](https://x.com/dad__vibes)

Released under the [MIT License](LICENSE). Copyright © 2026 Adam Zhang.

CLI-MODE is independent and is not affiliated with or endorsed by OpenAI,
Google, Anthropic, xAI, Cursor or GitHub.
