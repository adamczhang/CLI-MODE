# Changelog

## 0.3.8 — Honest approvals and /cli usage — 2026-09-26

Found by a full live validation of 0.3.7 on the Codex host (`checks/codex-validation-plan.md`).

### Approvals (both hosts)
- **Honest approvals for agents that act without asking.** Grok Build runs commands and writes files straight through ACPX's terminal and file system, without asking first. An approved turn runs at ACPX's approve-all (its own write and terminal checks read only the mode), so for such an agent an approval could not be limited to one kind: after `/cli approve always` for edits, Grok also ran commands freely. Now such an agent is marked (Grok from the start, any other agent as soon as it is seen doing this): its question says that approving lets it act freely for that one turn, and `/cli approve always` is refused with a pointer to `/cli access allow`. Agents that ask first keep kind-limited approvals.
- **A command run without asking is a question too.** When ACPX refused such a command (or file write) because nobody could be asked, the answer said the permission "could not be read" and `/cli approve` had nothing to approve. It now asks "Grok GRO-4K asks to run commands: git status" like any other request.

### Relays (both hosts)
- **An agent's paragraphs no longer run together with `/cli progress quiet`.** Text sent before and after a tool call under one message ID (as Grok Build does) was joined mid-line ("I will run the tests.The tests passed."), because quiet progress drops the tool events that separate them. The bridge now starts a new paragraph after any tool call.

### /cli usage (both hosts)
- **`/cli usage`** shows every running agent's usage from its own CLI, all asked at once and with no model request; **`/cli usage <name>`** (or `-7K`) shows one. Each agent reports what it can and the others say why they can't:
  - Claude: five-hour and weekly windows (its local `/usage`); Antigravity: its quota windows (as before).
  - Codex CLI: its plan windows, from `account/rateLimits/read` on a short-lived `codex app-server`.
  - GitHub Copilot: monthly premium and chat requests left, from GitHub's `copilot_internal/user` through the GitHub CLI, read only when `gh` is signed in as the account Copilot CLI uses.
  - Grok Build and Cursor: "Usage reporting not supported through its CLI" (neither CLI reports its plan's limits).
- The help card has `/cli usage [name]`.

### Validation tools
- `checks/codex_release_validation.py` runs the plan's Part 2 (brief, approvals, attachments, undo, quiet relays) against the installed Codex plugin through `codex app-server`. `codex_user_validation.py` accepts the host's brief note edit and the host's own work on turns that are not CLI-MODE's.

## 0.3.7 — Agents that know each other — 2026-09-26

### The project brief tells agents about each other (both hosts)
- **Agents running now.** `Agent_Working_Folder/BRIEF.md` lists every running agent: its name, working folder, whether it is working and its last saved answer. CLI-MODE rewrites the list before each task, after each turn and when an agent closes, so it is always current. Each task now starts "you are <Agent NAME>", so an agent knows which entry is its own, and names the brief every time.
- **A note from the host.** When an agent starts, the brief gets a dated entry ("…, when Grok GRO-4K started") and the host (Claude Code or Codex) writes 2 to 6 lines on what the conversation has been working on, or "Nothing yet", in the same turn that shows the activation. Earlier notes are kept. With `/cli display instant` on Claude Code there is no model turn, so the entry stays unwritten.
- On Claude Code, CLI-MODE's permission hook approves that one edit (an `Edit` of this project's `BRIEF.md` that replaces a "not written yet" line with plain lines), so a new agent never asks you to allow it; any other edit is left to Claude Code's permissions.
- The brief has three sections: your points, the host's notes and the agents running now. A 0.3.5 brief is read as points. `/cli brief` shows all three; `/cli brief clear` clears your points and the host's notes.

### Fixes (both hosts)
- **The test gate's time limit ends the whole test run.** On Windows, a run past its 10 minutes ended only the shell, and waiting for the output then blocked on the test runner still holding it, so the agent's queue stalled and the project's test lock stayed taken. The run and everything it started are now ended together.
- **`/cli undo` gives a file back its own line endings.** It wrote files back as git stores them, so with Git for Windows' default `core.autocrlf` (or an `eol` rule in `.gitattributes`) a restored CRLF file came back with LF endings. Files are now written as a checkout writes them.
- **A permission question goes stale when the agent moves on.** A queued `/d` that ran after the stop left the question open, so a later `/cli approve` sent an out-of-date approval. The agent's next turn now settles it.
- **`/cli undo` no longer undoes another agent's work.** It restored every file in the turn's change receipt, which covers the whole folder, so a file another agent edited while the turn ran was put back too. It now restores only the files the agent's own tools edited (as its edit steps report them), and names the receipt's other files, left as they are. A file an agent changed only through a shell command is not undone.
- A turn with an approval uses a one-off ACPX client that is closed after the turn, instead of adding one to the warm bridge per approval.

