# Claude Code plugin performance audit — 2026-09-23

Source: `claude-port` at `de2ffc8` (installed copy, fingerprint `6578275bb2890e46`) against the changes
described below. Windows 11, Python 3.14.7, Claude Code 2.1.280. This covers what CLI-MODE's own code
adds at each point of the Claude Code experience. It does not cover Claude's model time or the agents'
generation time, except where the plugin decides how often Claude has to act.

## Method

- **`checks/claude_performance_audit.py`** runs the hook and the controller exactly as Claude Code does:
  a new `python` process with the event on stdin. By default it measures the installed plugin, in a
  throwaway data folder and project with a fake agent, with no queue worker and no model. Each
  touchpoint is the median of 15 runs. `--agents` adds each agent's installation scan and usage lookup,
  which run the agent's CLI locally and never prompt it.
- **Relay turns** were measured from the stream events of P7b run 3 (35 turns, real Claude and Codex):
  - the time from each tool call to its result;
  - Claude's time between results and its next action;
  - `time_to_request_ms`, which includes the prompt hook.

## Global: every Claude Code session, CLI-MODE in use or not

Claude Code starts the hook for every session start, prompt, `python` command, subagent launch and turn
end in every session. Before this change, each start or prompt loaded the whole routing stack and wrote
a state file, even in sessions that never used CLI-MODE, which left a file behind for every session.

`hooks/claude.py` now starts with `nothing_to_do()`. It decides, before anything heavy loads, the events
CLI-MODE has no part in:
- a `python` command that isn't CLI-MODE's controller;
- a session start that isn't a compaction;
- an ordinary prompt, or a subagent or turn end, in a session where CLI-MODE is unused or inactive with
  nothing open.

It only ever answers "nothing to do". Anything else goes to the unchanged full hook, and a test checks
108 state and event combinations to confirm the two always agree. SHA-256 comes from Python's built-in
`_sha2`, because `hashlib` first loads OpenSSL (about 6 ms).

Back-to-back medians (ms). Python startup alone is about 30:

| Touchpoint | Before | After | Change |
|---|---:|---:|---:|
| Session start | 91.7 | 34.6 | −62% |
| Ordinary prompt, CLI-MODE never used | 105.1 | 46.1 | −56% |
| Ordinary prompt, CLI-MODE used earlier | 109.9 | 45.7 | −58% |
| Unrelated `python` command | 59.6 | 34.1 | −43% |
| Agent tool | 82.2 | 45.8 | −44% |
| Turn end | 59.3 | 46.1 | −22% |

Sessions that never use CLI-MODE no longer get a state file.

## CLI-MODE: where relay turns spend their time

Process costs are small and unchanged: menus take 125–135 ms, capturing a `/d` 110 ms, approving a
relay command 59 ms, and one relay call as a process about 108 ms, of which about 85 ms is imports.
Claude Code adds about 250 ms of shell start per tool call.

The seconds are in Claude's round trips. Every relay update costs Claude a model turn of about 3–3.5 s
(posting it, then issuing the next call). In run 3, turn 13 (a coding task):
- the turn made 6 relay calls in 28.8 s;
- 3 of the 6 updates were only `_Codex work: 3 done_`;
- turn 15 spent 2.9 s on a call that returned nothing.

Two changes, on Claude Code only (the Codex path is unchanged and a test checks it):
- **No empty second call.** The first call returns at once to show "Passing to" and returns cursor 0,
  so the next call also started at 0 and returned immediately, often with nothing. The immediate
  return now happens only while the "Passing to" line is still to be shown.
- **Progress alone waits for the full wait.** An update that is only activity or a plan waits the full
  8 s. The agent's words, artifacts and errors still return as soon as they're ready.

Replaying turn 13's timings under these rules predicted 4 calls instead of 6, with the final message at
about the same time. P7b run 5, on the installed `987bf3c`, passed 35/35 and confirmed it:

