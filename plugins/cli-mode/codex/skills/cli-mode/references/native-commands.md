# Provider slash commands

Only `/help` is CLI-MODE help. `/cli` and `$cli` remain host controls.
Admission is backend-specific: Antigravity switches transport to its native CLI,
while Claude Code, Grok Build, Cursor, GitHub Copilot and Codex CLI expand their own
commands inside the same ACP session. All of them refuse an unavailable command before dispatch
instead of forwarding it as model text.

## Antigravity (`agy`)

After activation, leading slash commands are checked against structured native
`/help` metadata, the documented workflow catalog, or native `/skills` metadata.
Unknown commands and unavailable terminal features fail before dispatch or session
changes. A slash prefix alone never proves that the native client handles a command.
The catalog covers all 35 commands and 10 aliases in the official CLI reference.
New handlers and aliases advertised by the installed CLI are accepted dynamically.

Documented aliases normalize only the leading command token; arguments and stored
original messages stay intact. `/teamwork` becomes `/teamwork-preview`, `/quota`
becomes `/usage`, and the other reference aliases use their canonical spellings.
`/help` belongs to CLI-MODE. `/commands` is not a help alias; unless the native
CLI independently advertises it, it is rejected as unknown.
Quoted commands and slash commands later in prose remain ordinary prompt text.

The first provider command validates the selected model against `agy models`,
closes the ACP session, and reports a fresh native conversation. ACP history is
retained but not copied. Follow-up messages resume the exact saved native
conversation ID; never use `--continue`, which could select unrelated work.
Native CLI uses its own signed-in account. No sign-in or installation is implicit.

Slash commands use a literal `-p` argument with structured output; ordinary
follow-ups use streaming stdin. Neither path uses shell interpolation. The native
client, not a host-authored prompt, expands commands. The documented workflows
`/boost`, `/teamwork-preview`, `/goal`, `/plan`, `/grill-me`, `/learn`, `/schedule`,
`/browser`, and `/btw` require task text. Workflow recognition does not prove that
all provider workflow features are available to the account or working headlessly.
Never substitute a model prompt for an unavailable native command. Native handler
argument restrictions still apply: for example, `/model` is a headless query;
use `/cli model` to change CLI-MODE's selected model.

Teamwork begins with the native scoping interview. Starting that interview is not
proof that worker agents are running. Relay its questions and wait for the user's
answers/approval; do not invent approval or bypass the workflow.

The native process exits after each turn; conversation history persists. Settings
changes validate native model/effort and apply flags on the next turn. `/cli stop`
gates dispatch and terminates an owned in-flight native CLI process, retaining
history. This is not proof that provider-managed background teams have stopped;
report this limitation and use the provider's own task controls for those teams.
Never independently launch worker sessions. Native tool/thought events are filtered;
only agent responses, final errors, and public completion events are relayed.

## Claude Code (`claude`)

Claude expands its own slash commands in the same ACP session. There is no
transport switch, no closed session and no lost history, so never tell the user
a fresh conversation was started and never persist a second conversation ID.

The adapter publishes its command list as a live `available_commands_update`
notification. CLI-MODE reads the persisted `acpx.available_commands` list when
available and also caches streamed updates for the owned session. If neither
source provides a list, the runtime answers for an unseen command. Once a list has
been observed, a command outside it is refused before dispatch.

Commands the adapter removes from its advertised list are refused without
needing an observed list, because their UX lives in the interactive terminal:
`clear`, `cost`, `keybindings-help`, `login`, `logout`, `output-style:new`,
`release-notes` and `todos`. Use `/cli model`, `/cli effort` and `/cli access`
to change settings; `/cost` and `/usage` are not host quota commands, and
utilization is reported once at activation from the native zero-turn query.

`AskUserQuestion` is routed through ACP elicitation where supported and is
otherwise disabled at session creation. Report a blocked interactive question
rather than answering on the user's behalf.

## Grok Build (`grok-build`)

Grok expands its own slash commands in the same ACP session, with no transport
switch and no lost history. Where the runtime publishes an
`available_commands_update` notification, CLI-MODE caches it for the owned
session and refuses a command outside that observed list; before any list is
observed it admits the command and Grok answers for itself.

`/cli model`, `/cli effort` and `/cli access` remain the host controls for
settings. Utilization is reported unavailable for this backend, so no provider
usage command substitutes for it.

## Cursor (`cursor`)

Cursor expands its own slash commands in the same ACP session, with no transport
switch and no lost history. Where the runtime publishes an
`available_commands_update` notification, CLI-MODE caches it for the owned
session and refuses a command outside that observed list; before any list is
observed it admits the command and Cursor answers for itself.

`/cli model`, `/cli effort` and `/cli access` remain the host controls. This
runtime exposes no usage command, so utilization is reported unavailable.

## GitHub Copilot (`copilot`)

Copilot expands its own slash commands in the same ACP session, with no
transport switch and no lost history. Where the runtime publishes an
`available_commands_update` notification, CLI-MODE caches it for the owned
session and refuses a command outside that observed list; before any list is
observed it admits the command and Copilot answers for itself.

`billing` is an interactive slash command inside the Copilot client, not a
shell subcommand, so it is unreachable over ACP and utilization is reported
unavailable. `/cli model`, `/cli effort` and `/cli access` remain the host
controls, though only access has advertised choices for this backend.

An advertised command may return `noPublicOutput: true` after a successful
completion (observed for Grok `/context`). Report that the command completed
without public output; do not invent a result or resend it. Ordinary prompts
and readiness still require a public reply.

## Codex CLI

Use only the session-advertised ACP commands. Direct strips `/d` or `$d` before
validation, so `/d /status` uses the same ACP session. The adapter exposes a
subset of terminal commands; do not assume terminal UI commands are supported.
There is no native transport switch. Read-only status can complete without
model-generated text. Account-changing commands require an explicit user request.