### Faster /d on Claude Code
- **Pasted images no longer cost every later `/d` a full transcript read.** Finding a `/d`'s images read the whole conversation transcript (which holds every pasted image) on each `/d` once the session had any upload. Uploads already sorted are now remembered, and the transcript is read only when a new one appears.

### Cleanup
- Removed two unused adapter functions and 41 superseded validation reports, evidence files and one-off live scripts from before 0.3.

## 0.3.6 — Approvals in the chat, attachments on /d — 2026-09-26

### Approvals in the chat (both hosts)
- **An agent that needs a permission asks you.** At Prompt or Auto-edit access, the turn still stops at the first request its level does not grant, but the answer now says what the agent asked for, from its own request (`Grok GRO-4K asks to run commands: npm install`), and how to answer. The access label reads "you approve in chat" instead of "approval requests stop the turn".
- **`/cli approve [name]`** sends the agent on (as its next `/d`) with that kind of request allowed for that turn: editing, deleting or moving files, running commands, fetching; an odd tool is approved by its exact title. Anything else it asks for on that turn stops it and asks again. **`/cli approve [name] always`** keeps the kind allowed while the agent runs. **`/cli deny [name]`** tells it no and lets it carry on. Without a name they answer the only agent waiting; a new `/d` to the agent also settles the question.
- Under the hood an approved turn sends ACPX a per-prompt permission rule (the approved kinds and reads pass, anything else escalates) at approve-all mode, because ACPX's own file-write and terminal checks read only the mode. An escalated request makes the bridge cancel the turn and report it as a permission stop.

### Attachments on /d (both hosts)
- **Files and pasted images attached to a `/d` reach the agent.** Claude Code's desktop app sends each attached file as an `@"path"` mention before the text and keeps pasted images only in its uploads folder; Codex's desktop app lists attached files, pasted images too, above the text. CLI-MODE takes them off the prompt, copies them into each named agent's `Agent_Working_Folder/<NAME>/attachments/` (a clashing name gets `-2`; files over 250 MB are left out) and names the copies in the task. A prompt that is not a `/d` is untouched.
- **A `/d` with a file attached is a `/d`.** The mention in front of it used to hide the `/d`, so the prompt went to the host instead.
- Attachments are not reported as files the agent saved. `/cli dir` lists them, and closing the agent removes them; its own files and saved answers stay.

### Help and docs
- The help card has `/cli approve|deny`; on Claude Code its "More:" line is one line shorter to stay under 50 lines.
- The README's host table lists features by what they do, and the README, release notes and `docs/ARCHITECTURE.md` explain approvals and attachments.

## 0.3.5 — Passing work between agents — 2026-09-26

### Agent to agent (both hosts)
- **A copy box under every answer.** Each agent's full answer is also saved as `Agent_Working_Folder/<NAME>/answers/NNN-<task>.md`, and the relayed answer ends with a small box listing that file and the files the turn created, changed or mentioned (existing ones only). On Claude Code the box has a copy button: paste it into another agent's `/d` and that agent reads the exact answer and files from the project. The answer above the box is shown as before.
- `/cli dir` counts an agent's saved answers apart from its own files.

### Safety nets for several agents (both hosts)
- **`/cli undo [name]`** puts back the files an agent's last turn changed, from the snapshots taken for its receipt: all or nothing, and only if none of those files changed since (by you or another agent). A file the turn created is removed.
- **Same-file warning.** When two agents' turns overlap and both edited the same file, the later answer says so (`⚠ Also edited by Codex COD-7K while this turn ran: app.py`). It comes from each agent's own edit steps, not the folder-wide receipt.
- **Test gate, on by default.** CLI-MODE finds a project's test command from its files (`npm test` for a package.json test script, `python -m pytest`, `cargo test`, `go test ./...`); a folder with no test setup runs nothing. `/cli test <command>` sets another (one line only), `/cli test off` turns it off for good, `/cli test auto` goes back to detection. After each agent turn that changes project files, CLI-MODE runs it and the answer says `✓ Tests passed` or `✗ Tests failed` with the runner's summary. The full output is kept in the agent's folder, and a failure's log comes first in the copy box. Runs are one at a time per project, with a 10-minute limit. `/cli test` shows the command and where it came from.
- An unknown word after `/cli` is named (`CLI-MODE has no /cli frobnicate.`) instead of a suggestion to activate.
- **Shared brief.** `/cli brief-add <text>` adds a point to `Agent_Working_Folder/BRIEF.md`, which every task asks the agent to read first; `/cli brief` shows it and `/cli brief clear` removes it. Your text is applied in the hook, never passed through a command line.

