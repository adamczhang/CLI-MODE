# Persistent controller and host hooks

Use the bundled `../../../../scripts/controller.py` relative to this file's
directory (plugin root `scripts/controller.py`). Resolve that absolute path once.
Use `python` on Windows. Use the actual `CODEX_THREAD_ID`, or the `session_id`
provided by the hook; never invent a new ID on each turn. Pass `--workspace` as
the user's absolute target workspace on every call. Hooks pass the same identity.

```powershell
python $controller --thread $thread --workspace $project status
python $controller --thread $thread --workspace $project frontend
python $controller --thread $thread --workspace $project catalog
python $controller --thread $thread --workspace $project draft --phase model --file $draftJson
python $controller --thread $thread --workspace $project activate --model gemini-3.8-flash-high --access allow
python $controller --thread $thread --workspace $project queue
python $controller --thread $thread --workspace $project relay --request $requestId --cursor 0
python $controller --thread $thread --workspace $project observe --request $requestId --cursor 0
python $controller --thread $thread --workspace $project resume
python $controller --thread $thread --workspace $project bind --agent grok-build --name ELON
python $controller --thread $thread --workspace $project tune --phase effort --name COD-7K
python $controller --thread $thread --workspace $project commands
python $controller --thread $thread --workspace $project agents --max 4
python $controller --thread $thread --workspace $project use --name COD-7K
python $controller --thread $thread --workspace $project cancel --name COD-7K
python $controller --thread $thread --workspace $project close --name COD-7K
python $controller --thread $thread --workspace $project off
```

`--name` takes an agent's name in any case (`COD-7K`, `cod7k`, or `-7K` when only one
running agent has that id); without it, the current agent is meant. `close` without
a name returns a chooser menu when several agents run, and is `off` otherwise.

`frontend` records setup pending and returns `activationMenu` using accepted
settings, or the initial defaults on first use. Display that text and wait.
`activate` without model/access arguments reuses accepted defaults when available;
explicit arguments apply the selected setup choices. `draft` saves the
phase, choices and displayed catalog snapshot in a JSON object supplied by the
host. Do this whenever changing menus so compaction cannot reorder selections.
Call `activate` after a menu selection or resolved tuning choice. `bind` authorizes saved defaults directly and checks first-use prerequisites without another menu confirmation. With
`--message-output` it also returns the activation confirmation, running the usage
lookup alongside activation. It verifies the native
settings and a read-only response containing a fresh readiness marker before
marking the mode active. A normal transport completion carrying a refusal or
paywall message without the marker is not readiness. `bind`, and `activate` from an
agent's activation page, start a NEW agent session (up to the agent limit) that
becomes current; other agents are untouched. Settings changes (`tune`, the settings
menu) reconfigure the session they opened on. While applying settings, dispatch to
that agent is gated; a failed reconfiguration leaves it not ready, with ownership
retained for close/off.
Do not claim the old settings are still accepted after a partial failure.
Runtime adapters are registered in `scripts/adapters.py`; this release
implements `agy` (Antigravity CLI), `claude` (Claude Code CLI),
`grok-build` (Grok Build CLI, also reachable as `grok`), `cursor` (Cursor CLI)
`copilot` (GitHub Copilot CLI) and `codex` (Codex CLI). A registry
record in backends.json alone is discovery metadata, not an implementation.
A conversation runs up to `agentLimit` (4, at most 8) agent sessions of any
backends. Each owned entry keeps its `alias` (its name), settings and `lastUsedAt`;
`main` is the current agent and `backend`/`settings` mirror it. Each agent has its
own FIFO queue and worker (`runners`, keyed by session), so agents work side by
side; captured requests record their `session`, `agent` and `name`.

Only a `/d` or `$d` prompt reaches the agent (Passthrough mode was removed;
a conversation saved in it opens in Direct mode, and a request it already
captured still sends as captured). `routingMode` is always `direct`.

For a `/d` prompt, the hook queues the captured original message. The worker
rejects unprefixed/empty requests before provider calls, removes the complete
`/d` or `$d` trigger plus one separator, and preserves the remaining payload.
Hook restore/compaction remembers host versus delegated turns without putting
user prose in routing metadata; temporary captured input is stored separately. Host turns allow normal Codex work; only delegated turns suppress
Codex subagents. A concurrent routing change applies only to later captures;
queued messages retain their routing snapshot and the active session continues.

