# Codex host validation plan (CLI-MODE 0.3.7)

A plan for validating CLI-MODE on the **Codex** host as a real user meets it, the way the Claude Code host is
validated with `claude_user_validation.py`. It is written so a Codex agent (or a person) can carry it out step by
step and report evidence.

## Ground rules

- **Be real.** Test the plugin *installed from GitHub* (`codex plugin marketplace add adamczhang/CLI-MODE --ref
  v0.3.7`), in the user's real Codex configuration, with real agents on their own subscriptions. Never test the
  source tree, a mock backend, or a copy of the plugin files.
- **Never `codex exec`.** It does not run plugin hooks, so CLI-MODE never activates there and every result would be
  wrong. Headless runs use `codex app-server` (JSON-RPC over stdio), the server Codex Desktop itself runs on; the
  harness below does this. The last part of the plan is done in the Codex Desktop window by a person.
- **Throwaway projects only.** Every run gets a new folder under `%TEMP%`; nothing touches a real project.
- **Real quota.** Each run spends Codex turns (the host) and the agent's own usage. Read Codex's rate limits before
  and after (the harness records them) and stop if a window is nearly used.
- **Evidence.** Keep the harness's evidence folder (`--keep`) and, for each manual step, the exact reply text or a
  screenshot. Report failures with the step id, the prompt, the reply, and what was expected.

## Preconditions

1. Codex CLI on PATH (`codex --version`, 0.155 or later), Codex Desktop signed in.
2. CLI-MODE 0.3.7 installed: `~/.codex/plugins/cache/cli-mode/cli-mode/0.3.7/` exists and `config.toml` pins
   `ref = "v0.3.7"`.
3. Hooks trusted: in Codex Desktop, **Plugins → CLI-MODE → Hooks** shows them trusted. (Headless check: the harness's
   first turn fails with a hook error otherwise.)
4. At least one Prompt-capable agent signed in (Grok Build is the cheapest to test; Claude, Copilot and Antigravity
   also work) and, for the several-agents steps, a second agent (Codex CLI works).
5. Quit Codex Desktop from the tray before headless runs if they misbehave: its own app-server can hold state.

## Part 1: the shared scenario, headless (automated)

The same scenario Claude Code is validated with: setup menus, settings, routing, emissions, native commands, queue
and resume, host turns, errors and closing, each turn checked (hooks ran from the installed copy, only the
installed controller was run, no errors or approval requests, views came back as `reference` lines).

```powershell
python checks/codex_user_validation.py --agent grok-build --depth full --extras gates format viewer agents tools --keep
```

Pass: the report's `problems` list is empty for every step. `gates` proves a thread without Full Access is refused;
`agents` runs two named agents at once; `tools` covers the change receipt, `/cli diff` and the copy box.

## Part 2: 0.3.6 and 0.3.7 features, headless (scripted)

Not in the shared scenario yet. Drive them through the same `Session` class the harness uses
(`from codex_user_validation import Session`): one thread in a throwaway git project, prompts sent with
`session.send(prompt, step_id)`, and files and state read back from the project and CLI-MODE's state. Use one
Prompt-capable agent (`/cli spawn gro`). Each row is one turn unless it says otherwise.

### A. Project brief (0.3.7)