## 0.3.4 — Agent working folders — 2026-09-25

### Agent working folders (both hosts)
- **Each agent has a folder for its files: `Agent_Working_Folder/<NAME>/` in the project.** A coding task still changes the project's files as asked; anything else an agent creates (notes, reports, assets, drafts) goes in its own folder, flat, so it finds its earlier files. CLI-MODE adds one paragraph naming the folder to each task it sends (never to an agent's own slash command); the chat shows the prompt as typed.
- **Saved-files receipt.** Each answer ends with what the agent saved there (new, changed, removed), under the change receipt, in the Claude Code chat, the Codex view and the background-task row. It comes from listing the agent's folder, so it works outside git, stays fast for images and audio, and names the right agent even while several work at once.
- The folder holds a `.gitignore` of `*`, so agent files stay out of git and out of the change receipt; the project's own `.gitignore` is untouched.
- **`/cli dir [name]`** shows where an agent saves its files: the full folder path (to open or paste elsewhere), its path in the project, and its newest files. It takes a name, a tag or the short form (`-7K`); without one, the current agent.

### Help (both hosts)
- **A compact help card.** `/cli help` shows one short line per command, grouped under AGENTS, SEND WORK, RESULTS and SETTINGS (green headings), with the rarer commands and the placeholders (`<agent>`, `<name>`) at the end: about 46 lines instead of 95 on Claude Code, and nothing wraps in the 40-column card. The README still explains every command in full.

### Claude Code
- **Agents that finish at the same time no longer lose an answer.** When several agents' turns ended together, Claude ran one relay per agent in a single turn and posted only the last one's answer. The wake-up now relays every agent that has finished but not been posted with one command, so their answers arrive together, oldest first. An agent that finishes during that turn gets its own relay, and each relay now asks for the turn's last message to carry every relay's output, so none is folded out of view or dropped.
- A wake-up that carries several task notifications, Claude's own among them, still finds CLI-MODE's follow.

### Docs
- The README and `docs/ARCHITECTURE.md` say when setup installs CLI-MODE's own ACPX: only when no ACPX 0.18.0 is found; a global `acpx@0.18.0` from npm is used instead.

## 0.3.3 — Antigravity's temp folder — 2026-09-25

### Antigravity
- **Antigravity no longer fills the temp folder.** Its ACP server unpacks about 1.25 GB into `%TEMP%` every time it starts and deletes that copy only when it exits by itself. Ending it, as CLI-MODE does when an agent closes or its idle timeout passes, skipped that clean-up, so every start left a copy behind. The managed launcher now gives each server a temp folder of its own and, on every start, removes the folders of servers that have ended.
- Copies left by earlier versions stay in `%TEMP%` as `_MEI…` folders of about 1.25 GB each. They can be deleted while no Antigravity agent is running.
- A launcher configured by hand in `~/.acpx/config.json` is kept by setup and does not get this fix.

## 0.3.2 — Several named agents — 2026-09-24