`status` reports durable routing state, owned sessions, accepted settings, pending
menus, outstanding operations and the most recent hook observation. It is not a
live agent-health or quota call. State lives in `$CODEX_HOME/plugin-data/cli-mode`
(default `~/.codex/plugin-data/cli-mode`), outside plugin files. `CLI_MODE_DATA`
can override this for tests but must be identical for hooks and host commands.
Do not use the legacy `~/.codex/cli-mode-state` directory. Locks serialize short
state transitions; process launch is ordered under the lock, while slow ACPX
response waits run outside it. State is atomically
replaced and keyed by the real conversation ID, with a workspace consistency check.
After off has cleared all sessions, pending setup and operations, an inactive
conversation may bind to a new workspace. Unfinished ownership blocks rebinding.

Read controller `catalog` for menus: it prefers the writable refreshed catalog
over the bundled snapshot. Refresh never writes into the installed plugin tree.
Settings and histories never contain account credentials. Public response logs
are saved under the state directory's `requests` folder. Captured input lives in
a conversation-scoped subdirectory, separate from routing metadata. It is deleted
after submission settles or on off. Later messages and local help controls do
not discard queued input.
After a process crash, off also removes leftover captured input. Request receipts
retain IDs, status and event paths, never the original prompt. ACPX retains its own provider history.

For a handoff, use `relay --request <id>` with the ID supplied by the hook; see
[handoffs](handoffs.md). The hook captures the exact text and starts a detached
conversation-owned worker; the host must not reconstruct or resubmit it. `relay`
blocks briefly, batches public events and returns a Markdown update to post, plus the
turn's one view reference when it settles. `observe`
is the raw form beneath it: the receipt and up to 100 public events from its byte
cursor. Repeat with the returned cursor
to continue streaming without holding the provider turn in the Codex response.
The observation also reports `workerState`, `statusAgeSeconds` for the current
receipt status, and `withoutPublicUpdateSeconds` since dispatch or the last
public event. These are local elapsed-time measurements, not claims about
provider activity. A running submitter with 30 seconds of public silence can
receive a neutral wait update at most once per minute. A stale worker or
uncertain receipt calls for inspection instead of a progress claim.
`queue` lists redacted request IDs/statuses; `resume` returns the captured request
IDs to monitor and starts the worker when it is safe to do so. The hook relays
those existing receipts until they settle. It never resends a submitted prompt.
Admission is recorded before provider calls.
Duplicate manual submissions return the existing result without another turn.
Local pre-dispatch rejection is terminal without being marked uncertain. Confirmed
provider failure or cancellation is also recorded separately from lost transport.
A submission whose outcome is unknown remains uncertain and must not be replayed.
Legacy unresolved receipts acquire a stable recovery operation on read. Inspect
and acknowledge an uncertain operation only after its submitter stops.
`send --file` remains for integrations without captured hook input; it creates
a receipt and joins the same admission path. It cannot bypass an unresolved request.
Admission covers metadata preflight, command validation, dispatch and final metadata.
Settings controls also checkpoint before launch, block overlapping admission, and
retain uncertainty when their accepted result cannot be confirmed. Inspect actual
provider settings before acknowledging a lost control response.
Use normal background execution/polling so user steering remains responsive.
Cancellation records both a request flag and an operation marker. The bridge
observes that marker before and during runtime admission, so cancel cannot be lost
between spawning the submitter and reaching the agent.
`dispatched` means ACPX was launched, not proof of completion. The controller
emits public message, plan, artifact, activity, usage, completion and error events.
Activity defaults on; `progress --choice quiet|activity` changes the saved display
preference for subsequent prompts without provider calls. The shared bridge
allowlists tool identity, kind, status, bounded title/locations and numeric usage.
Execute/unknown tool titles are generic. Raw tool payloads, thinking tools,
private thought chunks and arbitrary stderr never enter the public log.
`format-progress --file PUBLIC_EVENTS_PATH` with `--message-output` renders a
bounded, escaped activity snapshot and returns a text fallback without dispatch.
A deterministic relay coalesces text
fragments until a line end after at least 400 characters, 2,000 characters, one
second, a message-ID change or another public event; text is preserved exactly.
`relay` batches further before rendering (800 characters, 1.5 seconds of quiet,
6 seconds held, or settlement). Repeated identical plan
snapshots are suppressed; changed and empty plans replace the previous snapshot.
Tool state changes are immediate, duplicate snapshots are suppressed, and other
activity/usage changes coalesce for 500 ms and flush before completion. Tool IDs
are scoped to one operation. Each operation tracks at most 4,096 tools, dropping
additional identities rather than evicting completion/privacy state. Plan and
tool statuses are provider reports, not independent verification. Usage is
context/turn information, not subscription quota.