| Relay turn | Runs 3 and 4: calls; Claude's time between calls; total | Run 5 |
|---|---|---|
| 13, a coding task | 6 calls; 7.2–8.1 s; 28.4–28.8 s | 4 calls; 4.7 s; 28.0 s |
| 15, rerunning the tests | 2–3 calls; 2.1–4.3 s; 10.0–12.6 s | 2 calls; 2.1 s; 12.2 s |
| 25, earlier plus queued request | 2–3 calls; 3.3–3.8 s; 13.8–14.4 s | 1 call; 2.4 s; 10.3 s |

In a long task the agent's own work sets the finishing time. What falls is the number of Claude turns
spent on the way. The prompt hook's time inside Claude Code's `time_to_request_ms` stayed at
267–280 ms in all three runs, because every harness turn is a CLI-MODE turn, which still takes the full
hook. The global saving applies to sessions and prompts outside CLI-MODE, which the audit tool
measures.

## CLI-specific

Measured with `--agents` (ms; these steps never prompt a model):

| Agent | Installation scan | Usage lookup before | Usage lookup after |
|---|---:|---:|---:|
| Antigravity | 1,387 | 40,133 (then "unavailable") | 15,127 (then "unavailable") |
| Claude Code | 1,645 | 2,378–6,027 | unchanged |
| Codex, Copilot, Cursor, Grok Build | 1,527–3,445 | none (no account quota) | none |

- **Antigravity's `/usage` hangs.** `agy -p /usage` (AGY 1.2.9) never answers, in a new folder or a
  project, with stdin closed, and even past its own `--print-timeout`. The helper waited out its 40 s
  timeout. It is now bounded at 15 s; the last successful lookup, measured 2026-09-22, took 5.9 s.
- **Activating from menus and changing settings now overlap the usage lookup.** `bind` and `activate`
  already ran the lookup alongside activation, but `choose` (the menu path) and `tune --apply`
  (`/cli model`, `/cli effort`, `/cli access`) ran it only after the agent was ready. That cost up to
  40 s for Antigravity and about 6 s for the Claude agent. `activate()` now starts it, after its checks
  pass and with the exact settings, whenever that process will show the activation card.

## Not changed, and why

- **Controller imports, about 85 ms per relay call** (the agent adapters take 24 ms, argparse 11 ms).
  This is shared with Codex and small next to a round trip of about 3 s.
- **The installation scan (1.4–3.4 s)** runs `setup.ps1 Scan` in PowerShell. It runs once per agent
  (first-time check or R).
- **`/cli stop` closes the agent's session inside the prompt hook**, about 0.5 s (`time_to_request_ms`
  of 740–760 ms against about 250 ms). Its confirmation depends on the close finishing.
- **The fresh-bind sequence.** It was measured per step with live agents (one readiness prompt each,
  isolated state):

  | Agent | Bind | `sessions ensure` | Readiness prompt | All settings | 2 metadata reads |
  |---|---:|---:|---:|---:|---:|
  | Antigravity | 42.4 s | 18.9 s | about 19.6 s | 3.2 s | 0.7 s |
  | Copilot | 17.0 s | 5.3 s | about 10.6 s | 0.4 s | 0.7 s |
  | Grok Build | 16.0 s | 1.8 s | about 12.8 s | 0.45 s | 1.0 s |
  | Codex | 13.0 s | 3.5 s | about 8.4 s | 0.4 s | 0.7 s |

  Settings already go to the running owner over one connection, so recommendation 3 of the 2026-09-22
  analysis is in place. The remaining cost is starting the agent twice. `sessions ensure` starts it to
  create the session, then closes it (ACPX `createSession` calls `client.close()`). The readiness prompt
  starts it again as the long-lived owner. ACPX 0.18.0 has no call that creates a session inside an
  owner, and a prompt refuses a missing named session, so a fresh bind keeps both starts.

## Changing a setting on an active agent

