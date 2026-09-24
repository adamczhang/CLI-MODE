# CLI-MODE — agent guide (read this first)

> **Written for CLI-MODE 0.3.1** (tag `v0.3.1`, 2026-09-24).
> If `plugins/cli-mode/.codex-plugin/plugin.json` shows a different version, parts of this file may be
> out of date. Verify any file, function or rule named here against the code before relying on it; when
> they disagree, the code wins. Fix this file in the same change. `checks/test_agent_docs.py` fails
> until this line matches the plugin version and every path named here exists.

## Where the project stands

The repository's history starts at 0.3.0, a single snapshot of the Codex plugin plus its Claude Code port
(the 0.1.x and 0.2.x changes are summarised in `CHANGELOG.md`). Key facts:

- **Two hosts, one plugin folder.** `plugins/cli-mode` serves Codex and Claude Code. Most code is shared;
  each host has a thin layer of its own.
- **Codex's behaviour is pinned by a golden record** (`checks/fixtures/codex-golden.json`, 138 steps),
  recorded before the port and re-recorded only for intended Codex changes (the Agent Settings "Done" row,
  the skill path, Claude's default model and the final relay HTML).
- **Shared pieces added by the port:** `scripts/host.py` (the host switch), `route.decide()` (split out of
  the Codex hook) and text versions of menus and confirmations for a host without inline views.
- **Codex's skill lives under `plugins/cli-mode/codex/skills/`** (the manifest's `skills` path), so Claude
  Code never lists it; `/cli shortcuts` gives Claude Code GitHub installs bare `/cli` and `/d`.
- **Test Codex behaviour in a throwaway `CODEX_HOME`**, as `checks/codex_install_smoke.py` does; install
  into a real Codex configuration only when asked.

## Architecture

```
                    Codex                                     Claude Code
  prompt ─► hooks/hooks.json ─► hooks/route.py      prompt ─► claude/hooks.json ─► hooks/claude.py
                    │  decide()  (shared)  ◄──────────────────────────┘  (fast path: nothing_to_do)
                    │  records turnRoute, captures /d or passthrough text, starts the worker
                    ▼                                                  ▼
          codex_output(): additionalContext          instant reply (controller run in process), or
          naming exact controller commands           context naming the exact controller command
                    ▼                                                  ▼
   the model runs scripts/controller.py  ─────  one controller, shared: menus, binding, settings, relay
                    ▼
   queue_worker.py (detached FIFO worker) ─► dispatch.py ─► acpx-runtime.mjs ─► ACPX 0.18.0 ─► agent
                    ▲
   public event log (JSONL per request) ◄── observe / relay read it; no prompt is ever resent
```

- **Routing** (shared): `state.route()` turns a prompt into a route: `/cli …` controls, `/d` (Direct),
  passthrough text, help, setup replies. `route.decide()` records it in the conversation state and, for
  `direct` or `delegate`, captures the exact text as a request and ensures the worker runs.
- **Codex reply path** (Codex only): `route.codex_output()` returns additionalContext telling the model
  which controller commands to run. Menus and results come back as inline HTML views (`menuView` or
  `messageView` with a `reference` line; `menu_view.py`, `relay_view.render`). A relay loops
  `controller.py relay --request <id> --cursor N`; `QueueMixin.relay()` returns mid-turn Markdown updates,
  then one final view.
