# Shared CLI-MODE frontends

Render the returned menuView through the inline visualization contract described
in [presentation](presentation.md). Pass --menu-output at a task-owned writable
path. Use the ASCII activationMenu only as a disclosed text-only fallback when
inline views are unavailable. Never forward menu replies to the agent.

Both frontends return `hostAccess` and, after permission readiness, `routingReadiness`.
Check `hostAccess` first. `ready=true` means the executing host reports the built-in
Full Access permission profile. Disabled or unknown access must not be treated as
ready: show its message and ask the user to select Full Access in the current
Codex task, then run /cli again. Do not escalate the controller to make the check
pass, change permissions automatically, or assume a provider's own Allow setting grants host access.
The frontend returns its read-only menu before creating any state when access is
not verified. No installation probes run in that case.

Missing prerequisite confirmation or missing/different-copy hook evidence opens
this first-start menu from either /cli or /cli agy:
CLI-MODE / New User Detected. / Setup CLI Agent.
When Full Access needs attention it includes *Full access in codex is required*,
and always shows numbered registry agents, currently 1. Antigravity CLI,
2. Claude Code CLI, 3. Grok Build CLI, 4. Cursor CLI 5. GitHub Copilot CLI and 6. Codex CLI.
Use those full names consistently in all menus. The agent list paginates like any other menu once it
outgrows the option cap.

`pending.onboarding=select-agent`: a numbered agent selection runs
`first-time-check --agent <that backend id>`, not activation. Installation
receipts are per backend, so one backend's passing check never satisfies the other. It scans the full Windows dependency set through the bootstrap script and returns a Setup CLI Agent page with installation results
and Desktop hook approval instructions when needed. `pending.onboarding=check`:
I offers guided installation/sign-in approval; M shows manual installation help;
R reruns that command; B opens frontend --agent home; X, /cli stop or /cli off
cancels setup. Every menu ends with one X action; routing uses Back, active settings/tuning uses Close settings, and initial setup/help uses Exit. Setup statuses show Hooks: On and
Full Access: On when verified, otherwise !Attention! with only the relevant
instructions. No blank row precedes Antigravity CLI on the Setup page.
All of these replies are setup controls, never delegated prompts. Restore the
onboarding marker after compaction; do not interpret 1 as activation in this menu.

For hooks, stay in Codex Desktop: Plugins > CLI-MODE > Hooks > Review, or Trust all
for this plugin. The installed Desktop plugin detail UI supports these controls.
The user approves the current definitions; the plugin never approves itself,
manufactures hook receipts, disables hook trust, or requires opening Codex CLI.
After approval, return and recheck. If approved hooks still do not execute, a new
task may be needed to reload the plugin. Missing evidence does not by itself prove
hooks are untrusted. A stale observation for an already configured user offers
Recheck routing rather than claiming the user is new.

Installation receipts persist per backend across tasks, but do not stand in for
live hook evidence. `confirmed` reports installation only; `setupReady` requires
both installation and current-plugin hook evidence, after Full Access is checked.
Failed probes offer [guided installation](setup-installer.md), manual installation
links or Exit. Installation and sign-in require explicit approval. Partial success keeps setup pending. Once setupReady is true, the
controller clears onboarding and shows Agent Settings with Yes/defaults and
Change defaults. Setup completion alone never starts the agent: wait for Yes.
The Agent Settings page also shows `Mode: Passthrough` initially (or the saved
routing choice) and `3. Change routing mode`. That choice opens controller `mode`
with `--menu-output`; show its returned inline menuView using the same renderer
as every other menu. Selecting a mode or X returns the Agent Settings page with
its pending setup preserved. Wait for Yes before activation. Never reset a saved
Direct choice just because the user opens another agent's settings.
An explicit /cli bind <agent> retains its existing authorization to activate
that backend's defaults, but still requires all readiness gates and
authenticated agent verification.

Once any backend is configured, /cli shows Select CLI Agent, with Setup needed on unconfirmed providers. A numbered selection uses controller choose; it checks that backend when needed and otherwise opens its activation page. B returns home. A registry record may
declare short aliases, so /cli grok and /cli grok-build reach the same backend. Existing model/effort/access defaults
are preserved throughout setup and recovery. Opening menus pauses passthrough
without starting a second agent. The controller rechecks readiness on activation.

Menu colors and bold headers are supplied by the inline view renderer, not
syntax highlighting or branding metadata. Other chat messages remain ordinary
chat. See the presentation guide for the exact palette and fallback behavior.

## Routing-mode menu

The Change routing mode menu choice uses controller `mode` to show one settings page:
1. Passthrough, 2. Direct, X. Back. A selection runs `mode --choice` with the
selected name. X runs `mode --dismiss`, never `off`. This page uses `modeMenu`
without replacing provider `pending` settings. Opening, selecting and dismissing
it preserve activation, the provider session, model, effort, access and running
work. This is the exception to the general menu-exit shutdown rule above.
Use the shared numbered choices, current-choice marker, colors, typography and
Exit footer. Both standalone and setup entry points use the same menu renderer.

## Unified settings while active

With an active agent, `/cli mode`, `/cli menu` and `/cli model` without a choice
(and their `$` equivalents) all run controller `settings --menu-output` and show
the second-page Agent Settings view for the bound agent. It shows the accepted
model, effort, access and Passthrough/Direct mode using the shared renderer.

1 changes model, 2 effort and 3 access through `tune --phase` and the existing
options/activation verification flow, preserving the same provider session.
4 opens controller `mode` for Passthrough/Direct; selecting or dismissing it
returns the settings page. 5 toggles activity progress. X runs `settings --dismiss`, closing only the
page and leaving the CLI active. `/cli stop` remains the explicit shutdown.
Opening this page never creates a session or applies provider settings.

Explicit `/cli model <choice>` and `/cli mode <choice>` retain direct setting
behavior while active. When inactive, all three commands, including explicit
choices and `$` forms, reply exactly `CLI-MODE: Agent not activated. /CLI to setup`.
They do not open a menu or apply a setting. `/cli` itself remains agent selection.

All phase choices and navigation use the controller transaction commands in [controller](controller.md#menu-transactions-v108). X in active tuning closes settings and preserves the agent. B returns to Agent Settings; activation B returns to the previous phase.

## Shared menu transactions

Use controller `options`, `choose`, `navigate` and `refresh` as specified in the
[shared controller contract](controller.md#menu-transactions-v108).
Phase Back descriptions above apply to initial activation. During active tuning,
B returns to Agent Settings and X closes settings while preserving the agent.
Refresh uses owned-session metadata only; it does not open an inspector or switch
models to discover effort lists. Report cached/unsupported refresh explicitly.