`/cli model`, `/cli effort`, `/cli access` and the settings menu re-activate the same session. That
path always sent a readiness prompt first, only to wake the shared owner, even when the owner was
running: one full provider turn per change. The settings now go straight to the owner. With no owner
running, ACPX 0.18.0 refuses an owner-only control *before sending it*. The bridge reports that
refusal as `{accepted: false, ownerRunning: false}`, which leaves nothing uncertain, and only then does
a readiness prompt wake the owner (strict resume, as before) and the settings get applied. On the
prompt-free path, the conversation identity is checked after the settings, as the wake prompt did.

Measured live, with the same settings re-applied on a running session:

| Agent | Setting change now | Before (readiness prompt plus settings) |
|---|---:|---:|
| Grok Build | 0.44 s, 0 prompts | about 13 s |
| Copilot | 0.46 s, 0 prompts | about 11 s |
| Codex | 0.52 s, 0 prompts | about 9 s |

Tests against the real pinned ACPX and an offline agent confirm both paths. A running owner gets zero
prompts. An idled-out owner gets exactly one wake prompt, then every setting, with no uncertain work
left behind. Antigravity could not be measured: in two attempts `agy` hung, on `sessions ensure` and
then on `set model`, each until the 60 s timeout.

## Leaked Antigravity processes

19 `agy_acp_server.exe` processes were still running, from 2026-09-21 on, all with no live parent. With
their 18 `localharness_external.exe` children they held about 1.95 GB. They started in pairs about 18 s
apart, matching the two agent starts of each Antigravity activation. With the user's OK, all 19 were
ended.

**Diagnosis.** A `sessions ensure` run (which sends no prompt) showed this chain:

```
node launch.mjs → agy_acp_server.exe → agy_acp_server.exe (inner) → localharness_external.exe
```

ACPX's cleanup ended the launcher and the outer server. The inner server and its harness survived: the
outer server starts them and never stops them, and Windows does not end children along with their
parent. That is one pair per agent start. The bug is in Antigravity ACP 1.1.1's server. CLI-MODE's job
object doesn't catch it, because the job allows silent breakaway on purpose (the ACPX owner must
outlive the command), so the agent's processes are never inside it.

**Workaround.** The launcher joins a Windows job object with `KILL_ON_JOB_CLOSE` and no breakaway
*before* it starts the server. Everything the server starts is then born inside the job, and when the
launcher exits (cleanly, killed by ACPX, or crashing), Windows ends the whole tree.
- **`acp-login.py`, CLI-MODE's managed launcher.** The job is used for serving only. Sign-in
  (`--login`) may open the user's browser, which must not be closed when setup ends; sign-in is a
  one-off step that can still leave one pair.
- **The user's custom launcher.** `Tools\AntigravityACP\1.1.1\launch.py` does what its `launch.mjs`
  does, plus the job (Python at 15 MB instead of Node at 32 MB). `~/.acpx/config.json` now points at
  it; the backup is `config.json.before-launch-py-20260923-155506.bak`.

**Verified, all without a prompt:**

| Check | Before | After |
|---|---|---|
| Launcher force-killed after ACP `initialize` | 1 process left (`launch.mjs`), 2 left (`acp-login.py`) | 0 |
| Launcher's input closed | 1 left (`launch.mjs`) | 0 (`launch.py`, `acp-login.py`) |
| ACPX `sessions ensure` through the switched global config | the inner server and harness left | 0 left; session created in 18.1 s |

`checks/test_installer.py` has a test that reproduces the pattern without Antigravity: a child that
starts its own child and exits. The orphan survives without the job and is ended with it.
- **Chat display**, the default chosen by the user, costs one model turn per menu (about 4–6 s).
  Instant display costs nothing and takes about 2 s in total.

## Reproduction

```powershell
python checks/claude_performance_audit.py --agents                      # installed plugin
python checks/claude_performance_audit.py --plugin plugins/cli-mode     # this tree
```
