# CLI-MODE — working notes for Claude

> **Written for CLI-MODE 0.3.2** (tag `v0.3.2`, 2026-09-24).
> If `plugins/cli-mode/.codex-plugin/plugin.json` shows a different version, parts of this file may be
> out of date. Verify any file, function or rule named here against the code before relying on it; when
> they disagree, the code wins. Fix this file in the same change. `checks/test_agent_docs.py` fails
> until this line matches the plugin version and every path named here exists.

CLI-MODE drives six coding agents (Antigravity `agy`, Claude Code `claude`, Grok Build `grok-build`, Cursor
`cursor`, GitHub Copilot `copilot`, Codex CLI `codex`) over ACPX from inside a host. There are two hosts,
**Codex** and **Claude Code**, served by ONE source tree (`plugins/cli-mode`) and two installers. Every change
lands in one of three zones below; know which before editing.

## Code map

**Shared core (both hosts run it; a change here changes Codex):**
- `scripts/controller.py`: CLI entry and `run()`. It combines `menus.py` (setup and Agent Settings),
  `binding.py` (owned sessions), `dispatch.py` (one provider turn) and `queue_worker.py` (captured requests,
  FIFO worker, receipts, `observe`, `resume_monitoring`).
- `scripts/state.py`: `Store` plus `route()`, the prompt → route decision (`/d`, `/cli …`, help, setup; everything else is the host's).
  Several agents run at once: `owned[]` entries carry `alias` (the agent's name), `main` is the current agent,
  and `live_agents`/`target_of`/`agent_label` resolve and name them. `VERB_ALIASES` is the one table of command
  pairs (`close`=`stop`=`off`, `spawn`=`bind`, …). `scripts/names.py` generates, validates and resolves names.
  Gates are per agent: `pending_work(state, session=…)`, `operations.menu_holds`, one worker per agent (`runners`).
  `/d a,b <prompt>` captures one request per named agent (`turnRoute.requestIds`); a registry `tag` names the
  only agent of its kind. `scripts/changes.py` takes git-tree snapshots around each turn (a temporary index copy)
  for the change receipt and `/cli diff`. Each owned entry's `timeout` (minutes) is its ACPX owner TTL
  (`acpx.AcpxBackend.ttl`); `/cli attach` moves an open owned entry from another conversation in the folder.
- `hooks/route.py`: `decide()` and `task_through_settings()` (shared), `codex_output()` and
  `activation_reply()` (Codex only).
- Menus, text and labels: `frontends.py`, `presentation.py` (`menu_block`, `menu_frame`, `options_menu`,
  access and effort names), `help_view.py`, `confirmation.py`, `relay_view.markdown`/`render`, `menu_view.py`.
- Agents and runtime: `adapters.py`, the per-agent modules (`agy.py`, `claude_code.py`, `codex_cli.py`,
  `copilot_cli.py`, `cursor_agent.py`, `grok_build.py`), `backends/*`, `catalogs.py`, `native_commands.py`,
  `acpx.py`, `acpx-runtime.mjs`, `processes.py`, `setup.ps1`, `installer.py`, `doctor.py`, `acp-login.py`.
- Agent viewer (`/cli view on|off`): `scripts/viewer.py` opens `scripts/viewer.ps1`, a read-only PowerShell
  window that follows each turn's public events file. `dispatch.py` reopens it before each turn while it is on.
- `scripts/host.py`: the host switch (`current`, `claude()`, `views()`, `data_root`, `workspace`,
  `chat_color`). Host differences inside shared files branch only through it (`host.claude()` or `views`).
  The files with such branches are controller, state, frontends, menus, help_view, confirmation,
  native_commands and queue_worker.

**Codex only:**
- `hooks/hooks.json`, `hooks/route.py:codex_output`. Codex 0.155 runs `commandWindows` through PowerShell
  (earlier builds through `cmd.exe /C`), so it must parse in both; `test_features` runs it under each;
- `codex/skills/cli-mode/` (`SKILL.md`, `codex/skills/cli-mode/agents/openai.yaml` and the references Codex
  reads). It sits under `codex/` so Claude Code, which scans a plugin's root `skills/`, never lists it.
  Exception: `codex/skills/cli-mode/references/backends.json` is the agent registry BOTH hosts load
  (`state.REGISTRY`), so it ships in both zips;
- `QueueMixin.relay()`: mid-turn Markdown, then one HTML view;
- `.codex-plugin/plugin.json`, which is the version source of truth;
- `.agents/plugins/marketplace.json`.

**Claude Code only:**
- `hooks/claude.py`: SessionStart, UserPromptSubmit, PreToolUse approval, the Stop guard and `/cli reset`.
  `nothing_to_do()` is a pre-import fast path.
- `claude/hooks.json`, `claude/commands/{cli,d}.md`.
- `QueueMixin.relay_text()` and `relay_chain()`: nothing mid-turn, then the whole output as the last message.
- `QueueMixin.follow()` and `operations.follow_path`/`following`: a `/d` turn runs `controller.py follow` as a
  background task (the hook's `updatedInput` forces it and labels the row), ends, and is woken for one relay.
  The Stop guard reads `background_tasks` from Stop's input. `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` keeps the
  old 25 s relay loop.
- The desktop app lists any tool call running past about 2-3 s as a background-tasks row. Only agent turns
  (`follow`) may: activation runs inside the prompt hook (timeout 300 s), except an activation that widens
  access, which stays a command so Claude Code asks first.
- `presentation.strong`/`plain_strong`/`chat_menu`/`relay_plain` (green LaTeX, diff-coloured title rows);
  `relay_view.final_markdown`.
- Repo-root `.claude-plugin/marketplace.json`, which `package_plugin.py --sync` keeps in sync;
  `scripts/install-claude.ps1`; `scripts/claude_shortcuts.py` (bare `/cli` and `/d`, for the installer and
  `/cli shortcuts`).

**Packaging:** `scripts/package_plugin.py` builds `dist/cli-mode-codex-<v>.zip` (without the `CLAUDE_ONLY`
files) and `dist/cli-mode-claude-<v>.zip` (without the `CODEX_ONLY` files).

## How a change is protected

| Guard | Protects | Fails when |
|---|---|---|
| `checks/test_codex_golden.py` with `checks/fixtures/codex-golden.json` (160 steps, 31 route kinds) | every hook response and controller result **Codex** receives | a shared or Claude change alters anything Codex sees |
| `test_claude_package.py` with `checks/fixtures/codex-package-files.txt` | the Codex zip's exact file list; the Claude zip's contents; the root marketplace in sync; Claude's hook rules | a file leaks into the wrong package, or the marketplace or hooks drift |
| `test_host.py`, `test_claude_hook.py` | Claude routing, relay, colour, menus, the Stop guard, the fast path | a Codex or shared change breaks Claude behaviour |
| `test_package_reproducibility.py` | identical zips from LF and CRLF checkouts | packaging depends on line endings |
| `checks/claude_install_smoke.py`, `checks/codex_install_smoke.py` | real installs in a throwaway `CLAUDE_CONFIG_DIR` or `CODEX_HOME` (no cost) | a manifest, hook registration or installer breaks |
| `checks/claude_user_validation.py` (35 turns), `claude_stop_guard_live.py`, `live_parity_probe.py` | the installed plugin, live | real-world behaviour regresses. **These spend the user's quota: ask first** |

**Rules by zone:**
- **Claude-only change:** the Codex golden record must stay byte-identical. If it moves, the change leaked
  into shared code: route it through `host.claude()` or `views` instead.
- **Codex-only change:** `test_host.py` and `test_claude_hook.py` must still pass untouched.
- **Shared change:** it changes both hosts. Say so to the user. Re-record the golden record
  (`python checks/codex_golden.py --write`) ONLY when the Codex change is intended and the user agrees.
  Then read the fixture diff: nothing beyond the intended text may move (for example, no `route` kind
  changes). Update the scenario inputs in `codex_golden.py` if their meaning changed.
- **Always:** `python -m pytest checks -q -p no:cacheprovider` (about 5 minutes; 595+ tests) before saying
  something works. After any packaging, manifest, hook-registration or installer change, also run
  `python scripts/package_plugin.py`, then both install smokes.

## Working rules

- Commit or push only when asked; releases are the user's call (the recipe is at the end of `AGENTS.md`).
  A release also reviews this file and `AGENTS.md` and updates their "Written for" lines.
- Another session (Codex) may edit this repo: check `git status` before and after git operations, and
  never restore or overwrite files you didn't change.
- Test Codex behaviour in a throwaway `CODEX_HOME` (`checks/codex_install_smoke.py`); install into a real
  Codex configuration only when asked.
- A Claude install from the zip has three copies that must match: the build
  (`dist/cli-mode-claude-<v>/plugins/cli-mode`), the installed folder
  (`%LOCALAPPDATA%\CLI-MODE\claude-marketplace\plugins\cli-mode`) and the cache
  (`~/.claude/plugins/cache/cli-mode/cli-mode/<v>`). To sync them: `package_plugin.py`, re-extract the zip,
  then run its `install-claude.ps1`.
- Claude Code's desktop app renders `$…$` as maths only when:
  - there are at most 60 characters inside;
  - there is no `#`, `@`, `"` or `` ` ``;
  - the `$` doesn't end a still-streaming block.
  Hence the colour `\color{228b22}`, the split spans and the zero-width space after "Passing to".
  Plugin commands are always namespaced (`/cli-mode:cli`).
- Menus stay 40 columns wide (the phone's code-block width).
- In Git Bash heredocs `\\` collapses to `\`: write text containing backslashes with the Write or Edit tools.
