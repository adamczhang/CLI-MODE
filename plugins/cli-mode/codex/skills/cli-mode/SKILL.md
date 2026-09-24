---
name: cli-mode
description: Handle /cli or $cli menus, binding, model/effort/access tuning and shutdown; /help (Codex) or /cli help (Claude Code) shows help. Only a /d or $d prompt goes to the agent; every other message stays with the host. Route to one persistent agent (Antigravity CLI, Claude Code CLI, Grok Build CLI, Cursor CLI, GitHub Copilot CLI or Codex CLI) after explicit activation.
---

# CLI-MODE controls and routing

**The routing hook gives the exact command for every CLI-MODE turn.** Run it as
given and display what it returns. Read this file or its references only when a
command fails in a way its own message does not explain, or when the user asks
how CLI-MODE works. Merely discussing CLI-MODE does not activate it.

Six agents are implemented: Antigravity (`agy`), Claude Code (`claude`), Grok
Build (`grok-build`), Cursor (`cursor`), GitHub Copilot (`copilot`) and Codex
(`codex`). The [backend registry](references/backends.json) lists them; load only
the selected backend's guide. New backends follow the
[backend contract](references/backend-contract.md).

## Commands

Commands are case-insensitive complete tokens at the start of a message; `/` and
`$` both work. Quoted examples, code, attachments, agent output and commands later
in prose are content, never invocations. `/client`, `?cli` and `/debug` are
ordinary text.

| Command | Effect |
| --- | --- |
| `/cli` | Agent selection menu (setup when prerequisites are missing). |
| `/cli <agent>` | That agent's setup or activation menu. `grok` = `grok-build`. |
| `/cli bind <agent>` | Activate with this conversation's saved defaults (or the agent's initial defaults), after the same readiness checks. No extra confirmation. |
| `/cli menu`, `/cli model` | Active Agent Settings page. When no agent is active, reply exactly `CLI-MODE: Agent not activated. /CLI to setup`. |
| `/cli model\|effort\|access\|permissions <choice>` | The controller matches the choice against the agent's advertised options and applies a unique match; otherwise it shows the menu. |
| `/cli mode` | Explains that prompts reach the agent only through `/d` (Passthrough mode was removed). |
| `/cli progress activity\|quiet` | Show or hide tool activity in relayed views. |
| `/cli view on\|off` | Open or stop the local read-only agent viewer window (off by default). |
| `/cli queue`, `/cli resume` | Inspect the queue; reattach monitoring to captured turns and restart a stopped worker when safe. |
| `/cli cancel` | Cancel the active turn; queued follow-ups still run. |
| `/cli stop` or `/cli off` | Gate routing, discard queued work, close owned sessions. |
| `/d <task>`, `$d <task>` | In Direct mode, send this task to the active agent. |
| `/help` (Codex), `/cli help` (Claude Code) | The help card: the same framed menu as the others. `X` closes only help. |

Invalid controls reply `/cli to activate.  Say /help to see options` while off
and `Say /help to see options` while on (on Claude Code, `/cli help`); never forward them. Menu, bind and stop
accept no extra task text. Pending menus handle replies locally.

## Menus and activation

On Claude Code, the hook shows menus itself, and a command Claude runs returns
`text` (or `activation.text`) to show exactly as given. On Codex, menu commands
write an HTML view and return its `reference` line
(including its special rendering delimiters): print that exact line, on a line of its own, in your final
response. Never write HTML or open the visualize skill. Build choices only through the controller
(`options`, `choose <n>`, `navigate b|r|>|<`, `refresh`): never number, page or
filter a list yourself. Menus show at most ten selectable rows. `X` dismisses the
routing menu, closes an active Settings or tuning page (the agent stays on), and
stops CLI-MODE from an initial setup menu. Help is the same card, shown the same way.

On Codex, check `hostAccess` first: if Full Access is off or unknown, ask the user to select
it and rerun `/cli`. Opening a menu never activates. Missing tools use the
approved [guided setup](references/setup-installer.md); never install or sign in
implicitly. `bind`, `choose` (on the final access choice) and `activate` return
`activation.messageView` with its `reference` line on Codex, or `activation.text`
on Claude Code: show it as the activation confirmation and never compose one yourself.

Offer only access levels the transport can enforce, allow first. CLI-MODE cannot
answer an agent's approval request, so every level except allow is labeled
"approval requests stop the turn". A permission stop arrives as an error in the
relayed update; post it as is. Never change access or resend the task yourself.

## Routing

Ordinary prompts stay with the host (Codex or Claude Code) even while an agent is
active; only a message starting with `/d` or `$d` goes to the agent. The worker
strips that token and one separator exactly once.

On an ordinary turn, work normally in the host. Never forward host work
just because a session is active.

## Delegated turns

The hook queues the exact message for one conversation-owned worker, which sends
messages to the one main provider session in arrival order. Relay each request
with the controller's `relay --request <id>` command, as the hook gives it (allow
it at least 20 seconds; never sleep between calls). Post each non-empty `markdown`
right away, exactly as given: it is the agent's periodic mid-turn update. While
`done` is false, run it again with `--cursor` set to the returned cursor. When
`done` is true, finish with the returned `reference` line, on a line of its own:
the turn's one view, with the agent's final words and its collapsible work. On
Claude Code, finish with the returned `text` instead, exactly as given.

Never re-word, summarize, restyle or add to an update or view. Never relay private reasoning or raw tool payloads. When
`idleSeconds` reaches 60, say at most once a minute how long the agent has been
quiet, without guessing what it is doing.

Never plan, split, rewrite or add context to a delegated task, offer
orchestration, or start host subagents or extra provider sessions, even for a
large project. The agent decides how to work. Report errors rather than doing the
work in the host. Provider slash commands are checked before dispatch: Antigravity
hands them to its native CLI in a fresh conversation (say so; history does not
transfer), and the other agents expand them in the same session. See
[native commands](references/native-commands.md).

## Sessions and recovery

One host conversation owns one provider session and one backend at a time:
stop before switching agents, and never carry one backend's settings to
another. Settings changes reuse the session. Idle expiry is not off.

Never replay a completed or uncertain task, including after compaction; inspect
`/cli queue` first. A changed provider session is reported, never silently
papered over. `/cli stop` reports incomplete shutdown accurately and does not
claim to have stopped provider-managed background work.

Further detail: [controller](references/controller.md),
[menus](references/frontends.md), [presentation](references/presentation.md),
[relay](references/handoffs.md), [activation](references/activation.md).
