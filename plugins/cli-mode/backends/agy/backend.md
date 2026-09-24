# Antigravity backend for CLI-MODE

/cli agy (also $cli agy) opens this frontend, case-insensitively.
/cli bind agy starts with saved defaults without a further menu confirmation.
/cli opens agent selection; /cli stop or /cli off shuts down. /help lists controls.
Requests to build, edit or explain this backend never activate it.

Read [the shared CLI-MODE rules](../../codex/skills/cli-mode/SKILL.md) and
[the ACPX backend guide](references/acpx.md). The shared skill and this internal backend belong to the
same source/distribution. If the shared file is missing, report incomplete setup
instead of inventing routing rules.

Default to the current Codex thread working directory, including any attached
worktree. Only use another directory when the user explicitly chooses it; never
substitute the source checkout, plugin cache or project root for the thread cwd. Use
the installed ACPX `antigravity` profile, preserving any configured launcher.
ACP is the initial transport. Provider slash commands use the controller-owned
native CLI handoff described in [native command transport](../../codex/skills/cli-mode/references/native-commands.md).
Do not launch an untracked native task or install runtimes implicitly during activation.
The explicit approved first-run installer is the exception for a visible PowerShell
window and account setup; follow the shared setup-installer guide.

## Text activation menu

Follow [shared frontends and first-time checks](../../codex/skills/cli-mode/references/frontends.md).
For /cli agy, run frontend --agent agy with --menu-output at a task-owned
writable HTML path. Display the returned menuView using the shared inline
presentation guide. Its text comes from the initial template or saved defaults.
This is a menu in the Codex conversation, not a terminal wizard. Do not
activate merely because the skill was invoked. Follow `hostAccess` and
`pending.onboarding` before interpreting numbered replies. New User Detected
means 1 selects Antigravity CLI for `first-time-check --agent agy`; it never means
Yes to activation. The Setup CLI Agent page explains Desktop hook approval and
R rechecks setup. Once setupReady is true, show the activation menu and wait for
Yes. In that menu only, 1/Yes selects defaults and 2 opens model setup. B returns
to the agent menu. No reply or elapsed time is acceptance. Never delegate setup
replies. See the shared frontend reference for the complete setup flow.

The initial requested default is **Gemini 3.8 Flash**, **High** reasoning, with
**Allow (YOLO)**. The default access value is `allow`, mapped in the cached catalog
to Antigravity's `mode=yolo` (native label YOLO, auto-approve all tools) plus
ACPX `--approve-all`. Apply and verify the native mode on each owned session,
for the main session, and apply the ACPX flag per invocation. Do not change global permission
configuration. This is the user's chosen permission setting, subject to the
host's existing limits and task scope.

## Three-phase model setup

Option 2 presents exactly one phase at a time in plain text and waits for a
selection before advancing. Use controller `catalog` for instant display; it
prefers the writable refreshed catalog over [the bundled snapshot](assets/models.json).
Save each phase, displayed snapshot and draft via controller `draft`. Keep draft selections separate from the active configuration
until all three phases complete. Do not contact the runtime merely to open the
menus. Menu numbers refer to the displayed snapshot, never a later reordered
list. Use [the menu examples](assets/setup-menus.txt) as layout guidance and
populate choices from the cache rather than treating the examples as a catalog.
Pass every generated phase, tuning or disambiguation menu through controller
`format-menu --file` with --menu-output before displaying menuView. Follow the shared presentation guide;
do not highlight menus or double-frame already formatted output. The first input
line is CLI-MODE; the second is exactly Select Model, Select Reasoning Effort,
or Select Access Level for the current phase. The formatter adds the divider
below these two header rows. Put phase numbers and selected settings in the body.

1. **Model — 1/3.** List `modelFamilies` by name, without effort suffixes:
   Gemini 3.8 Flash, Gemini 3.7 Flash, Gemini 3.6 Flash, Gemini 3.1 Pro in the
   current snapshot. Mark Gemini 3.8 Flash as the initial default. Include the
   cached date, `R. Refresh models`, and `B. Back` to the activation menu.
