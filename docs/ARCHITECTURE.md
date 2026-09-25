# CLI-MODE architecture

How CLI-MODE works under the hood. For installing and using it, see the [README](../README.md).

## Components

One source tree, `plugins/cli-mode`, serves both hosts (Codex and Claude Code) through two installers.

- **Routing hooks** read each prompt. `hooks/route.py` serves Codex; `hooks/claude.py` serves Claude Code.
  They decide whether a prompt stays with the host, goes to an agent, or is a CLI-MODE control.
- **The controller** (`scripts/controller.py`) is the command-line entry point the host runs. Its
  `Controller` combines `menus.py` (setup and settings), `binding.py` (owned-session lifecycle), `dispatch.py`
  (one provider turn) and `queue_worker.py` (the detached FIFO worker, receipts and the `relay` command,
  rendered by `relay_view.py`).
- **Adapters** (`agy.py`, `claude_code.py`, `codex_cli.py`, `copilot_cli.py`, `cursor_agent.py`,
  `grok_build.py`) describe each agent: models, effort, access levels and native commands. Provider guides and
  catalogs live in `plugins/cli-mode/backends`.
- **ACPX** runs the agents over the [Agent Client Protocol](https://agentclientprotocol.com/). A small Node
  bridge (`acpx-runtime.mjs`) calls ACPX's public shared runtime; ACPX keeps the agent connection alive between
  controller invocations.

To add a CLI, start with the
[backend contract](../plugins/cli-mode/codex/skills/cli-mode/references/backend-contract.md).

## ACPX

CLI-MODE runs ACPX 0.18.0 exactly. When setup finds no ACPX 0.18.0, it installs
CLI-MODE's own copy with `npm ci` from the lockfile in
`plugins/cli-mode/runtime/acpx`, under `%LOCALAPPDATA%\CLI-MODE\acpx\0.18.0`. An
existing global `acpx@0.18.0` from npm counts as installed, so setup then adds no
copy of its own and CLI-MODE uses the global one. When both exist, CLI-MODE uses
its own copy first.

Each binding saves the ACPX installation it started with, so a PATH change cannot
silently switch its runtime. If that installation later stops being 0.18.0 (for
example, a global `npm install -g acpx@latest`), the binding stops with a repair
message rather than running another version, and running setup again installs
CLI-MODE's own copy. Installing that copy next to a global one keeps CLI-MODE independent of
global npm upgrades: new bindings pick it up.

ACPX 0.18.0's shared runtime does not support injecting `mcpServers` or interactive
permission callbacks. Configure MCP tools in the provider CLI itself; a nonempty
ACPX `mcpServers` configuration is rejected before submitting a prompt. This
integration uses the selected static access policy and fails requests needing
unavailable interactive approval.

## Requests, the queue and relaying

The routing hook captures a `/d` prompt exactly, queues it in arrival order for
its agent (the one named first, or the current agent), and starts that agent's
detached worker. Each agent has its own session, queue and worker, so agents work
side by side. A worker forwards one message at a time to its agent's ACPX session
even if the host's response is interrupted by a new message. The host reads each receipt and public events
through `observe --request <id>`; it never rewrites or resubmits the prompt.
Captured input is removed after submission or shutdown. `/cli cancel` stops an
agent's running turn; `/cli close <name>` discards that agent's queued follow-ups
and closes its session, and `/cli stop` does so for every agent. The
host relays public output into chat while its response is active; the worker
cannot independently post new chat messages after that response ends.

`/d` prompts and file-based submissions use one controller request lifecycle.
Follow-up turns require the original provider conversation to resume successfully.

After a turn, an agent's worker stays idle for up to five minutes so the next
message starts at once, then exits. Closing the agent ends it immediately.

## Agent names

Each owned session keeps a name (`alias`): one given at spawn, or one generated
from the agent's code and two characters derived from the session's own unique
name, so a conversation saved before names existed gets a stable one. Generated
names are never given out twice in a conversation (`usedNames`). Requests record
their agent's session, kind and name, so an answer keeps its label after the agent
closes. `main` is the current agent; `backend` and `settings` mirror it.

A prompt naming several agents (`/d gro-4k,cod-7k ...`) is captured once per agent, each request in its
agent's queue. An agent's `timeout` (minutes, 1 hour by default, saved in `agent-timeout.json`) is the ACPX
owner's idle TTL. `/cli attach` moves an owned entry from another conversation's state in the same folder
into this one, under both state locks, so one agent never has two owners.

## Change receipts

Around each agent turn, the worker writes the folder's content as a git tree (`scripts/changes.py`): it copies
the repository's index to a temporary file, runs `git add --all` against that copy and `git write-tree`, so
the real index never changes. `git diff-tree --numstat` between the two trees is the receipt, saved on the
request before it settles, so the relay that follows always has it. The trees stay reachable only through
the receipt; git's garbage collection removes them eventually, after which `/cli diff` says the diff is gone.

## Stopping

Stopping verifies closure of owned sessions and settlement of local submitters.
It does not certify that every provider-managed descendant or background task exited.
On Windows, each submitter process (the ACPX bridge, ACPX CLI or native CLI)
is tied to the worker that started it, so it cannot outlive that worker. The
ACPX session owner is deliberately left out of that tie: a worker that dies
detaches from its turn rather than canceling it, and the request is then shown
as uncertain for inspection.

## State and logs

- **Codex:** `$CODEX_HOME/plugin-data/cli-mode`.
- **Claude Code:** `~/.claude/plugins/data/cli-mode-cli-mode`, or `cli-mode-inline` for sessions in the
  desktop app.

Public response logs and ACPX history can contain task content. Private reasoning is excluded from CLI-MODE's
relay logs.

## Development

```powershell
python -m unittest discover -s checks -p 'test_*.py'
python scripts/package_plugin.py
python checks/package_smoke.py
```

See the [v0.3.1 validation report](../checks/v0.3.1-validation.md) for this
release, the [0.3.0 full validation](../checks/v0.3.0-full-validation.md) across both hosts and six agents, and
the [ACPX hardening report](../checks/acpx-hardening.md) for its
implementation evidence. The suite exercises the real pinned ACPX runtime with an
offline fixture agent when ACPX is installed; those tests report a skip if it is
unavailable. Live provider tests consume quota; the [validation plan](../checks/five-cli-validation-plan.md)
describes them. A CI workflow template is included in `scripts/github-actions-validate.yml`;
it is not enabled automatically.
