# CLI-MODE

![CLI-MODE: unlock third-party subscriptions in Claude Code and Codex](docs/images/banner.jpg)

**Every AI coding subscription you pay for, from inside Claude Code.**
CLI-MODE is a Claude Code plugin that runs Antigravity, Claude Code, Grok Build, Cursor, GitHub Copilot and
Codex CLI as your agents: you send them work from the chat, or let Claude hand it over, and their answers come
back into the conversation you were already in.

**Built for Claude Code** (first-party support; new features land here first) · Also runs in Codex, with fewer
features · **Windows only for now** · Six agents over the [Agent Client Protocol](https://agentclientprotocol.com/)
· MIT

## Quick install

You need Windows 10 or 11, **Claude Code 2.1.147 or later**, Python 3.10+, Node.js 22.13+, and at least one
agent CLI signed in to its own subscription (setup checks each one and guides the rest).

```bash
claude plugin marketplace add adamczhang/CLI-MODE@v0.4.0 --sparse .claude-plugin plugins
claude plugin install cli-mode@cli-mode
```

Then, in a Claude Code session in your project folder:

```text
/cli-mode:cli shortcuts
/cli
```

The first line adds `/cli` and `/d` to autocomplete; `/cli` picks your AUTO agent and runs its setup. Then just
talk to Claude: in AUTO, Claude Code's default, it works with your agent and hands it the work worth handing
over. `/cli mode direct` lets you send every task yourself with `/d <your task>` instead. The
[release zip](https://github.com/adamczhang/CLI-MODE/releases/tag/v0.4.0) has an installer that also checks
Python, Node and Claude Code; Codex installs are under [Codex](#codex).

## The problem

1. **Your subscriptions live in separate apps.** Many developers pay for several AI coding tools: Claude, Codex,
   Grok, Antigravity, Cursor, GitHub Copilot. Each one lives in its own app or terminal with its own chat, so
   using the credits you already pay for means switching apps and losing your place, or leaving the host and
   workflow you like for a multi-agent app.
2. **Each model is best at something, and combining them is manual.** Advanced users want one agent to build,
   another to review, a third to research, each on the model that suits it, from their own environment and
   without copying prompts and answers between windows by hand.

## Introducing CLI-MODE

CLI-MODE brings those agents into Claude Code (and Codex). Each agent runs **its own CLI, on its own
subscription, with its full capabilities**: CLI-MODE drives it over the Agent Client Protocol and relays its
plan, progress and answer into your chat. There is no second app, no second chat window and no terminal to
watch, and no server of CLI-MODE's own: everything runs on your machine.

You choose how much to drive yourself. In **AUTO** (Claude Code's default) you only talk to Claude, and Claude
decides what to hand to your agents, writes the task, checks the result and tells you how it went. In
**DIRECT** you send work to an agent yourself with `/d`.

## What it unlocks

| Feature | What it unlocks |
|---|---|
| **Six agents, one chat** | Use Antigravity, Claude Code, Grok Build, Cursor, GitHub Copilot and Codex CLI from Claude Code, each on its own plan. |
| **AUTO mode** (Claude Code's default) | Claude Opus leads and checks; a cheaper engine from another provider does most of the work: long jobs and parallel parts, each on its own agent. |
| **Several named agents at once** | A builder, a reviewer and a researcher working side by side; one prompt to several agents to compare answers. |
| **Change receipts, diff and undo** | Every turn ends with what changed; `/cli undo` puts an agent's turn back. |
| **Your tests after every change** | The answer says whether your tests still pass. |
| **Working folders and hand-offs** | Each agent keeps notes and drafts out of git, and any answer can be passed to another agent in one paste. |
| **Approvals in the chat** | At Prompt access, an agent that wants to run a command or edit a file asks you in the conversation. |
| **Persistent agents** | Each agent keeps its conversation across prompts, and can be brought into a new session. |
| **Guided setup** | `/cli` checks each agent's CLI, sign-in and model access, and guides installation. |

## Supported CLIs

| Agent | Tag | Access levels | `/cli usage` | Install guide |
|---|---|---|---|---|
| Antigravity | `agy` | Allow, Prompt | ✓ | [Antigravity CLI](https://antigravity.google/docs/cli/install/) (also needs its ACP runtime) |
| Claude Code | `cla` | Allow, Auto-edit, Prompt | ✓ | [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) |
| Grok Build | `gro` | Allow, Prompt | — | [Grok Build](https://docs.x.ai/build/overview) |
| Cursor | `cur` | Allow | — | [Cursor CLI](https://cursor.com/docs/cli/overview) |
| GitHub Copilot | `cop` | Allow, Prompt | ✓ (monthly requests) | [Copilot CLI](https://docs.github.com/copilot/how-tos/copilot-chat/use-copilot-chat-in-the-command-line) |
| Codex CLI | `cod` | Allow | ✓ | [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) |

Every command that takes an agent accepts its tag or full name (`cla` or `claude`, `gro` or `grok`), and the
tag starts its generated names (`GRO-4K`). Each agent works in the conversation's folder with its own account
and model access. Up to four run at once, in any mix; conversations do not move between providers.

## Claude Code features

Claude Code is CLI-MODE's primary host: features are designed for it first, and every change is checked
against it. Development toward the first release focuses on Claude Code.

### DIRECT and AUTO modes

**AUTO** is Claude Code's default: you talk to Claude only. In **DIRECT** your words go, with `/d`, to the agent
you name. `/cli mode` opens the Mode page, which switches between them; DIRECT, once chosen in a conversation,
stays until you switch back.

**Which to use.** The two modes are built for different goals:

- **DIRECT is the pass-through, and you orchestrate.** Every `/d` goes to the agent as you wrote it, with none of
  AUTO's planning and checking in between. You decide how many agents run and how the work is split. Use it when
  you want your CLI agent's subscription to do all the work, with Claude's usage kept to a minimum.
- **AUTO is a partnership, not a pass-through.** Claude keeps quick work and anything that needs its judgment or
  this conversation, and hands your agent the work worth handing over: a long job, a long message full of data,
  independent parts that can run at once (each on its own agent), a review, or whatever you ask the agent to do.
  Each handoff costs about half a minute of Claude's own turns, so sending every small request through AUTO would
  be slower than either mode.
- **Pair Opus with a cheaper execution engine.** AUTO's use case is Claude Opus as the lead, planning, writing
  well-specified tasks and checking each result, with a less expensive model from another provider (a cheaper API
  model or another subscription, such as Gemini Flash through Antigravity) carrying out the work. The aim is to
  move most of the work, around 60%, off Claude and onto that cheaper engine, while Claude keeps the parts that need
  its judgment. A second top-tier model as the agent costs about as much as Claude doing the work itself: it buys a
  second opinion, not savings.

1. Run **`/cli`**. The first time, pick your **AUTO agent** and its model, the usual way; the choice is
   remembered for every conversation, and afterwards `/cli` offers it as **1**. Any other agent you start from
   that list (or with `/cli <agent>`) becomes your AUTO agent. The AUTO agent (and backup, if you set one) starts
   at once and waits, so a handoff never waits for an agent to start.
2. Ask Claude for what you want. Work it can finish quickly, Claude does itself. For a long job (a feature built
   to a spec, a refactor across files, many tests), big independent parts or a review, Claude posts
   `Passing to Codex-01: …`, and the agent's work shows as a row in background tasks.
3. When the agent finishes, its result wakes Claude: what changed, the test result, the agent's report, and
   CLI-MODE's own check of it. Claude looks only at what that check names and tells you what was done and anything
   left open.

AUTO is built to cost Claude little: handing over takes Claude one tool call, the result arrives with the
wake-up (nothing to fetch), and Claude is given AUTO's rules once, not on every message.

What to know about AUTO:

- **`/d <question>` asks Claude itself:** nothing from that turn goes to an agent (CLI-MODE refuses a handoff
  then), and Claude may edit freely that turn. A plain message lets Claude decide what to hand off.
- The commands that change which agent does what are Claude's and CLI-MODE's in AUTO (`/cli spawn`, `use`,
  `model`, `effort`, `menu`, `timeout`, `attach`, `brief`); while they run, `/cli` opens the Mode page.
  **`/cli mode direct`**
  switches back: your agents keep running and `/d` reaches them directly again.
- **You keep the safety controls:** `/cli cancel` (every agent's running turn), `/cli list` (with a ledger of the
  handoffs), `/cli usage`, `/cli view`, `/cli access`, and `/cli off`, which closes every agent (the next `/cli` starts
  your AUTO agent again). The commands that act on one agent's piece of the work (`/cli undo`, `diff`, `queue`,
  `resume`, `dir`, `test`, `progress`, `cancel <name>`) are Claude's in AUTO: ask Claude to undo a change, show a
  diff or run the tests. `/cli help` shows AUTO's own card.
- **The agent's questions come to you.** If it stops to ask permission, Claude tells you what it asks; answer
  with `/cli approve` or `/cli deny`, and it goes on with Claude's task.
- **Several agents, one writer per file.** Each task Claude writes names the files it may change, and no two
  running tasks, nor Claude itself, change the same file; a task without a Files line claims the whole project,
  and `Files: none` (work that writes only in the agent's own working folder, such as a simulation) claims no
  project file. If an agent edits a project file its task didn't name, Claude's copy of the result says so. So
  work on separate files runs in parallel, and Claude can start more agents like your AUTO agent for it
  (`Codex-02`, `Codex-03`, up to the agent limit). Reviews and research run alongside as read-only handoffs,
  which refuse writes. Change receipts and overlap warnings work as in DIRECT, and an undo (ask Claude) puts back
  only the agent's own edits.
- **Agents stay loaded.** AUTO's agents wait between tasks, and close after two hours idle or with `/cli off`.
- **Delegation strength** (`/cli mode strength normal|strong|max`, or the AUTO settings page): at **Normal** (the
  default) Claude works itself and hands off only what is worth it; at **Strong** Claude keeps only small pieces of
  work (about 40 lines an edit, three files a turn: a bug fix with its test) and hands off anything bigger, which
  saves more of Claude's usage and takes longer; at **Max** every project change goes to the agent. It steers
  Claude's own edit tools; it is not a sandbox.
- **Settings:** `/cli mode agent [<agent> [<model>]]` changes the AUTO agent; `/cli mode backup <agent>|none`
  sets a backup, used when the AUTO agent fails or is out of usage.
  Choosing an AUTO agent, from the Mode page or by command, always turns AUTO on and starts it (and the backup).
  The AUTO settings page (or `/cli mode effort <level>` and `/cli mode fast on|off`) sets the AUTO agent's effort
  and Codex's own fast mode; running agents take them in place, with no restart.
- **Long prompts go by reference:** a long message (4,000 characters or more), such as a spec with data pasted in,
  is saved for the agents to read, and Claude's task points to it instead of copying the data out again.
- **For developers:** each task Claude writes is kept in `Agent_Working_Folder/.cli-mode/tasks/` (Goal,
  Context, Inputs, Files, Do not, Done when, Report); agents never commit or push; each result carries the change receipt and
  the test result. The agent reads its own instruction files (`AGENTS.md`, `CLAUDE.md`) for your conventions,
  and in AUTO the project brief is not used.

### How it feels in Claude Code

- **Agents are background tasks,** like Claude's own subagents: Claude posts "Passing to …" and ends its turn,
  the agent's work shows as a row named after it and the task, with one line per step, and when it finishes,
  Claude posts its whole answer (DIRECT) or checks it for you (AUTO). Nothing polls while it works. With
  background tasks off (`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`), Claude checks on the agent about every 25
  seconds instead, and AUTO is not available.
- **Only agents in background tasks.** Starting an agent (10–45 seconds) runs inside CLI-MODE's hook, so your
  prompt shows a "CLI-MODE" status line and the confirmation card is the reply. Raising an agent's access is the
  exception: Claude Code's permission prompt asks you first.
- **Replies are chat messages.** Menus and confirmations are posted as normal chat, one small Claude turn each;
  **`/cli display instant`** shows them at once with no model turn (the desktop app frames that as "blocked by
  hook"), **`/cli display chat`** switches back.
- **Commands.** After a zip install, `/cli` and `/d` autocomplete as typed. After a GitHub install, Claude Code
  offers `/cli-mode:cli` and `/cli-mode:d`; **`/cli-mode:cli shortcuts`** adds `/cli` and `/d` (a command file
  of your own with either name is left alone). Help is **`/cli help`**.
- **Interrupting is safe.** Stopping Claude mid-relay (Esc) or an agent's row stops only the watching; the agent
  keeps working. Your next `/d`, or **`/cli resume`**, shows what you missed without resending anything.
  **`/cli cancel`** stops the agent's turn itself.
- **Attachments:** files and pasted images attached to a `/d` are copied into the agent's folder and named in its
  task.
- **Green titles** on the desktop and in the mobile app; the terminal shows them as raw LaTeX, so
  **`/cli color off`** switches to plain bold.
- **`/cli reset`** sets aside this session's saved state if it ever becomes unreadable.
- **Every session runs the hook,** but it answers in about 35–50 ms and does nothing in sessions that don't use
  CLI-MODE.

## Codex

CLI-MODE also runs in the Codex desktop app, from the same plugin source. It is supported and tested (a recorded
record of every hook reply keeps Codex's behaviour fixed while Claude Code changes), but new features reach it
later, or not at all.

**Install** (PowerShell or Bash):

```bash
codex plugin marketplace add adamczhang/CLI-MODE --ref v0.4.0
codex plugin add cli-mode@cli-mode
```

Open a new task with **Full Access**, run `/cli`, and if prompted approve the hooks under **Plugins → CLI-MODE →
Hooks → Review / Trust all**, then recheck setup. Using both hosts? Install both: they share one ACPX
installation and each agent's sign-in; conversations and settings stay separate per host.

**Where Codex stands:**

| Feature | Claude Code | Codex |
|---|:---:|:---:|
| Six agents, guided setup, model, effort and access per agent | ✓ | ✓ |
| `/d`, several named agents, one prompt to many | ✓ | ✓ |
| Approvals in the chat, attachments | ✓ | ✓ |
| Change receipts, diff, undo, test runs, same-file warnings | ✓ | ✓ |
| Working folders, saved answers, copy box, project brief | ✓ | ✓ |
| Persistent agents, attach from an earlier session | ✓ | ✓ |
| **AUTO mode** (Claude hands work over) | ✓ | — |
| Agents as background tasks; the answer arrives when the agent finishes | ✓ | — |
| Live progress | Task row, viewer window | In chat, viewer window |
| Menus and results | Chat text | Inline views |

- **No AUTO:** Codex has no background task that wakes the model when an agent finishes, which AUTO needs. `/cli
  mode` on Codex says that only `/d` reaches an agent.
- **Relaying happens inside Codex's turn:** the agent's words arrive as chat updates in readable batches, then
  one view shows the final message and a collapsible **work** section with the plan and tool activity.
- **Help** is also `/help` on Codex.

## Install

**Claude Code, from the release zip:** download `cli-mode-claude-0.4.0.zip` from the
[v0.4.0 release](https://github.com/adamczhang/CLI-MODE/releases/tag/v0.4.0), extract it, and run in PowerShell:

```powershell
.\install-claude.ps1
```

It also checks Python, Node and your Claude Code version, and adds `/cli` and `/d` to autocomplete.
**From GitHub:** see [Quick install](#quick-install). **Codex:** see [Codex](#codex).

**What setup installs:** a pinned copy of ACPX for CLI-MODE (unless a global `acpx@0.18.0` from npm is already
installed, which CLI-MODE then uses), and each agent's ACP adapter the first time it runs.

**Upgrading?** A GitHub install stays on its tag; the
[release notes](RELEASE_NOTES.md#upgrading-from-an-earlier-03-release) show how to move it to a new release and
keep your settings.

Release **0.4.0** · [Release notes](RELEASE_NOTES.md) · [Changelog](CHANGELOG.md)

## Get started

1. Start a Claude Code session in your project and accept the folder's workspace trust prompt (on Codex, a new
   task with **Full Access**).
2. Run **`/cli`** and choose an agent. Setup checks its dependencies and guides installation and sign-in.
3. Accept the defaults or choose the model, effort and access.
4. On Claude Code, that agent is now your AUTO agent: ask Claude for what you want, and it hands over the work
   worth handing over (see [DIRECT and AUTO modes](#direct-and-auto-modes), and which mode suits you).
5. To drive an agent yourself (DIRECT, and always on Codex), send your prompt with `/d`. CLI-MODE announces
   `Passing to Claude CLA-4F...`, for example, and relays the answer under `Claude CLA-4F says...`. `CLA-4F` is
   the agent's name. On Claude Code, `/cli mode direct` switches to DIRECT.

## Sending a prompt to an agent

In DIRECT, only a message that starts with `/d` (or `$d`) goes to an agent, the current one unless you name
another; everything else stays with your host, so you decide exactly what each agent is asked:

```text
/d Explain how authentication works in this project.
```

`/d` does not activate an agent on its own. Messages sent while an agent is busy queue up for it and go out in
order; see [Architecture](docs/ARCHITECTURE.md) for how the queue works.

## Several agents

Every agent has a name, shown in capitals: one you give it (`/cli spawn gro ELON`, 1-10 letters and digits) or a
generated one such as `GRO-4K`. Start another agent at any time, even while others work; the newest becomes the
current agent, the one a plain `/d` goes to. Put a name first to send to another:

```text
/d gro-4k Review the parser changes.
/d elon Write tests for the parser.
```

Names match in any case, and a generated name also as `gro4k`, or as `-4K` when only one running agent has that
ending. A tag (`gro`, `cod`) names an agent too when only one of its kind is running. Each agent has its own
queue, so they work side by side, and each answer is relayed under its own name (`Grok GRO-4K says...`).
`/cli list` shows them all, `/cli use <name>` changes the current agent, and `/cli close <name>` closes one while
the others keep working.

To ask several agents the same thing, name them all, with commas between:

```text
/d gro-4k,cod-7k Review the parser changes.
/d elon, -7K Is this migration safe?
```

Each gets its own copy and works at the same time; the answers arrive one by one, each under its own name. If
any name matches no running agent, nothing is sent.

**Where agents save files.** A coding task changes your project's files as asked. Anything else an agent creates
(research notes, reports, art, drafts) goes in its own folder, `Agent_Working_Folder/<NAME>/` in the project,
such as `Agent_Working_Folder/ART/`. CLI-MODE tells the agent this with each task, and the answer ends with what
it saved there:

```text
Grok ART saved 3 files in Agent_Working_Folder/ART/: marble/face-1.svg new · marble/preview.html new · notes.md new
```

The folder is kept out of git (it holds its own `.gitignore`), so drafts never reach your history; copy what you
keep into the project. It is reported in folders outside git too. `/cli dir [name]` shows an agent's folder as a
full path and as its path in the project, with its newest files.

**Passing work between agents.** Every answer ends with a small box listing where the full answer is saved
(`Agent_Working_Folder/<NAME>/answers/`) and the files the turn created, changed or mentioned. Copy it into
another agent's `/d`, and that agent reads the exact answer and files itself:

```text
Codex RESEARCH answer: Agent_Working_Folder/RESEARCH/answers/003-compare-3d-engines.md
Files: docs/engine-report.md
```

**Safety nets.** `/cli undo [name]` puts back the files an agent's own tools changed in its last turn (only if
none changed since); another agent's edits made meanwhile are left as they are. When two agents working at once
edit the same file, the later answer warns you. After every turn that changes files, CLI-MODE runs your
project's tests (found automatically: `npm test`, `python -m pytest`, `cargo test` or `go test ./...`) and the
answer says whether they passed; `/cli test <command>` sets another, `/cli test off` turns them off.

**The project brief** (DIRECT). Every agent reads `Agent_Working_Folder/BRIEF.md` before each task. It has three
parts:

- **Agents running now:** each agent's name, working folder, whether it is working, and its last saved answer.
  CLI-MODE rewrites this before every task, so an agent closed in the meantime is gone from it.
- **From the host:** when an agent starts, your host (Claude Code or Codex) adds a dated note on what the
  conversation has been working on, or "Nothing yet". Notes are kept, so they read as a history. (With
  `/cli display instant` on Claude Code there is no model turn to write one.)
- **Your points:** `/cli brief-add <text>` adds one.

`/cli brief` shows it; `/cli brief clear` removes your points and the host's notes. In AUTO, each task Claude
writes is its agent's brief instead.

**Agents from earlier sessions.** An agent you didn't close keeps its conversation. In a new session in the same
folder, `/cli attach` lists them and `/cli attach <name or number>` brings one here; its next `/d` continues
where it left off, and the earlier session no longer has it.

## Settings and commands

- **`/cli`** — choose an agent or run setup (in AUTO: your saved AUTO agent first, and the Mode page while it
  runs).
- **`/cli mode`** (Claude Code) — the Mode page; `/cli mode auto|direct` switches, `/cli mode agent`,
  `/cli mode backup` and `/cli mode strength` set up AUTO.
- **`/cli spawn <agent> [name]`** (or **`/cli bind`**) — start an agent with saved defaults, after readiness
  checks. `<agent>` is its tag or full name, for example `cod` or `codex`.
- **`/cli list`** (or **`/cli agents`**) — the running agents by name (in AUTO, with the handoff ledger);
  **`/cli agents max <n>`** sets how many can run at once (4 by default, up to 8).
- **`/cli use <name>`** — make that agent the current one.
- **`/cli diff [name]`** — what an agent's last turn changed, as a diff.
- **`/cli usage [name]`** — each running agent's plan usage from its own CLI (or that it can't report it).
- **`/cli timeout [name] <time>`** — how long an idle agent keeps running, from 5 minutes to 24 hours (`90`,
  `90m`, `2h`; 1 hour by default). Without a name it sets the default for every conversation and this one's
  agents. `/cli timeout` shows the values.
- **`/cli attach [name]`** — bring an open agent from an earlier session in this folder here.
- **`/cli menu [name]`** (or **`/cli settings`**) — open an agent's settings page; the current agent's by default.
- **`/cli model <choice>`**, **`/cli effort <choice>`**, **`/cli access <choice>`** — change a setting, with an
  agent's name first for another agent (`/cli model elon opus`). CLI-MODE matches your wording (for example
  `opus`, `extra high` or `bypass permissions`) against the agent's options and applies a unique match; if the
  choice is unclear it shows the menu.
- **`/cli progress activity`** or **`/cli progress quiet`** — show tool activity and usage (default), or only
  messages and plans. Applies from the next turn.
- **`/cli view on`** or **`/cli view off`** — watch each agent turn live in its own PowerShell window: the agent's
  text, tool activity, plans and the result, in colour. Read-only, off by default, saved for every conversation.
- **`/cli queue`** — see queued, running and completed requests; **`/cli resume`** picks up monitoring of existing
  turns without resending anything.
- **`/cli cancel [name]`** — cancel an agent's running turn, keeping queued follow-ups.
- **`/cli approve [name] [always]`**, **`/cli deny [name]`** — answer an agent that stopped to ask for a
  permission (see Access levels below).
- **`/cli close [name|all]`** (or **`/cli stop`**, **`/cli off`**) — close one agent, or all of them (in AUTO,
  all of them). With several running and no name, it asks which. Closing the last agent returns you to your
  host.
- **`/cli help`** (or **`/cli commands`**; on Codex also **`/help`**) — show the command card. Reply X to close
  it.

`$` works in place of `/`, and controls are case-insensitive. Help and controls always stay local. Closing help
or settings keeps the agent running.

**Access levels.** Every agent starts at Allow, where it edits files and runs commands without asking (see
[Supported CLIs](#supported-clis) for each agent's levels). At the other levels you approve in the chat: when
the agent asks for something its level does not grant, its turn stops and the answer asks you:

```text
Grok GRO-4K asks to run commands: npm install
Its turn stopped for your answer (Prompt access). /cli approve lets it run commands and carry on,
/cli approve always lets it run commands from now on, /cli deny tells it no.
```

`/cli approve` sends the agent on with that kind of request allowed (editing files, running commands, deleting,
moving, fetching) for that turn; `/cli approve always` keeps it allowed while the agent runs; `/cli deny` tells it
no and lets it carry on without it. Add a name when several agents are waiting (`/cli approve gro-4k`). A new
`/d` to the agent also settles the question.

Some agents (Grok Build) run commands and edit files without asking first, so an approval can't be limited to one
kind for them: their question says that `/cli approve` lets them act freely for that one turn, and
`/cli approve always` is refused (use `/cli access allow` to let them act freely from now on). Access is shown as
the shared level followed by the agent's own name for it, for example `Allow (Bypass permissions)` or
`Allow (YOLO)`.

## What to expect

- **Persistent context:** each agent keeps its own conversation; follow-up prompts continue it, and settings
  changes keep it. An agent idle for its timeout (1 hour by default) stops its process; the next task starts it
  again in the same conversation, with a slower first reply.
- **Change receipts:** in a git repository, each answer ends with what changed in the folder during the turn:
  files, lines added in green and removed in red, for example `Codex COD-7K changed 2 files +42 -7`.
  `/cli diff` shows the full diff. Agents that share a folder at the same time share its changes too. Your
  staging area is never touched.
- **Only public output:** private reasoning and raw tool inputs and outputs are never relayed.
- **Theme:** menus and views follow your host's light or dark theme; CLI-MODE's own lines are green.
- **Provider commands:** an agent's slash commands run as commands. CLI-MODE refuses ones that would sign you out
  or change the model, effort or access behind its back (`/logout`, `/model`, `/permissions`, `/allow-all` and
  similar) and points to the `/cli` control instead. Unknown commands are refused before anything is sent.
  Antigravity's native commands hand off to its own CLI in a fresh conversation, and CLI-MODE says so.
- **Usage:** `/cli usage` asks every running agent's own CLI what it has used, without a model request: Claude
  (five-hour and weekly), Codex (its plan windows), Copilot (monthly requests left, when the GitHub CLI is signed
  in as the same account) and Antigravity report their limits; Grok and Cursor can't report it through their
  CLIs. The activation card shows Claude's and Antigravity's.
- **Attachments:** files and pasted images attached to a `/d` in the Claude Code or Codex desktop app are copied
  into each named agent's folder (`Agent_Working_Folder/<NAME>/attachments/`) and named in its task. They are
  removed when the agent closes; `/cli dir` lists them. Local file paths in your prompt work too.
- **MCP tools** are configured in each agent's own CLI, not through CLI-MODE.

## FAQ

**Whose credits does it use?** The agent you pick runs on its own account and subscription, exactly as if you ran
its CLI yourself. Your host also does some work: see the next question.

**Does it cost Claude turns?** A few small ones. Each menu reply is one short turn (none with
`/cli display instant`); in DIRECT each agent turn takes two: one to pass it on, one to post the answer. In AUTO,
Claude writes the task and later reads and checks the result, a little more, but the agent does the reading,
writing and testing on its own plan. Nothing runs while the agent works. If you pick the Claude Code agent
inside Claude Code, the agent and the host draw on the same Claude plan. On Codex, relaying happens inside
Codex's own turn.

**Is my code sent anywhere new?** Only to the agent you choose, which talks to its own provider as it would from
your terminal. CLI-MODE has no server of its own. Its settings, queue and relay logs stay on your machine, in
your host's plugin data folder. Setup installs ACPX from npm, and ACPX fetches some agents' ACP adapters from
npm the first time they run.

**What can it do on my machine?** It installs prompt hooks in your host and runs the agent CLIs you set up. At
**Allow** access an agent can edit files and run commands without asking, the same as that CLI's own "YOLO" or
bypass mode. Choose Prompt access to approve its requests in the chat instead.

## Troubleshooting

- **`/cli` does nothing (Claude Code):** check that the plugin is enabled (`/plugin`), accept the folder's
  workspace trust prompt, check `/hooks` (and that `disableAllHooks` and `--bare` are off), then run
  `/reload-plugins` or start a new session. "This session loaded an older plugin" means the same.
- **Hooks need attention (Codex):** review or trust the plugin hooks, then recheck setup. Start a new task if an
  approved update has not loaded.
- **Full Access needs attention (Codex):** select Full Access for the current task and rerun `/cli`.
- **Activation fails:** check the agent CLI's sign-in, model access and quota. Antigravity's CLI and ACP runtime
  are set up separately.
- **Starting another agent is refused:** the agent limit is reached (four by default). Close one with
  `/cli close <name>`, or raise the limit with `/cli agents max <n>`.
- **A turn stopped on a permission request:** the agent needed an approval its access level cannot give. Use
  `/cli access allow`, or ask for work that needs no approval.
- **AUTO says its agent is not running:** `/cli mode auto` starts it again from your saved choice.
- **Odd behaviour after an upgrade:** from a source checkout, run `python plugins/cli-mode/scripts/doctor.py` to
  see installation conflicts, prerequisites and which ACPX CLI-MODE would use.

Bug reports should include your OS, host, agent, versions and the error you saw.

## Development

How it works, where state lives and how to run the tests: [Architecture](docs/ARCHITECTURE.md). Contributions:
[CONTRIBUTING.md](CONTRIBUTING.md). Requests for more agents and focused pull requests are welcome. Work toward
the first release focuses on Claude Code; Codex keeps its current features, protected by a recorded copy of its
replies.

## Author and license

Built by **Adam Zhang** · [@dad__vibes](https://x.com/dad__vibes)

Released under the [MIT License](LICENSE). Copyright © 2026 Adam Zhang.

CLI-MODE is independent and is not affiliated with or endorsed by OpenAI, Google, Anthropic, xAI, Cursor or
GitHub.