2. **Effort — 2/3.** Show only the selected family's advertised efforts. Mark
   High as the initial default when available, but require a choice; never invent
   Medium for a model that only offers High/Low. Include `B. Back` to model.
   Resolve the pair using its cached `modelId`, not string concatenation: Gemini
   3.1 Pro High, for example, maps to `gemini-pro-agent`. If a future model exposes
   a separate effort control, use its verified choices and preserve the model ID.
   If no effort choice exists, show its single Provider default option explicitly.
3. **Access level — 3/3.** List the cached access aliases and native descriptions,
   with `allow` first, then prompt. Auto-edit is unsupported by the ACP transport. Initially mark
   allow Default; later mark the saved access choice Default.
   Include `B. Back` to effort. Choosing access completes setup and proceeds to
   connectivity/settings verification; no fourth confirmation menu is required.

Use current conversation selections as defaults on later visits. Back preserves
compatible drafts. A changed model clears its old effort selection; do not carry
an incompatible effort or ID forward. Exiting setup without completion leaves
the previously active configuration unchanged. No reply is never a selection.
Menu replies belong to setup, not passthrough. Do not use Codex's model list,
web model lists, or invented options.

For R/Refresh, use shared controller `refresh` on the model page. It reads
owned ACP metadata without launching another session or changing settings.
No eligible ACP session or invalid metadata leaves cached choices intact with
an explicit message. Successful refresh resets draft choices and returns to model selection.

After all phases, verify the selected model/effort pair and access setting live.
Preserve explicit Medium/Low and stricter access selections. Store the accepted
configuration as this conversation's default for reactivation;
do not edit provider global defaults. Reflect the actual selections in later
activation menus, including when access is no longer allow.

## Bound-agent tuning and binding

/cli bind agy calls controller `bind`. This is explicit authorization to use
saved defaults, initially Gemini 3.8 Flash High with allow. The controller opens
pending setup, checks prerequisites if unconfirmed, and applies the usual hook,
settings and readiness gates. Do not wait for another Yes. On success use the
same activation and quota confirmation below. Never install or sign in implicitly.

`/cli model`, `/cli effort` and `/cli access` (or `/cli permissions`) <choice> run controller `tune --phase <phase> --apply`, exactly as the routing hook gives it. The controller matches the typed choice against the advertised options itself and applies a unique match to the same session, preserving unrelated settings; with no choice or an ambiguous one it returns the phase menu for a numbered reply. Never interpret the catalog yourself or invent a model, effort or access level. Applying a change reconfigures the existing session; partial failure gates dispatch until off/cleanup.

Antigravity encodes effort in the model ID, so an effort choice selects the matching model variant of the current family; a model choice keeps the current effort when that family offers it and otherwise uses the family default.

The controller verifies accepted values before reporting success and saves them
as the new conversation defaults. No work dispatch occurs during tuning. `B` returns to the
AGY menu; /help preserves the pending phase; /cli stop cancels setup and stops.
For refresh use the procedure above and preserve the tuning marker. After a
refresh invalidate unavailable drafts and show the model choices again.

## Verify and activate

First verify Full Access and a fresh current-plugin hook observation, following
the shared controller guide. Hook approval is available in Codex Desktop at
Plugins > CLI-MODE > Hooks > Review / Trust all. The command-line activation
gate rejects a missing or stale hook observation. Do not manufacture one by
running a hook fixture; complete plugin/hook setup separately. Controller-only
development tests are not activation of this host conversation.

After option 1 or completed option 2, run `first-time-check --agent agy` if not
yet confirmed, then continue activation on success. Verify the installed ACPX executable and the configured
Antigravity ACP launcher/runtime and helper. Use the existing profile and its
login. A native `agy` binary on PATH alone is not sufficient. Session acquisition
and a successful nonmutating prompt must verify authenticated connectivity.
Do not open browser sign-in or run setup/install commands automatically. If
anything is missing, disconnected, or unauthenticated, report the specific
blocker and offer the approved guided setup or manual installation/sign-in, then retry this
activation. This runtime uses its configured internal OAuth login, so use ACPX
`--auth-policy skip` to let the runtime authenticate; this does not bypass the
runtime's login. ACPX `fail` incorrectly rejects this setup merely because no
explicit ACPX credentials were supplied. Do not expose
credentials or change profiles to bypass the failure.