For a delegated turn, follow the public
progress presentation in [handoffs](handoffs.md). Poll/yield while the submitter
runs so updates reach the chat before completion; do not block until the whole
response finishes. No extra classifier model is used. Actual chat latency also
depends on host polling. A successful return requires ACPX's canonical completed
turn result with `end_turn` and complete public-output observation. Raw RPC errors,
CLI exit-code history and a journal end marker cannot establish turn success.
The journal supplies media as well as text; its cursor is saved only after public
output is flushed to disk. Invalid cursors restart observation, never submission.
A completed turn with missing output reports that limitation and retains its
provider outcome; it must not be replayed to recover the response.
Final metadata refresh failure does not turn completed provider work into a retry.

The first `relay` view carries the bound adapter's Passing to <Agent>... announcement;
report any subsequent failure accurately. Relay views render public provider text
through the shared presentation guide. Direct routing requires a /d or $d prefix; no host complexity decision exists.

`tune --phase model|effort|access` requires an active bound session and no inflight
work. It stores accepted settings and the catalog snapshot in pending setup,
marked `tuning`. Interpret the user's plain text using that snapshot and the
bound backend guide's tuning rules, then apply resolved IDs with `activate`. Empty or
ambiguous choices show options without changing live settings. Preserve the
`tuning` marker and snapshot when saving subsequent drafts. A cold owner is woken
by a readiness turn that must resume the existing provider conversation before
owner-only controls run. This may reconnect the provider process, but cannot
silently start a replacement conversation.
`commands` returns the single plain-text Commands table without state changes.
The routing hook records that help is open and consumes X before other menus or
forwarding. Other unprefixed replies remain local while help is open.
`format-menu --file PATH` wraps host-generated menu text at 40 characters per row
including the ASCII frame. Use it for all phase/tuning/refresh/ambiguity menus.
See [presentation](presentation.md) for styled menus, accent attribution, default-colour provider messages and the plain-text fallback.

`status.inflight` retains uncertain operations after a crash/timeout. Inspect the
named session's ACPX status/history and actual files, cancel if necessary, then
explicitly acknowledge the operation with `acknowledge --operation ID` before a
new send. Acknowledge is not a retry. Never clear an operation while its submitter
or provider turn is still running; the controller rejects a tracked live submitter.
Submitter process liveness is checked so a host/controller crash does not leave
an inspected operation permanently locked. Unknown liveness stays protected;
verified off can clear operations whose submitters are dead.
Provider conversation identity is distinct from ACPX's durable record ID. An
unexpected provider ID change detected before dispatch rejects the new prompt.
A change reported after a completed turn produces a context warning; report
continuity uncertainty without silently appending a brief. Explicit settings
changes may rotate a provider ID, which is verified before accepting the binding.

The controller exposes no worker creation or per-session dispatch commands.
`send` targets only the current agent. Legacy (non-agent) ownership blocks dispatch
and activation until off/cleanup completes. `close --name` closes one agent: its
queued requests are superseded, its running turn is canceled and its session
closed, while the other agents keep working. `off` gates routing first, cancels and
closes all recorded ownership (including legacy workers), and reports incomplete
shutdown if operations are still finishing.
Repeat off to verify final cleanup after those operations settle; never kill
unrelated processes or delete provider history.
Verified closure is recorded while a submitter finishes, so an already-closed
session cannot leave a stale inflight operation when its canceled stream ends.
Failed closure still retains ownership and uncertainty for inspection.
Shutdown completion and remaining failures are derived from final saved ownership
and inflight state, including closure completed concurrently by another caller.
`shutdownScope` is `owned-sessions` and `processTreeVerified` is false: logical
closure is not evidence that all descendants or provider-managed background work
exited. The runtime close check requires the named open session to be absent;
a merely dead adapter is not sufficient.
Unfinished ownership blocks reactivation until off completes cleanup. Settings changes return the same main-session ownership after verification.

