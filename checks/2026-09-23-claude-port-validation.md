# Claude Code port validation — 2026-09-23

Branch `claude-port`, from `60d730a`. Windows 11, Claude Code 2.1.278, Codex 26.917,
Python 3.14, ACPX 0.18.0. This record covers P1–P6 of the port plan.

## Codex is unchanged

- `checks/test_codex_golden.py` replays 138 steps across 14 scenarios, covering all
  29 hook route kinds including compaction restores. Every hook response, controller
  result and rendered view matches the record taken at `60d730a`.
- The Codex zip contains `60d730a`'s 83 files plus `scripts/host.py`
  (`checks/fixtures/codex-package-files.txt`). Its SHA256 was identical across P4 and
  P5, and `SKILL.md`'s wording now covers both hosts.
- `checks/codex_install_smoke.py`, in a throwaway `CODEX_HOME`:
  - Codex read `.agents/plugins/marketplace.json`, not the new
    `.claude-plugin/marketplace.json`, and installed byte-identical Codex hooks.
  - The Claude files were copied along and went unused.
  - The installed copy passed `installed_plugin_smoke.py` for all six agents.
- `checks/package_smoke.py` ran the full suite against a fresh extraction of the
  Codex zip: 551 tests passed. Its 32 skips are exactly the Claude-only tests, whose
  files the Codex installer correctly leaves out.

## Claude Code installs and answers locally

`checks/claude_install_smoke.py`, in a throwaway `CLAUDE_CONFIG_DIR`, from the
repository root and from the Claude zip (`install-claude.ps1`, then again as an
upgrade):

- Claude Code registered exactly CLI-MODE's six Claude hook handlers. This includes
  the repository channel, whose folder still holds Codex's `hooks/hooks.json`, so the
  `strict: false` marketplace entry keeps Codex's hooks out.
- `/cli help`, `/cli-mode:cli help`, `/cli`, `$cli queue`, `/cli mode` and
  `/cli reset` were answered by the hook at 0 turns and $0.

Hook cost per event, measured as fresh processes:
- about 70 ms for an unrelated `python` command or a turn end;
- about 110 ms for an idle prompt;
- 130–150 ms for an instant `/cli` menu.

## Real agents on Claude Code's text path

`live_parity_probe.py --host claude-code` drives the controller with Claude's text
rules and real agents. No Claude model is involved.

| Agent | Bind | Turn | Final message verbatim | HTML written | Largest result | Provider command | Stop |
| --- | ---: | ---: | --- | --- | ---: | --- | --- |
| GitHub Copilot | 16.6 s | 6.4 s | yes | no | 343 | `/context` 1.5 s | clean |
| Antigravity | 51.3 s | 11.5 s | yes | no | 559 | native handoff, skipped | clean |
| Grok Build | 18.5 s | 6.5 s | yes | no | 258 | `/context` 1.9 s | clean |
| Codex CLI | 27.8 s | 10.5 s | yes | no | 337 | `/status` 2.0 s | clean |

All four refused an unknown command and `/model x`, pointing to `/cli help` and `/cli model`.

## Claude itself relaying

`checks/claude_live_relay.py` runs `claude -p` with the unpacked dev build, using the
user's own sign-in. It runs bind, then a `/d` file task, then stop.

- **Copilot and Antigravity pass.**
  - Bind took 2 turns (the exact command, auto-approved, with no permission denials),
    and the confirmation was shown alone.
  - Two or three relay calls posted the agent's final message verbatim.
  - `/cli stop` was answered by the hook at $0.
  - Each agent cost about $0.15.
- **The Claude Code agent's bind worked** under the Claude host. Its relay turn, and
  the Grok Build and Codex CLI runs, then hit the account's 5-hour session limit. The
  Claude agent and a Claude host share one plan's quota.

The first live runs found three issues, all fixed and re-verified:
1. The `/cli` command body said the hook had not run, contradicting the hook's context.
2. Claude opened the Codex-worded skill before binding, then said prompts "stay with
   Codex". `SKILL.md` now covers both hosts, and the hook's context states it is
   complete and needs no skill.
3. An output style added commentary after the confirmation. The contexts now state
   that results are shown alone, whatever the output style.

## Real-world use (P7)

These checks use the user's real Claude Code configuration with CLI-MODE installed
by `install-claude.ps1`. They run under the desktop app's own bundled Claude Code
(`%APPDATA%\Claude\claude-code\2.1.280\claude.exe`), which is launched in
`bypassPermissions` and loads user plugins from settings. `checks/claude_session_script.py`
drove one session through a first-time user's path:

