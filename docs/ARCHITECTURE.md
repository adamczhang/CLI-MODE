# CLI-MODE architecture

How CLI-MODE works under the hood. For installing and using it, see the [README](../README.md).

## Components

One source tree, `plugins/cli-mode`, serves both hosts (Codex and Claude Code) through two installers.

- **Routing hooks** read each prompt. `hooks/route.py` serves Codex; `hooks/claude.py` serves Claude Code.
  They decide whether a prompt stays with the host, goes to the active agent, or is a CLI-MODE control.
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

Setup installs CLI-MODE's own copy of ACPX with `npm ci` from the lockfile in
`plugins/cli-mode/runtime/acpx`, under `%LOCALAPPDATA%\CLI-MODE\acpx\0.18.0`.
CLI-MODE uses that copy first. An existing global `acpx@0.18.0` from npm still
works as a fallback, and a global upgrade to another version never changes
which ACPX a binding uses. Each binding saves its tested ACPX installation, so a
PATH change cannot silently switch its runtime.

ACPX 0.18.0's shared runtime does not support injecting `mcpServers` or interactive
permission callbacks. Configure MCP tools in the provider CLI itself; a nonempty
ACPX `mcpServers` configuration is rejected before submitting a prompt. This
integration uses the selected static access policy and fails requests needing
unavailable interactive approval.

## Requests, the queue and relaying

The routing hook captures a `/d` prompt exactly, queues it in arrival order,
and starts a detached worker for the active conversation. The worker forwards
one message at a time to the saved ACPX session even if the host's response is
interrupted by a new message. The host reads each receipt and public events
through `observe --request <id>`; it never rewrites or resubmits the prompt.
Captured input is removed after submission or shutdown. `/cli cancel` stops the
active turn; `/cli stop` discards queued follow-ups and closes the session. The
host relays public output into chat while its response is active; the worker
cannot independently post new chat messages after that response ends.

`/d` prompts and file-based submissions use one controller request lifecycle.
Follow-up turns require the original provider conversation to resume successfully.

After a turn, the conversation's worker stays idle for up to five minutes so the
next message starts at once, then exits. `/cli stop` ends it immediately.

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
