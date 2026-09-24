# Changelog

## 0.3.0 — A fresh start — 2026-09-24

The repository restarts its history at this release: one snapshot of the Codex plugin and its Claude Code port. The entries below summarise the earlier releases.

- `/cli view on|off` (off by default): a read-only PowerShell window (PowerShell 7 when installed) that shows each agent turn live, with the agent's text, tool activity, plans and the result in colour; Markdown tables are drawn and agent escape sequences stripped.
- Ask before installing an agent or activating it with wider access than Prompt (Claude Code shows its own permission prompt for these commands).
- Stopping setup cancels a running installer; the next menu appears when a Claude Code install completes.
- Report a busy or failed control as itself rather than as unreadable state, and show why a request was not sent.
- After a compaction, a cancel or an off is not repeated, and answers already relayed are not relayed again.
- Read the hook's input as UTF-8 on every Windows code page; read the state file once per prompt; run the activation usage lookup on a daemon thread.
- Claude agent: the Opus 5.5 default uses its canonical model ID, stale model caches migrate, Haiku works without an effort selector, and usage scopes have readable labels.
- Codex views: final answers render headings, nested lists, tables and code blocks; activation uses responsive rows; work groups show workspace-relative paths and safe command labels; "Passing to …" appears once per request.. It follows the public events file each turn already writes, so it never reaches the agent; closing it is safe, and the next turn reopens it while the viewer is on. Both hosts.

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