| Id | Do | Expect |
|---|---|---|
| A1 | Send `We are building a garden-planner app. Reply OK.` (a host turn, gives context) | Codex answers itself; CLI-MODE does not route it |
| A2 | `/cli spawn gro` | Activation card. `Agent_Working_Folder/BRIEF.md` has `## From the host` with a dated `### ..., when Grok GRO-xx started` entry whose text is a real note about the garden planner (2 to 6 lines), **not** "(The host has not written this note yet ...)"; `## Agents running now` lists GRO-xx |
| A3 | `/d Reply with only the word ready.` | The task the agent received starts `you are Grok GRO-xx. First read ...BRIEF.md` (read the request's events or ask the agent to quote its first line); the agent list shows GRO-xx idle with a `last answer` path after the turn |
| A4 | `/cli brief-add Use metric units.` then `/cli brief` | The point is listed, with `Host notes: 1` and `Agents listed: Grok GRO-xx` |
| A5 | `/cli spawn cod` (second agent) | A second dated host entry is **added** (the first is kept); both agents listed |
| A6 | `/cli close cod` (by its name) | Codex CLI's line leaves the list; Grok's stays; both host notes stay |

### B. Approvals in the chat (0.3.6)

| Id | Do | Expect |
|---|---|---|
| B1 | `/cli access prompt` | Access shows `Prompt — you approve in chat` |
| B2 | `/d Create a file named hello.txt containing the word hi` | The relayed answer says `Grok GRO-xx asks to edit files: ...hello.txt` and offers `/cli approve`, `/cli approve always`, `/cli deny`; no file yet |
| B3 | `/cli approve` | A new turn runs; the file exists (in the project or the agent's folder) |
| B4 | `/d Run the shell command: git status` then `/cli deny` | Asks to run commands; after deny the agent answers without running it |
| B5 | `/d Create b.txt containing b`, `/cli approve always`, then `/d Create c.txt containing c` | c.txt is created with no question |
| B6 | `/d Run the shell command: echo hi` | Stops and asks again (only edits were approved always) |
| B7 | `/cli approve` twice | The second says `No agent is waiting for an approval.` |
| B8 | Queue: stop on a question (as B2), send another `/d` before answering, then `/cli approve` | The question was settled by the next turn: `No agent is waiting ...` |

### C. Attachments on /d (0.3.6)

The Codex desktop app puts attached files, pasted images too, in the prompt text as below. Send exactly this shape
(with a real file path) to reproduce it headlessly:

```text

# Files mentioned by the user:

## notes.txt: C:/Users/<you>/AppData/Local/Temp/<run>/notes.txt

## My request:
/d What code word is in the attached file? Reply with just the word.
```

| Id | Do | Expect |
|---|---|---|
| C1 | The prompt above, with notes.txt containing a unique code word | The agent answers the code word; a copy is in `Agent_Working_Folder/GRO-xx/attachments/notes.txt` |
| C2 | The same block with a request that is **not** a `/d` | Codex answers itself; nothing is copied |
| C3 | `/cli dir` | Lists `1 attached file in attachments/ ... notes.txt` |
| C4 | `/cli close` | `attachments/` is gone; `answers/` stays |

### D. Undo and the test gate (0.3.7 fixes)

Use a git project with `core.autocrlf=true`, a CRLF file committed, and a `package.json` or `tests/test_*.py` so a
test command is detected.

| Id | Do | Expect |
|---|---|---|
| D1 | `/cli access allow`, then `/d Change the second line of notes.txt to "changed"` | The answer ends with the change receipt and `✓ Tests passed` (or failed, with the log in the copy box) |
| D2 | `/cli undo` | `Undid Grok GRO-xx's last turn: restored notes.txt.`; the file is byte for byte the committed one (CRLF kept) and `git status` is clean |
| D3 | Two agents: while GRO-xx works on file a, COD-xx edits file b (by `/d` in the same folder); then `/cli undo gro-xx` | Only a is restored; the reply names b as left as it is |
| D4 | `/cli test` with a command that hangs (`python -c "import time; time.sleep(900)"`), after a turn that changes files | The gate gives up after its limit and the agent's queue keeps working (optional: slow, 10 minutes) |

## Part 3: in the Codex Desktop window (a person)

What only the real window shows. New task in a throwaway folder, Full Access.

| Id | Do | Expect |
|---|---|---|
| W1 | `/help`, `/cli`, pick an agent, activate | Views render as cards (not raw paths); the activation card names the agent |
| W2 | `/cli spawn gro` after a few messages of real conversation | Open `Agent_Working_Folder/BRIEF.md`: the host note summarises the conversation sensibly |
| W3 | Paste a screenshot **and** attach a file, type `/d What is in the picture and the file? One line each.` | The agent describes both (the pasted image reaches it as a file) |
| W4 | Approvals B2 and B3 in the window | The question reads clearly in the relayed view; approving continues the work |
| W5 | `/cli view on`, then a `/d` | The viewer window opens and follows the turn |
| W6 | `/cli close` | CLI-MODE is off; the next plain message goes to Codex |

## Reporting

For each step: id, pass or fail, and for a failure the prompt, the exact reply (or screenshot) and what was
expected. Add Codex's rate-limit use before and after, the agents used, and the evidence folders. File a failure as
a GitHub issue with the report attached, or hand it to the Claude Code session maintaining CLI-MODE.