- **Claude Code reply path** (Claude only), in `hooks/claude.py`:
  - Local controls (menus, help, queue, stop) are answered in the hook itself, as a chat reply
    (default) or an instant hook notice.
  - Slow controls and agent turns get a context naming the exact command.
  - PreToolUse auto-approves only CLI-MODE's own controller commands; installing (`setup-start --approved`) and
    activating with wider access (`activate --access` other than `prompt`) still get Claude Code's permission prompt.
  - A `/d` turn posts "Passing to …", runs `controller.py follow --request <id>` and ends. PreToolUse
    approves `follow` with `updatedInput` that adds `run_in_background: true` and a row label
    (`<Agent> · <first 30 characters of the prompt>`), and denies a second follow of the same request.
    `QueueMixin.follow()` prints one line per step (never the agent's words) and exits when the request
    settles (0 if completed). Its end wakes Claude, which runs the relay once.
  - The relay is `relay_chain()` over `relay_text()`. It prints plain text (`presentation.relay_plain`),
    posts nothing mid-turn, and ends the turn with the agent's whole output. Requests already shown are
    skipped, because background turns overlap. With `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` there is no
    follow, and the relay waits up to 25 s per call, as before.
  - A Stop hook lets a turn end while its follow runs (from Stop's `background_tasks`, or the follow's
    process file), and otherwise gives the next follow or relay command, up to 3 times.
  - `/cli reset` sets aside unreadable state.
- **Host differences inside shared files** branch only through `scripts/host.py`: `host.claude()`,
  `host.views()`, `data_root`, `workspace`, `chat_color`. The shared files with branches are controller,
  state, frontends, menus, help_view, confirmation, native_commands and queue_worker. The Codex default
  path keeps the pre-port routing; `QueueMixin.relay()` adds only per-request "Passing to" tracking and
  workspace-relative activity labels.

## Code map

**Shared core (a change here changes Codex):**
- `scripts/controller.py`: CLI entry and `run()`. It combines `menus.py` (setup and Agent Settings),
  `binding.py` (owned sessions), `dispatch.py` and `queue_worker.py`.
- `scripts/state.py`: `Store` and `route()`.
- `hooks/route.py:decide()` and `task_through_settings()` (a `/d` task typed into open settings is sent;
  both hooks call it).
- `frontends.py`, `presentation.py` (menu frame and labels), `help_view.py`, `confirmation.py`,
  `relay_view.py` (`markdown`, `render`), `menu_view.py`.
- `adapters.py`, the per-agent modules (`agy.py`, `claude_code.py`, `codex_cli.py`, `copilot_cli.py`,
  `cursor_agent.py`, `grok_build.py`), `backends/*`, `catalogs.py`, `native_commands.py`.
- Runtime and setup: `acpx.py`, `acpx-runtime.mjs`, `processes.py`, `setup.ps1`, `installer.py`,
  `doctor.py`, `acp-login.py`.
- Agent viewer (`/cli view on|off`): `viewer.py` opens `viewer.ps1`, a read-only PowerShell window that
  follows each turn's public events file.

**Codex only:**
- `hooks/hooks.json`, `hooks/route.py:codex_output()` and `activation_reply()`. Codex 0.155 runs the
  Windows hook command through PowerShell and earlier builds through `cmd.exe /C "<command>"`, so
  `commandWindows` must parse the same in both (`package_plugin.WINDOWS_HOOK`, checked by `test_features`);
- `codex/skills/cli-mode/**` (`SKILL.md`, references, `codex/skills/cli-mode/agents/openai.yaml`). It sits
  under `codex/` (the manifest's `"skills": "./codex/skills/"`) so Claude Code, which scans a plugin's
  root `skills/`, never lists it. Exception: `codex/skills/cli-mode/references/backends.json` is the agent
  registry that BOTH hosts load at runtime (`state.REGISTRY`), so it ships in both zips;
- `QueueMixin.relay()`;
- `.codex-plugin/plugin.json`, the version source of truth;
- `.agents/plugins/marketplace.json`.

**Claude Code only:**
- `hooks/claude.py`, `claude/hooks.json`, `claude/commands/{cli,d}.md`;
- `QueueMixin.relay_text()`, `relay_chain()` and `follow()`, with `operations.follow_path`/`following`;
- `presentation.strong`/`plain_strong`/`chat_menu`/`relay_plain`, `relay_view.final_markdown`;
- the repo-root `.claude-plugin/marketplace.json`, kept in sync by `package_plugin.py --sync`;
- `scripts/install-claude.ps1`; `scripts/claude_shortcuts.py` writes bare `/cli` and `/d` into
  `~/.claude/commands` for the installer and for `/cli shortcuts` (a GitHub install).

**Packaging:** `scripts/package_plugin.py` builds `dist/cli-mode-codex-<v>.zip` (without `CLAUDE_ONLY`:
`claude/`, `hooks/claude.py`, `scripts/claude_shortcuts.py`) and `dist/cli-mode-claude-<v>.zip` (without
`CODEX_ONLY`:
`hooks/hooks.json`, `SKILL.md`, `openai.yaml`).

## How the whole project is protected

| Guard | Protects | Fails when |
|---|---|---|
| `checks/test_codex_golden.py` with `checks/fixtures/codex-golden.json` | every hook response and controller result Codex receives | anything Codex sees changes |
| `checks/test_claude_package.py` with `checks/fixtures/codex-package-files.txt` | the Codex zip's file list, the Claude zip, the root marketplace sync, Claude's hook rules | a file lands in the wrong package, or the manifests drift |
| `checks/test_host.py`, `checks/test_claude_hook.py` | Claude routing, relay, menus, colour, the Stop guard, the fast path | a shared or Codex change breaks Claude Code |
| `checks/codex_install_smoke.py`, `checks/claude_install_smoke.py` | real installs in a throwaway `CODEX_HOME` or `CLAUDE_CONFIG_DIR`, at no cost | a manifest, hook or installer breaks |
| `checks/claude_user_validation.py`, `claude_stop_guard_live.py`, `live_parity_probe.py`, `five_cli_*` | live behaviour with real accounts | real use regresses. **They spend the user's quota: ask before running** |

**Rules by zone:**
- **Codex-only change:** `test_host.py` and `test_claude_hook.py` must pass untouched. If the golden
  record moves, that is expected only for the Codex behaviour you meant to change.
- **Claude-only change:** the golden record must stay byte-identical. If it moves, the change leaked into
  shared code: branch through `host.py` instead.
- **Shared change:** it changes both hosts, so tell the user. Re-record the golden record
  (`python checks/codex_golden.py --write`) only for an intended change the user agreed to. Then read the
  fixture diff: only the intended text may move (no `route` kinds), and fix `codex_golden.py`'s scenario
  inputs if their meaning changed.
- **Before saying anything works:** run `python -m pytest checks -q -p no:cacheprovider` (595+ tests,
  about 5 minutes). After packaging, manifest, hook or installer changes, also run
  `python scripts/package_plugin.py` and both install smokes.

## Working rules

- Commit or push only when the user asks; releases, tags and GitHub Releases are the user's call.
- Claude Code may be editing this repo at the same time. Check `git status` before and after git
  operations, and never restore, stash or overwrite files you didn't change. A worktree
  `..\CLI-MODE-claude` (branch `claude-port`, merged) may also exist.
- `dist/` is untracked; archives are never committed, only attached to a tag's GitHub Release.
- Menus stay 40 columns wide; the same text is read on a phone.
- Claude Code specifics that look odd but are deliberate:
  - green text is LaTeX `$\color{228b22}\small\textsf{\textbf{…}}$`: at most 60 characters, no `#`,
    split into spans when long, and "Passing to" ends with a zero-width space, all because of the
    desktop app's inline-maths rules;
  - Claude menus are ```` ```diff ```` blocks whose title rows start with `+`, to colour them;
  - Claude relays post nothing mid-turn, because the desktop app folds text between tool calls out of view.
- Release recipe:
  1. Bump `.codex-plugin/plugin.json` to a plain version (no `+codex` stamp).
  2. Run `package_plugin.py --sync`.
  3. Update `CHANGELOG.md`, `RELEASE_NOTES.md`, the README install refs and `checks/v<ver>-validation.md`.
     Review `AGENTS.md` and `CLAUDE.md` against the code and update their "Written for" lines.
  4. Run the tests and both smokes.
  5. Commit, push, create an annotated tag and a GitHub Release with both zips and their `.sha256` files.
  6. Check that `marketplace add …@tag` installs on both hosts.