### Several named agents (both hosts)
- **Up to four agents run at once, in any mix, each with a name.** `/cli spawn <agent> [name]` starts one at any time, even while others work, and it becomes the current agent. A name you give is 1-10 letters and digits; otherwise one is generated from the agent's code and two characters, such as `COD-7K`. Names show in capitals and match in any case.
- `/d <name> <prompt>` sends to that agent; a plain `/d` goes to the current one. A generated name also matches as `cod7k`, or as `-7K` when only one running agent has that ending. Only the first word after `/d` can name an agent, and a word shaped like a generated name that matches none sends nothing.
- Each agent has its own queue and worker, so agents work side by side. Answers, "Passing to" lines, background-task rows and the activation card name the agent (`Codex COD-7K says...`).
- `/cli list` lists the running agents with their settings and what each is doing; `/cli agents max <n>` sets the limit (up to 8). `/cli use <name>` changes the current agent.
- `/cli close <name>` closes one agent (its turn, queue and session) while the others keep working; `/cli close all` closes them all. With several agents, a bare `/cli close` asks which. Closing the current agent makes the most recently used one current, and closing the last turns CLI-MODE off.
- `/cli menu`, `/cli model`, `/cli effort`, `/cli access` and `/cli cancel` take an agent's name to act on another agent.
- **Agent tags:** every command that takes an agent accepts its three-letter tag, `agy`, `cla`, `cod`, `gro`, `cop` or `cur` (`/cli cla`, `/cli spawn cod`), as well as its full name. The same tag starts its generated names; the registry's `tag` field defines both.
- **Several agents at once:** `/d gro-4k,cod-7k <prompt>` (or `/d elon, -7K <prompt>`) sends the prompt to each named agent as its own request; they work at the same time and each answer arrives under its own name. A tag names an agent when it is the only one of its kind (`/d cod ...`, `/cli close gro`). If any name matches no running agent, nothing is sent; ordinary text such as "Hi, can you..." still goes to the current agent.
- **Change receipts:** in a git repository, each answer ends with what changed in the agent's folder during the turn: file count, lines added (green) and removed (red), and the files. `/cli diff [name]` shows the full diff. The snapshots use a temporary copy of git's index, so the staging area is never touched; outside git there is no receipt. The same receipt appears on Claude Code (chat) and Codex (the final view), and ends the agent's background-task row.
- **Agent timeout:** an idle agent's process now stops after 1 hour (was 30 minutes); its next `/d` starts it again in the same conversation. `/cli timeout <time>` sets the default for every conversation and this one's agents, `/cli timeout <name> <time>` one agent (5 minutes to 24 hours: `90`, `90m`, `2h`).
- **Attach:** an agent that was never closed can be brought into a new session in the same folder: `/cli attach` lists them (with when each was last used), `/cli attach <name or number>` moves one here, and its next `/d` continues its conversation. The earlier session no longer has it, so two sessions never drive one agent.
- Command pairs that do the same thing: `close` = `stop` = `off`, `spawn` = `bind`, `settings` = `menu`, `agents` = `list`, `commands` = `help` (`/cli help` now also works on Codex).
- `/cli bind` and the activation page start a new agent rather than reconfiguring the running one; settings change through Agent Settings or `/cli model|effort|access`. A `/d` task typed while any menu is open closes the menu and is sent.

### Routing
- **Passthrough mode is removed, on both hosts.** Only a message that starts with `/d` (or `$d`) reaches the agent; everything else stays with the host. `/cli mode` now says so, and the routing choice is gone from the activation and Agent Settings menus ("Toggle activity progress" is now 4).
- A conversation saved in Passthrough mode opens in Direct mode; a request it had already queued is still sent.

### Claude Code: agent turns in the background
- A `/d` turn runs as a background task: Claude starts CLI-MODE's `follow` for the request, posts "Passing to …" and ends its turn. The agent's work shows as one row in Claude Code's background tasks, named `<Agent> · <start of the prompt>`, with one line per step.
- When the agent finishes, the row ends and wakes Claude, which posts the agent's whole answer. Claude no longer spends a turn every 25 seconds checking on the agent. With `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, the previous behaviour stays.
- Activating an agent (`/cli bind`, the activation menu, `/cli model`, `/cli effort`, narrower access) runs inside CLI-MODE's hook, so it never becomes a background task of its own and costs no Claude turn. Raising access still goes through Claude Code's permission prompt.
- The only CLI-MODE commands that can still show as background tasks, raising access and the fallback relay, are named after the agent (`<Agent> · starting`, `<Agent> · answer`).

### Claude Code: reliability
- A background task's notification is never treated as a prompt. When it is the end of CLI-MODE's own follow, the new turn is given the exact relay to run; an open menu no longer takes it as a reply.
- During an agent's turn, Claude's own wake-up, scheduling and monitor tools are refused, as subagents already were: the follow's end wakes the conversation.
- Answers are never posted twice when turns overlap, and a relay with nothing left to post says so.

### Docs
- The README presents Claude Code as the primary host with first-class support; Codex is supported and tested, and new features may reach it later.

## 0.3.1 — Hooks that run on current Codex — 2026-09-24

### Codex
- **CLI-MODE's hooks run again.** Codex 0.155 runs Windows hook commands through PowerShell, where the previous `python -c __import__(...)` command was a parse error, so every hook failed and Codex fell back to reading the skill. The command now works in cmd.exe, PowerShell 7 and Windows PowerShell 5.1. Codex asks to trust the updated hooks once.
- Replies 1 and 2 on an agent's activation menu run their exact controls.
- A `/d` task typed while Agent Settings is open closes the menu and is sent, as on Claude Code.