## Runtime boundary

Each binding records the absolute Node executable and npm ACPX package path.
Every invocation verifies ACPX 0.18.0 and its public runtime entry point; repairing
that installation is required if it changes. Setup installs this exact version.
The Node bridge uses `acpx/runtime`, not private ACPX source imports or raw RPC
completion heuristics. ACPX owns its persistent queue owner, strict resume,
targeted cancellation and canonical result; the controller owns host routing,
receipts, settings admission and public presentation.

An empty ACP session may be a placeholder that a provider cannot resume.
On a new binding, the first read-only readiness prompt uses ACPX's normal CLI
empty-session recovery. The controller saves the resulting provider identity,
then applies settings through that running owner and verifies them from session
metadata. Activation sends one readiness prompt, because each is a full provider
turn; a model the plan cannot serve surfaces on the first real prompt. User
prompts and later readiness checks use the shared bridge with strict resume; a
changed provider conversation is never silently accepted.

The bridge is one long-lived Node process per controller or worker process. It
serves requests in turn, reuses the loaded ACPX runtime and caches
`acpx config show` output until the files ACPX reported change. It reports the
provider session identity after each turn, so ordinary prompts need no separate
`sessions show` before or after (provider commands still read metadata first,
for the advertised command list). After draining the queue, the worker waits up
to five minutes for the next message before exiting, keeping the bridge warm.

Registry overrides and documented auth configuration are read from ACPX config;
credentials remain ephemeral. The shared API cannot inject configured
`mcpServers` or offer per-turn permission callbacks. Nonempty ACPX MCP configuration
is rejected explicitly before prompt dispatch; configure tools in the provider CLI.
An embedded persistent runtime would be needed for host-injected tools or
interactive approval callbacks. Native Antigravity handoff remains an explicit
separate transport under the same controller lifecycle.

## Hook integration and limits

The plugin bundles `hooks/hooks.json`: `UserPromptSubmit` refreshes routing
context, `SessionStart` restores it on resume/compaction, and `PreToolUse` denies
native subagent start/follow-up calls when active. Matching is limited to native
and collaboration tool identifiers; connector messaging tools are not blocked.
On an off prompt, the hook
immediately disables routing and instructs the host to finish shutdown. On a
delegated prompt, it starts a detached worker after committing the capture;
the hook itself makes no model call and opens no terminal UI. The skill handles
setup, quota and presentation in the existing Codex chat.
For mid-turn compaction, hooks retain the current control or relay route
without storing user prose in routing metadata. A fresh user prompt queues
behind accepted work. Compaction
during a frontend restores its saved phase instead of opening a new menu.
Once any delegated turn starts, its operation/event path replaces the pending route.
Compaction resumes observing or relaying that result; it must never resend the
original task. Completed setup restores active mode instead of a nonexistent menu.