Use the shared controller's `activate` with the selected native model ID and
access alias; do not create competing state or issue raw lifecycle calls instead.
It creates a unique namespace, persists ownership, verifies settings/readiness,
and commits active state atomically. Identical settings reuse the main session;
changed settings reconfigure that same session. A partial failure gates dispatch
and requires off/cleanup; never start a candidate alongside the main session.
Read advertised model and
configuration metadata from the connected session. Verify that the cached access
mapping is still advertised, apply the chosen native mode (`set mode yolo` for
default allow), and verify its accepted value and matching ACPX policy before
reporting access. For prompt, use `--approve-reads` and
`--non-interactive-permissions fail` instead of `--approve-all`; surface any
permission request needing user handling without weakening that choice.
The native initial `default` mode is not CLI-MODE's desired default `allow`.
If a mapped mode is absent or rejected, report the blocker and refresh access.
Resolve the selected model and effort (initially Gemini 3.8 Flash and High)
to the real settings, whether effort is a separate control or part of
an advertised model variant. Do not guess IDs or assume `reasoning_effort` is
the adapter's key. Apply the selections and verify the accepted values. If the
requested default is absent or ambiguous, state that and show live choices;
do not silently use the runtime default or a different model. Missing model
metadata is a setup/capability failure, not permission to invent a list.

Verify session creation and send a short nonmutating readiness prompt asking
the agent to identify its working directory and confirm readiness without making
changes. State that CLI-MODE is active only after a successful response. If setup
fails, report activation pending and the concrete blocker; do not claim readiness
from the existence of a process. On success, follow
[the shared confirmation contract](../../codex/skills/cli-mode/references/activation.md).
Run controller `--message-output <path> activation-message`; display its result
without constructing a provider-specific template. Control commands accept no
appended task. No delegated task runs before verified activation.


## Persistent process lifetime

Use the main named ACPX session with `--ttl 1800` on all prompt invocations,
including the readiness prompt, and on session/control operations that may
launch an owner. Antigravity owns any internal subagent lifetime. Never use `exec` for
ordinary mode prompts or close the main session after returning an answer.
The prompt submitter can exit while its detached session owner and agent stay
alive for follow-ups. The 30-minute timer applies only after work/queue activity
drains; it is not a 30-minute limit on an active coding task. Do not send artificial
keepalive prompts or create a scheduler to keep the process alive.

Idle expiry releases the process, not saved conversation history. Routing stays
selected in this host conversation: a later ordinary prompt restarts the owner
and attempts to resume the saved provider session with the same settings. Report
any actual context loss according to the shared recovery rules. A crash or reboot
can also stop the process, so do not promise uninterrupted process lifetime.

`/cli stop` disables routing and shuts down this mode's
owned main session (plus legacy owned workers from older versions). Stop admitting new work, cooperatively cancel
active turns, close those sessions through ACPX, and check their shutdown state.
Do not kill unrelated AGY processes or delete history. Report incomplete shutdown
if a close fails, rather than claiming every process stopped. Repeated off is
idempotent. Explicit off requires a new activation before routing resumes;
unlike idle expiry, closing marks sessions closed, so later activation starts a
fresh session unless the user explicitly requests supported history restoration.

## Per-message communication

After activation and setup, pass every ordinary prompt intact to the same main
AGY session. Do not classify complexity, add a brief, offer orchestration, split
tasks or spawn another CLI session. AGY owns all task planning and decides whether
its native subagents are useful. See [native subagents](references/subagents.md)
for documented capabilities and the ACP-versus-native-CLI distinction.

Relay the turn with the controller's `relay` command: its first view carries Passing to Antigravity... in the accent colour, and relayed Antigravity text uses the default chat colour introduced by `Antigravity says...`.
While waiting, relay public plans/messages attributed to Antigravity, then the
answer and artifacts. Follow the shared handoff guide for filtered ACPX tool
activity and usage. Raw tool payloads and private reasoning are excluded. Native
command transport retains only verified public responses. All ordinary prompts are passed unchanged.

If the backend fails, report the blocker without doing its work in Codex.
Mode controls and setup replies are not task prompts and receive no dispatch
banner. Stop mode first to speak to Codex normally.
Only `/cli stop` ends routing; idle expiry preserves selection and history.

## Shared transaction and navigation rules

See [shared menu transactions](../../codex/skills/cli-mode/references/frontends.md#shared-menu-transactions).