### Claude Code
- Raising an agent's access with `/cli access allow` or the settings access menu asks first, as activating with wider access already did.
- A message the open settings page cannot take says it was not sent, instead of disappearing.

### Relays
- An answer that ends inside a code block no longer turns CLI-MODE's own lines into code.
- Terminal escape sequences from an agent are removed from chat and views.

### Errors
- A failed control shows its reason instead of Node's deprecation warning.
- Copilot's readiness error says when `GH_TOKEN` (or a similar variable) is set and overrides its own sign-in.

### Docs and validation
- The README leads with what CLI-MODE is, what it needs and a short FAQ; internals move to `docs/ARCHITECTURE.md`.
- New validation tools cover both hosts, all six agents and every renderer (`checks/v0.3.0-full-validation.md`).

## 0.3.0 — A fresh start — 2026-09-24

The repository's history restarts here, with one snapshot of the Codex plugin and its Claude Code port. This entry describes everything CLI-MODE does at 0.3.0; items marked **(new)** were added in this release. The entries below it summarise the earlier releases.

### Hosts and install
- One plugin for two hosts, Codex and Claude Code, from one source tree. Windows only.
- Codex installs from the GitHub marketplace. Claude Code installs from a release zip (`install-claude.ps1`, which keeps saved data on update) or from GitHub; `/cli shortcuts` adds bare `/cli` and `/d` to autocomplete.
- Both hosts share CLI-MODE's own pinned ACPX (0.18.0) and each agent's sign-in; conversations and settings stay separate per host.

### Agents
- Six agents over the Agent Client Protocol: Antigravity, Claude Code, Grok Build, Cursor, GitHub Copilot and Codex CLI, each on its own account and subscription.
- One persistent agent session per conversation, in the conversation's working folder. Follow-ups and setting changes keep the session.
- Per-agent defaults for model, effort and access; saved choices take precedence. Access and effort use the same names for every agent, with the agent's own term alongside (`Allow (YOLO)`).
- Claude agent **(new)**: the Opus 5.5 default uses its canonical model ID, stale model caches migrate, Haiku works without an effort selector, and usage scopes have readable labels.

### Setup and activation
- `/cli` opens agent selection; a first-time check finds what each agent is missing, and guided installation runs only after you approve it.
- `/cli bind <agent>` activates with saved defaults; the activation card shows model, effort, access and, where the agent reports it, subscription usage.
- **(new)** Installing an agent, or activating it with wider access than Prompt, asks first (Claude Code shows its own permission prompt).
- **(new)** Stopping setup cancels a running installer; on Claude Code the next menu appears when an install completes.

### Routing
- Direct mode (the default): only `/d` or `$d` prompts go to the agent. Passthrough mode sent every ordinary prompt (removed after 0.3.0).
- `/cli` controls and help always stay local and are case-insensitive; `$` works in place of `/`.

### Settings and display
- `/cli menu` opens Agent Settings; `/cli model|effort|access <choice>` matches your wording against the agent's options.
- `/cli progress activity|quiet` shows or hides tool activity and usage.
- **(new)** `/cli view on|off`: a read-only PowerShell window that shows each agent turn live, with text, tool activity, plans and the result in colour.
- Claude Code: `/cli display chat|instant` (replies as chat messages or instant notices), `/cli color on|off` (green titles or plain bold), `/cli help`, `/cli reset`.

### Relaying answers
- Codex: the agent's words arrive as chat updates during the turn, then one view with the final message and a collapsible work section (plan and tool activity). **(new)** Views render headings, nested lists, tables and code blocks, use workspace-relative paths, and show "Passing to …" once per request.
- Claude Code: "Passing to …" first, then the agent's whole answer as the turn's last message, with a one-line work summary; very long answers come in parts.
- Only public output is relayed: never private reasoning or raw tool inputs and outputs.

### Queue and reliability
- A detached worker per conversation sends prompts in order, even if the host's turn is interrupted; messages sent while the agent is busy queue behind it.
- `/cli queue`, `/cli cancel` (the running turn only) and `/cli resume` (reattach without resending); `/cli stop` closes the agent and clears the queue.
- Nothing is ever sent twice: requests are captured once with receipts, and uncertain work must be reconciled before the queue continues. **(new)** After a compaction, a cancel or an off is not repeated, and answers already relayed are not relayed again.
- **(new)** A busy or failed control is reported as itself rather than as unreadable state, and a request that was not sent says why.