Windows uses the documented JSON `commandWindows` override. Codex has run it as
`cmd.exe /C "<command>"` and, from 0.155, through PowerShell; the command is one
`python -c "..."` argument with no `$`, `%` or inner double quote, so both shells
pass it unchanged. Python reads `PLUGIN_ROOT` from the environment and runs
`hooks/route.py` as `__main__`, so spaces or `&` in the plugin path cannot break it.
Hook stdin passes through and the exit code propagates. The builder verifies the
exact command. Wrapper tests run it under `cmd.exe` forms (including Codex's) and
under PowerShell 7 and 5.1, with spaces and `&` in the plugin path; they do not
establish installed Desktop hook execution or trust. See the official hooks
documentation below.

Installation and hook trust are separate user actions. Before activation verify
Full Access via hostAccess and a fresh hookSeen from this plugin/task. Review and
trust hooks in Codex Desktop: Plugins > CLI-MODE > Hooks > Review / Trust all.
No separate Codex CLI is required. Recheck setup after approval; if approved hooks
still do not run, reload in a fresh task.
Absent hooks mean persistent routing is not ready: report the setup blocker;
do not describe instruction-only behavior as enforced routing. A manually
invoked hook fixture or controller test is not proof of host integration.
Hooks are guardrails, not a sandbox or an input replacement service. Specialized
tool paths can bypass generic hook coverage; host adherence to the shared skill
is still required. See [official hooks documentation](https://learn.chatgpt.com/docs/hooks).

Before first activation, run `../../../../scripts/doctor.py`. If a standalone skill
conflicts, use the verified namespaced plugin entrypoint and report the conflict;
do not load its old terminal wizard. Doctor is read-only and never uninstalls or
modifies accounts. Remove a confirmed legacy install only within the user's
authorization and using allowed tools. An automatic approval rejection is a
blocker, not permission to try a different deletion mechanism.

## Shared entrypoints and installation check

Use `frontend --agent home` for /cli, `frontend --agent <id>` for a backend
menu (`agy`, `claude`, `grok-build`, `cursor`, `copilot` or `codex`), and `first-time-check --agent <id>` for prerequisite
verification. `options --phase model|effort|access --agent <id> --page <n>`
returns one paginated choice menu built from the saved catalog, so the host
never assembles or renumbers a choice list. `frontend --agent <id> --page <n>`
pages the agent list the same way. `bind --agent <id>` and `activate --agent <id>` name the backend
explicitly; `activate` also accepts `--effort` for backends whose effort is a
separate advertised selector. Read
[shared frontend rules](frontends.md) for transitions, persisted confirmation,
missing-install prompts and inline presentation.

## Inline menu output

Add `--menu-output ABSOLUTE_HTML_PATH` before frontend, first-time-check, mode
or format-menu. Use a durable path writable by the current task. The response
includes menuView.path for the inline visualization and retains text as fallback.
No view is written without that explicit option. Rendering does not activate an
agent or change its working directory. All provider calls use the thread cwd
passed as --workspace (including worktrees); the default is the process cwd.
Never substitute the plugin directory or main source checkout without a user request.

`commands` always returns ordinary chat text and never writes a `menuView`, even
if `--menu-output` is supplied for compatibility with an older caller.

## Inline public message output

`--message-output PATH format-message --kind agent --agent BACKEND_ID --file PUBLIC_TEXT_PATH`
writes a two-voice HTML fragment and returns messageView.path. This is a presentation-only command;
it never dispatches or changes state. Delegated turns use `relay` instead, which
renders the same voices from the public log itself. See presentation.md.

`settings` opens the bound active agent settings page without provider calls.
`settings --dismiss` closes it without deactivation. Bare `/cli menu` and
`/cli model` route here while active; explicit model choices keep their existing
direct behavior. `/cli mode` only explains that prompts reach the agent through `/d`.


## Menu transactions

`options` persists the phase, page, exact numbered choices and catalog snapshot.
Draft settings override accepted settings only for display/selection; accepted
runtime settings change only after verified activation. `choose <number>` uses
that saved mapping and advances model -> effort -> access. Natural-language
choices must resolve to that same mapping. Do not reinterpret numbers from a
freshly reordered catalog. `draft` preserves the existing snapshot unless an
explicit replacement is supplied.

`navigate b` goes to the previous phase during activation and Agent Settings
during active tuning. `navigate >` and `navigate <` page the current list.
`navigate r` or `refresh` operates on the model page. X closes active settings
or tuning without stopping; X on routing goes back. Initial setup Exit stops.

Refresh reads the owned ACP session's last advertised metadata, without a new
session, reconnect, coding prompt or model switch. Without eligible metadata it
returns `refreshed=false`, `catalogStatus=cached` and retains the snapshot.
Successful refresh atomically replaces the catalog, clears draft choices and
returns to model selection. Efforts observed for the current model are never
assigned to other models; those keep their model-specific cached observations.
New separate-effort models without an observed effort list are not offered yet.
A cached response is not proof of current provider availability.

## Activation presentation

After successful activation/binding, run `--message-output <path> activation-message`.
It requires a verified active session, reads native quota metadata where supported,
and returns one shared activation text and inline view. It refuses stale success
if the agent is stopped or reconfigured during lookup. Usage failure returns
`Usage not available through CLI`; it does not undo activation. Do not use
`format-message --kind activation` or compose a per-provider template.
Passing lines are canonicalized from `--agent`; relayed text always uses `--kind agent`.