- **Menus, all answered by the hook at $0 in 2–5 s:**
  - help, closed with X;
  - the new-user home menu;
  - choosing GitHub Copilot, which ran the first-time check ("ACPX and GitHub Copilot
    CLI are installed") and opened the activation page;
  - change defaults, then back;
  - settings, routing mode and the queue;
  - stop.
- **After help, a question reached Claude** ("What is 2+2?" returned "4"). Earlier,
  help held the next message and answered "Help is open"; that was the problem the user
  saw in the desktop app.
- **Activation from the menu (1 = Yes):** Claude ran one exact `activate` command
  (180 s timeout, auto-approved) and showed only the activation card. 22.6 s.
- **`/d` relay:** the transcript shows the passing line, then Copilot's words verbatim,
  with nothing posted twice. In passthrough mode, a plain prompt was delegated and relayed.
- **PowerShell tool:** told to use PowerShell, Claude ran `bind` and `relay` through it
  with no permission denials.
- **Attached image:** the prompt hook's input has only the text (`prompt`) and no
  attachment field. Claude now says "the attached image was not forwarded" before
  relaying, and Copilot confirmed it received only the text.

Fixed during P7:
- **Stale cache on reinstall.** `plugin update` keeps the cached copy for an unchanged
  version. The installer now reinstalls with `--keep-data`, which a throwaway
  configuration confirmed preserves saved state.
- **Plugin named "0.1.16" in the desktop app.** A plugin without `plugin.json` is named
  after its versioned cache folder. The Claude zip is now a standard plugin with
  `plugin.json` (it has no Codex hooks file), and the desktop lists `cli-mode:cli-mode`
  again. A `plugin.json` cannot coexist with the repository channel's `strict: false`
  entry; Claude Code rejects it as a conflict.
- **Help no longer holds the next message** (see above).
- **Replies now appear as chat messages by default.** The desktop app shows an instant
  hook reply as a "blocked by hook" warning, which read as an error. `/cli display
  instant` restores free, immediate replies, and the choice is saved for every session.
- **Relays note that attachments are not forwarded** (see above).

`checks/claude_install_smoke.py --claude <binary>` passes for both channels on Claude
Code 2.1.278 and on the desktop's 2.1.280, answering seven prompts at $0.

## Installed-plugin user validation (P7b)

`checks/claude_user_validation.py --agent codex` runs 35 turns against the **installed**
plugin, using the desktop app's own Claude Code (2.1.280) and the real configuration.
- **Setup:** it refuses to start unless the build, the installed marketplace folder and
  Claude Code's cache have the same fingerprint.
- **Checks on every turn:**
  - the hook and every controller command ran from the installed folder;
  - there were no permission denials, subagents or other commands;
  - the agent's final words were posted verbatim;
  - "Passing to" was posted no more often than requests were relayed.
- **Losing control:** simulated by ending Claude Code mid-relay (`taskkill /T`), twice.
- **Scenarios:** menus, changing models, emissions and attribution (bold, since Claude
  Code has no custom colours), chat versus instant display, queueing, resuming, a host
  turn, errors and closing.

Run 1 found three problems, and run 2 found two more; all are fixed:
- **Choosing a "Setup needed" agent** from a returning user's agent list showed the list
  again instead of that agent's page. This code is shared with Codex; the golden record
  is unchanged.
- **The agent's answer could arrive as a mid-turn update that Claude skipped**, leaving
  only "work: 6 done". The Claude relay now holds the agent's latest words for the final
  `text`, which Claude always posts, including when the log's `done` event is written
  before the receipt settles.
- **Output of an interrupted relay was lost.** A new `/d` or `/cli resume` now also
  relays earlier requests whose relay never finished.
- **Given one relay command per request, Claude posted only the last one's final
  text** (run 2, turn 25: the earlier answer was lost for good). `relay` now takes
  `--request` more than once on Claude Code:
  - the requests form one stream with one cursor;
  - every request's final words arrive in the one final `text`, oldest first, each
    under its own heading;
  - the Stop guard and compaction continue the same command.
- **"Passing to Codex..." was posted twice** in most relay turns. It now comes once per
  request.

Run 3 passed 35/35 with no problems, using about 1% of the plan's 5-hour window. In
turn 25, one command covered both requests, and the final message showed the
interrupted request's answer and then "queued".

Two observations from run 3 are fixed with unit tests; no live run has checked them yet:
- **A new request's "Passing to" line was posted alone** while an earlier answer was
  held for the end, so it landed between that request's opening line and its answer.
  It now waits and appears directly above the request's own first output.
- **Claude reworded a failed bind's error and added advice of its own.** Hook contexts
  now state that an `error` is shown as `CLI-MODE: <error>` with nothing added, and the
  harness checks it.

## Not covered

- Cursor: the account plan still blocks it.
- The Stop-hook relay guard is covered by unit tests only; no live run ended early.
- The last two P7b fixes (the "Passing to" line placement and exact bind errors) have not
  had a live run.
- The desktop app's visual rendering of chat-mode menus, which needs a new session.