### Provider commands
- An agent's own slash commands run inside its session. Commands that would sign out or change model, effort or access behind CLI-MODE's back are refused with the `/cli` control to use instead.
- Antigravity's native commands hand off to its own CLI in a fresh conversation, and CLI-MODE says so.

### Performance
- **(new)** The hook reads its input as UTF-8 on every Windows code page and reads the state file once per prompt; the activation usage lookup runs on a daemon thread.

## 0.2.1 — Same result from every Claude Code install — 2026-09-23

- Make Claude Code's two install routes end the same. Codex's skill moves to `plugins/cli-mode/codex/skills/` (its manifest's `skills` path), outside the `skills/` folder Claude Code scans, so a GitHub install no longer lists an extra `cli-mode` skill; Codex loads it from the new path. `/cli shortcuts` (Claude Code) adds `/cli` and `/d` to autocomplete after a GitHub install, with the same code the zip's installer now runs (`scripts/claude_shortcuts.py`).
- One install section in the README and release notes: pick your host, and run both if you use both.
- `AGENTS.md` and `CLAUDE.md` map the code for agents working in the repository; `checks/test_agent_docs.py` keeps them tied to the plugin version and to paths that exist.

## 0.2.0 — Claude Code support — 2026-09-23

- Install CLI-MODE in Claude Code as well as Codex, from the same source tree: a `cli-mode-claude-0.2.0.zip` whose `install-claude.ps1` installs or updates it in place (keeping saved data), or `claude plugin marketplace add adamczhang/CLI-MODE@v0.2.0`. The same six agents, setup, menus, settings, routing modes and commands work in both hosts.
- Claude Code: `/cli` and `/d` (also `/cli-mode:cli` and `/cli-mode:d`; the installer adds the bare names as personal commands so autocomplete offers them), `/cli help` in place of the built-in `/help`, `/cli display chat|instant`, `/cli color on|off` and `/cli reset`.
- Claude Code relays: "Passing to …" first, then the agent's whole output as the turn's last message, where the desktop app never folds it out of view; earlier interrupted requests come first in the same message; a Stop guard resumes a relay Claude ends early, and `/cli resume` or compaction continue from the saved cursor. Relay output is plain text, not JSON.
- Claude Code's look: green, bold sans-serif titles and names through the chat's LaTeX (kept within the desktop's limits for inline maths), menus in their 40-column ASCII box with green title rows, and plain bold for the terminal with `/cli color off`.
- Speed: the Claude hook decides most events before importing anything; setting changes go straight to the running agent session; the activation card's usage lookup overlaps the provider work.
- Antigravity: the ACP sign-in helper runs its server in a job that closes with it, so no server outlives sign-in; agy's usage lookup times out after 15 seconds (agy 1.2.9's `/usage` can hang).
- Both hosts: Agent Settings drops the duplicate "Done" row ("X. Close settings" closes it; "5" now toggles activity progress). `/cli resume` reattaches to captured work without resending prompts, and reports a blocked or failed worker.

## 0.1.16 — Equal support for every agent — 2026-09-22

- Verify all six agents live: activation, a tool-using turn, relay, provider commands, refusals and stop. Claude, Codex, Copilot, Grok Build and Antigravity passed; Cursor's account returned "Upgrade your plan to continue" for every model, so it was verified offline only.
- Separate an agent's messages wherever tool work interrupts its text, so agents that send no message IDs (Grok, Copilot, Antigravity) no longer run a preamble into the answer, and the final view shows the answer alone.
- Refuse provider commands that would sign the user out or change model, effort or access behind CLI-MODE (`/logout`, `/model`, `/effort`, `/fast`, `/permissions`, `/allow-all`, `/always-approve`, `/reset-allowed-tools`) the same way for every agent, with the `/cli` control to use instead. Unknown commands get one wording for all six.
- Show access as the shared level with the agent's own name (`Allow (Bypass permissions)`, `Allow (YOLO)`), effort from lowest to highest under one spelling (`Extra High`), and Codex models as GPT models, in menus, settings and activation messages alike.
- Render `/help` as the same framed card as every other menu.
- Match `/cli model|effort|access <text>` in the controller against the agent's advertised options and apply a unique match, instead of having the model interpret a catalog.
- Standardize on the Windows PowerShell 5.1 built into Windows: PowerShell 7 is no longer a prerequisite, and the unused Antigravity PowerShell helpers are removed. Retry Grok's network sign-in check once before reporting sign-out.
- Cache Antigravity's native command inventory for ten minutes (each lookup takes about two seconds), fix a race in the settled stop, and remove unused per-agent menu and notification code.

## 0.1.15 — Faster hook, periodic chat updates, and theme-matched views — 2026-09-22

- Run the Windows hook with a quote-free `python -c` launcher that reads `PLUGIN_ROOT` itself, instead of starting PowerShell on every prompt: 130 ms instead of 444 ms, in every Codex task. Codex runs hooks as `cmd.exe /C "<command>"`; the launcher is tested that way and three others, with spaces and `&` in the plugin path.
- Return the exact `visualize{...}` line with every view, so the model never loads the 32 KB visualize skill to show a menu or result.
- Relay mid-turn updates as chat Markdown the host posts as given (Codex renders inline views only in a final response), and end each delegated turn with one view: the final message and the nested work section. Agent output that looks like a view reference is defused.
- Use Codex's theme tokens, built-in icons and progress bar in views and menus, with the previous palette as fallback.
- Make `off` wait up to 10 seconds for submitters to unwind, so one stop reports a settled shutdown; tell the model how long `bind` and `relay` can take instead of letting it poll or sleep.
- Close a session with one warm bridge request instead of three ACPX processes, send cancel through an idle warm bridge, and let the idle worker check the state file's timestamp before parsing it.

## 0.1.14 — Fast relay, complete hook commands, and one-step activation — 2026-09-22

- Add `relay`, one blocking command that waits for new public events, batches them (800 characters, 1.5 seconds of quiet, 6 seconds held, or settlement) and renders one view per batch. The agent's words stay visible; its plan and tool activity nest inside browser-native `<details>` sections grouped by kind. The host no longer runs observe, format-message and format-progress per update, or re-types the agent's text.
- Batch logged message text into paragraphs of at least 400 characters instead of one event per line.
- Give every routed turn the complete controller command, including a fresh view path, and stop telling the model to read the skill first. A new session gets a short preload, so a cold `/cli bind` needs no skill reads.
- Make `bind`, the final `choose` and `activate` return the activation confirmation themselves, with the usage lookup running alongside activation.
- Send one readiness prompt per activation instead of two; settings are verified from session metadata.
- Reuse one bridge process per controller or worker, cache `acpx config show` until the files ACPX reported change, and report the provider session from the bridge after each turn, removing two `sessions show` calls from ordinary turns. The worker now stays ready for five minutes after draining. With an instant offline agent, activation fell from 6.7 to 4.0 seconds and a turn from 2.5 to 1.3 seconds.
- Load adapters in the routing hook only when a turn needs them.
- Cut SKILL.md from 21 KB to 6.5 KB, rewrite the relay guides around `relay`, and keep one copy of the menu transaction rules instead of six.

## 0.1.13 — Plugin-owned ACPX, clear access limits, and a leaner hook — 2026-09-22

- Install CLI-MODE's own pinned ACPX with `npm ci` from a shipped lockfile, and prefer it over any global copy. A global `acpx@0.18.0` is still found, now by its package folder instead of by matching the exact text of npm's launcher script, so an npm upgrade can no longer break discovery. Node.js 22.13+ is checked before a binding is created.
- Label every access level except Allow "approval requests stop the turn" in menus, settings and the activation message. A turn that stops on a permission request now ends with a message naming the agent, the access level and `/cli access`, instead of ACPX's raw error.
- Cut the instructions the routing hook injects on each turn by 45–80%. Delegated turns no longer carry setup and menu rules, menus no longer carry relay rules, and relay turns echo only the state fields they use.
- Make the first readiness probe of a new session, which uses the ACPX CLI, honor the same cancel signal as every later turn.
- Read ACPX credentials only from the configuration files ACPX reports it loaded, and refuse to send if their names disagree with ACPX's own list.
- On Windows, tie each bridge, ACPX CLI and native CLI process to the worker that started it, so none can outlive it; the ACPX session owner keeps any accepted turn.
- Split the 1,400-line controller into menus, binding, dispatch and queue-worker modules without changing its interface.
- Stop committing release archives. `dist/` is ignored, and each archive and checksum is attached to its tag's GitHub Release instead; the v0.1.12 links now point there. Tagged commits keep their original archives.
- Show `/help` as a single plain-text Commands table with the core controls. X closes help without changing the active agent or an underlying setup or settings menu.

## 0.1.12 — Verified startup, agent defaults, and tabbed help — 2026-09-22

- Fix first activation for Codex and Claude by allowing ACPX to establish its initial provider session before enforcing pinned identity on later turns and readiness checks.
- Report the age of queued and running requests and the time since the last public event, so a quiet agent can be described with measured wait time without implying progress.
- Complete live six-agent validation of the home menu, activation, three queued coding stages, routing modes, cancellation with a waiting follow-up, and owned-session closure.
- Start new Codex bindings on GPT-6 Sol at High effort and Cursor bindings on Composer 2.5 with provider-managed effort. Keep the existing Grok 4.7 High, Claude Opus 5 High, Antigravity and Copilot defaults; saved conversation choices still take precedence.
- Reorganize `/help` as a CSS-only tabbed in-chat guide with a complete command/description table and sections for setup, routing, tuning, progress, queue recovery and provider commands. Dismissing help with `X` now preserves the active agent and any pending menu.

## 0.1.11 — Resilient ACPX forwarding and public activity — 2026-09-22

- Keep delegated turns independent of the host Codex response: the hook starts one detached, conversation-owned FIFO worker, and steering messages queue behind the active ACPX turn.
- Add nonblocking `observe`, `queue`, and `resume` controls; preserve public event cursors and require explicit reconciliation after an uncertain worker failure.
- Make `/cli cancel` an explicit active-turn abort while `/cli stop` discards queued follow-ups; retain Direct as the default and snapshot routing for each queued message.

- Add shared ACPX tool/activity progress with public titles, lifecycle states and file locations; hide raw payloads, command arguments and private reasoning.
- Show reported context tokens, token breakdowns and cost, with duplicate suppression and burst coalescing. Tool completion never substitutes for canonical turn completion.
- Add persistent `/cli progress activity|quiet` controls (activity by default), an Agent Settings toggle, and deterministic bounded inline activity snapshots with a text fallback.

- Default new conversations and state without a routing choice to Direct; preserve explicitly saved Passthrough or Direct choices.

- Capture delegated prompt text in the routing hook and queue it by request ID, preserving Unicode, whitespace and line endings without model reconstruction.
- Persist submission receipts before provider calls; repeated IDs return existing state/results without replaying work.
- Remove captured input after submission or shutdown. Keep queued follow-ups across new messages and local controls; keep uncertain submissions protected for inspection.
- Use ACPX's public shared runtime for prompt submission and live-owner settings controls. Trust its canonical turn result instead of interpreting raw RPC failures or CLI exit-code history.
- Pin each binding to the tested ACPX 0.18.0 installation, require provider-session continuity, and refuse silent creation of replacement conversations during reconnect.
- Serialize prompt and settings admission, persist operations before dispatch, and retain uncertain controls after lost responses or checkpoint failures.
- Carry cancellation across the spawn/admission boundary and recover orphaned captured input without deleting another conversation's files.
- Preserve media through ACPX's observation journal, recover stale cursors without replaying work, and distinguish completed work from incomplete output observation.
- Report shutdown as owned-session closure without claiming verification of provider process trees. Reject unsupported ACPX MCP injection explicitly.
- Add real pinned-runtime tests with an offline ACP provider, alongside lifecycle and recovery regressions.

## 0.1.10 — Reproducible packaging and release consistency — 2026-09-22

- Normalize packaged UTF-8 text to LF so LF and CRLF checkouts produce identical archives and checksums.
- Read the ACP setup client's version from the plugin manifest.
- Point the README to current validation and repair historical report links.
- Add binary-preservation and full-package line-ending reproducibility tests.

## 0.1.9 — Initial release — 2026-09-22

- Support six persistent CLI agents: Antigravity, Claude Code, Grok Build,
  Cursor, GitHub Copilot and Codex CLI.
- Offer one shared setup/settings flow for model, effort, access and routing.
- Route all ordinary prompts in Passthrough, or only `/d` and `$d` in Direct.
- Preserve provider-native commands, owned sessions and working directories.
- Relay public plans and messages with consistent activation and attribution.
- Launch verified Windows npm ACPX installations directly through Node and
  reuse fresh activation metadata to reduce transport overhead.
- Include installation helpers, a portable package, 374 offline tests, and
  six-provider live validation of activation, both routing modes and shutdown.

Earlier development evidence remains under `checks/`; those reports describe
pre-release snapshots and their original test conditions.
