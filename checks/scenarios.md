# Current acceptance scenarios

Run the offline suite and fresh-package smoke test. Historical reports document
older snapshots. Live account and installed-host checks remain separate.

| Scenario | Expected result |
| --- | --- |
| /cli or $cli, including mixed case/leading whitespace | Shared menu; no activation. |
| /cli agy or $cli agy | AGY page; first-time check shown until confirmed. |
| /cli bind agy | Verify prerequisites, hooks, settings and readiness, then activate saved defaults without a second menu selection. |
| Missing ACPX/AGY CLI or failed readiness | Clear blocker; no false activation or implicit installation/sign-in. |
| First-time check passes | Persist receipt; hide F across both menus and conversations. |
| /cli model Gemini 3.1 Pro | Resolve cached family, preserve supported effort/access, apply to the same session. |
| Model has no current effort | Ask for a supported effort; do not silently choose another. |
| /cli effort low | Use current family's advertised Low ID; retain access. |
| /cli access allow or $cli permissions YOLO | Same cached access choice; preserve model and effort. |
| Empty tuning command | Relevant menu, saved snapshot, no runtime change. |
| Ambiguous or unsupported plain text | Show matching/available choices; no invented selection. |
| R in model menu | Refresh advertised choices; never launch an inspector alongside the main agent. |
| /help | Shared styled inline help with header divider; preserve mode and pending setup; disclose text fallback if necessary. |
| Any menu, including generated choices and disambiguation | Unhighlighted ASCII frame, at most 40 source characters per row including border; long labels wrap without truncation. |
| Long public progress | Coherent paragraphs/lists, one attribution per update, natural viewport wrapping; never pad fragments or expose private reasoning. |
| Styled menus and two voices | Inline menu uses the documented palette; activation/passing use accent, provider text uses default colour with accent attribution. Disclose any text-only fallback. |
| Bare commands or help | Ordinary task text while active; ordinary host message while off. |
| Unknown /cli control while off | /cli to activate.  Say /help to see options |
| Unknown /cli control while on | Say /help to see options. No forwarding. |
| Tuning while off | Same activation/help hint; never starts an agent. |
| /cli agy task or /cli stop extra | Invalid control; appended prose is not dispatched. |
| /client, quoted controls or controls inside prose/code | Ordinary content, no control action. |
| Old /direct and bypass/off triggers | No special meaning; Passthrough treats them as ordinary input. Direct reserves only the complete /d and $d tokens. |
| Ordinary prompt, whole game, image generation or parallel-work request | Entire original text goes once to the one main session. No host decomposition, inference fallback or extra agent workers. |
| Steering message while ACPX is running | Queue exact text behind the active turn; do not cancel or live-inject it. The provider turn continues after the Codex response ends. |
| Routing choice changes with queued work | Existing requests use their captured Direct/Passthrough choice; the new choice affects later messages. |
| /cli cancel with queued follow-up | Cancel the active turn explicitly, settle it, then drain queued follow-ups. |
| Dead submitter with uncertain outcome | Halt queue; inspect and acknowledge the uncertain operation before `resume`. Never replay it automatically. |
| Setup reply | Saved phase handles it; never forward as task work. |
| Raw tool payloads, thinking tools, private thoughts and stderr | Excluded from public relay/logs. |
| ACPX tool activity | Public kind/title, lifecycle state and file locations; duplicate snapshots suppressed, partial metadata retained, late updates cannot reopen settled tools. |
| /cli progress quiet or $cli progress activity | Persist display preference for subsequent turns without provider calls, session changes or prompt replay; Direct and Passthrough share the same relay. |
| Usage bursts | Latest reported context, token breakdown and cost; coalesce bursts, never infer subscription quota. |
| Activity snapshot from a growing public log | One latest row per tool, unfinished tools first, at most 20 displayed rows; ignore an unfinished last JSONL line. |
| Malformed/Unicode activity metadata | Allowlist fields, bounded text/locations, preserve complete Unicode characters, escape HTML; no raw input/output or reasoning fallback. |
| Public plans/messages | Coalesce fragments, suppress identical plan snapshots, relay in chat attributed to the bound provider. |
| No public progress | Factual wait update; no invented activity or additional provider prompts. |
| Image/artifact response | Present available artifact; no claim of delivery if inaccessible. |
| Attachment only visible in Codex | Report missing agent-readable path; sender remains text-only. |
| Provider tests/completion | Attribute to the bound provider; no claim of host verification. |
| Compaction during help/setup | Preserve pending phase and displayed snapshot. |
| Compaction after dispatch | Observe saved operation/events; never replay original request. |
| Native Codex subagent call while active | Deny covered native calls; unrelated connectors are not matched. |
| /cli stop during setup or work | Gate immediately, cancel/close owned sessions, preserve history; incomplete cleanup is reported. |
| Repeated stop | Harmless, never starts an agent. |
| Failed setting change or leftover legacy workers | Gate dispatch until successful cleanup. |
| Stop races a late session ensure | Inflight lifecycle cleanup closes late owner; cannot reactivate after stop. |
| Idle expiry | Routing stays active; resume same saved session and report identity/context changes. |
| Different conversation/workspace | Isolate state; workspace rebinding only after complete stop/cleanup. |
| ZIP extraction with empty data/home | No bundled runtime state or credentials; same offline behavior. |

Plain-language choice interpretation is a host-skill responsibility, not a Python
natural-language classifier. Tests verify routing, saved choices, resolved native
IDs, gates and session reuse. Live Codex adherence needs installed trusted hooks.

| Supported native slash command while active | Antigravity closes ACP once and discloses fresh native context; the other four expand advertised commands in the same ACP conversation. Retain exact saved identity for follow-ups. |
| Unknown /commands or unavailable terminal command | Explain unavailability without dispatch or session changes. |
| Successful activation presentation | Green monospace, CLI-MODE Activated only, blank line, bold labels, Question: /help, Weekly aligned beneath Five hour. |

## Guided Windows setup

- Fresh machine without Python/PowerShell 7: PowerShell 5.1 scan still runs.
- Missing components: display exact list; no installs/sign-in until approval.
- Approved install: visible popup, one installer at a time, track status in task.
- Native/ACP sign-in: verify structured account/protocol results, never model claims.
- Failed/closed popup: Retry, manual/separate installation, Exit; rerun /cli resumes.
- Completed setup: rescan and display Agent Settings, without starting agent work.
- Stop during installation: gate task immediately; cancellation takes effect between
  package operations and does not claim an active external installer has stopped.
- Custom ACP launcher: preserve configuration; direct user to its separate sign-in.

## Claude Code backend

| Scenario | Expected result |
| --- | --- |
| /cli on a fresh task | Agent menu lists all five registered CLIs, including Claude Code. |
| /cli claude or $cli CLAUDE | Claude Code page; first-time check shown until confirmed. |
| /cli bind claude | Verify prerequisites, hooks, settings and readiness, then activate Opus 5 / High / Bypass permissions without a second menu selection. |
| /cli claud or /cli bind claud | Inactive hint; a partial agent name is not a control. |
| Antigravity receipt exists, Claude does not | Claude still shows its own first-time check; one receipt never vouches for another. |
| Owned Antigravity session, then /cli bind claude | Refuse with "Run off successfully before activating another backend"; no second session. |
| /cli effort xhigh on Claude | Apply the advertised `effort` selector, preserving the model value and access. |
| /cli access prompt on Claude | Map to native `default` (Manual) and drop host `--approve-all`. |
| /cli access plan or auto on Claude | Not an advertised CLI-MODE access level; show options rather than substituting a mode. |
| /cli model sonnet then verify | Accept the rotated ACP session id without reporting context loss; the record id is unchanged. |
| A model switch adds a `fast` option | Verification still passes; only the requested options are checked. |
| Claude slash command, list not yet observed | Dispatch in-session; never announce a fresh conversation. |
| Claude slash command outside an observed list | Refuse before dispatch; not forwarded as model text. |
| /clear, /login or /todos on Claude | Refuse before dispatch as terminal-only, without an observed list. |
| Claude turn emits reasoning chunks | Excluded from the relay and public log, like Antigravity. |
| Activation confirmation | Utilization from the zero-turn /usage local command, percent used as reported, ISO reset countdowns. |
| Quota query fails or returns no rate limits | `Utilization: unavailable (reason)`; activation is still reported honestly. |
| An API key outranks the subscription | Report that the key is billing rather than showing an empty utilization block. |

## Controls and help

| Scenario | Expected result |
| --- | --- |
| Every control under `/` and `$` | Identical routing: menu, bind, stop/off, model/effort/access/permissions and help. |
| `$help` | Opens CLI-MODE help, same as `/help`. |
| `?cli`, `?help` | Not controls; ordinary text. `?` is not an accepted prefix. |
| `/commands` | Not a help alias; ordinary text unless a provider advertises it. |
| `/client`, `$helpme`, `/clip art` | Ordinary text; a complete token is required. |
| `/cli <agent> <task text>` | Hint, not activation: menu and bind take no appended task text. |
| `/help` rendering | Sectioned list; every row fits the 40-character frame and exactly one `X. Exit` row. |

## Grok Build backend

| Scenario | Expected result |
| --- | --- |
| /cli on a fresh task | Agent menu lists all five registered CLIs, including Grok Build. |
| /cli grok, /cli grok-build or $cli GROK | Same Grok Build page; the alias and canonical ID are equivalent. |
| /cli gro or /cli grokbuild | Inactive hint; neither is a registered word. |
| /cli bind grok | Verify prerequisites, hooks, settings and readiness, then activate Grok 4.7 / High / Always approve. |
| /cli access auto-edit on Grok | Refuse with the reason that no permission-mode selector is advertised; never substitute allow or prompt. |
| /cli access prompt on Grok | Drop host `--approve-all` and use the ACPX default permission flow. |
| /cli effort xhigh on Grok | Apply the advertised `reasoning_effort` selector, preserving the model value. |
| Activation verification | Check model and reasoning_effort only; never claim a permission mode was applied. |
| Activation confirmation | `Utilization: unavailable (Grok Build exposes no subscription quota windows)`. |
| Per-session token/cost figures | Never presented as subscription quota. |
| Image attachment with Grok active | Report that this runtime does not accept image input rather than implying delivery. |
| Owned Claude or Antigravity session, then /cli bind grok | Refuse until off/cleanup succeeds. |
| Grok slash command, no observed list | Dispatch in-session; never announce a fresh conversation. |

## Menu option cap and paging

| Scenario | Expected result |
| --- | --- |
| Any menu, any agent | At most ten selectable rows, counting Back, Refresh, Exit and paging rows. |
| A catalog that fits | No paging rows at all. |
| Cursor model phase | Pages with `> Next page (2 of 8)`; page 1 has no Previous, the last page has no Next. |
| Paging through every page | Each model appears exactly once, in advertised order. |
| Item numbers | Continuous across pages; page 2 starts at 6, not 1. |
| `B. Back` on page 3 | Returns to the previous phase, not page 2. |
| Requested page out of range | Clamps to the first or last page rather than failing. |

## Cursor backend

| Scenario | Expected result |
| --- | --- |
| /cli or $cli cursor | Cursor Code page; first-time check until confirmed. |
| /cli bind cursor | Activate Auto / Provider default / Always approve after all checks. |
| /cli model composer-2.5 | Refused as ambiguous: a bracketed variant is advertised, so the full ID is required. |
| /cli effort high on Cursor | Refused: this runtime advertises no reasoning-effort control. |
| Effort phase on Cursor | One explicit Provider default row; no invented levels. |
| /cli access auto-edit on Cursor | Refused with its reason; never substituted. |
| /cli access plan or ask | Not access levels; the interaction mode is pinned to agent and not exposed. |
| Activation verification | Checks the accepted model and the pinned agent mode only. |
| Activation confirmation | `Utilization: unavailable (Cursor exposes no subscription quota command)`. |
| /cli model after activation | Rotated ACP session id is not reported as context loss. |

## GitHub Copilot backend

| Scenario | Expected result |
| --- | --- |
| /cli or $cli copilot | Copilot page; first-time check until confirmed. |
| /cli bind copilot | Activate provider default / provider default / Allow all after all checks. |
| Model phase on Copilot | One explicit Provider default row; no model is set or verified. |
| Effort phase on Copilot | One explicit Provider default row. |
| /cli model gpt-5.4 on Copilot | Refused: this runtime advertises no model catalog over ACP. |
| /cli access allow then verify | Native `allow_all=on` applied and read back. |
| /cli access auto-edit on Copilot | Refused: `allow_all` is on/off with no edits-only value. |
| Activation confirmation | Model and Effort read Provider default; no specific model is ever named. |
| Utilization | `unavailable (Copilot exposes no subscription quota command)`. |

## Frontend consistency

| Scenario | Expected result |
| --- | --- |
| Access phase, any agent | `allow` is listed first, then auto-edit and prompt where advertised. |
| An access level a runtime lacks | Absent from the menu and explained in that backend's catalog. |
| Activation menu, any agent | Same structure: CLI-MODE / Agent Settings / display name / Model / Effort / Access / 1 Yes / 2 Change. |
| Any phase with no advertised choice | One explicit Provider default row, never invented options. |
| Any rendered menu | At most ten selectable rows, 40-character frame, exactly one `X. Exit`. |

## Live validation regressions (2026-09-22)

| Trigger | Required result |
| --- | --- |
| Repeated Windows PATH refresh | Deduplicate directories; repeated calls do not grow PATH. |
| Activation ends with an upgrade refusal | No fresh readiness marker means routing stays inactive and owned cleanup is attempted. |
| Agent reads a missing file before successfully creating it | Correlated file-RPC errors do not become prompt failures. Unknown/prompt errors remain fatal. |
| Prior denied write followed by a successful read-only turn | ACPX historical exit 5 does not poison a clean completed turn. |
| Prior denied write followed by a successful file write | Recovery requires a correlated successful file response; unresolved/denied writes remain failures. |
| Cursor Prompt, including an old cached catalog or saved session | Refuse before dispatch: native writes bypass this restriction. Allow is the supported choice. |
| Antigravity Auto-edit, including an old cached catalog or saved ACP session | Refuse before dispatch: ACPX client permissions block file writes. Never substitute Allow. |
| Commands advertised only in persisted ACPX metadata | Capture the catalog before validation; reject unknown commands. |
| Advertised command completes without a public message | Return noPublicOutput explicitly. Ordinary prompts and readiness still require public output. |

## Independent routing-mode settings

| Trigger | Required result |
| --- | --- |
| /cli mode or $cli mode, on or off | One page with Passthrough, Direct and X. Preserve activation, session, model, effort, access and other pending settings. |
| Select either mode while a CLI task runs | Save routing only; do not cancel or reconfigure current work. |
| X in the routing-mode menu | Close only that menu. Do not run off. |
| Plain prompt in Direct mode while CLI is on | Handle in Codex; no provider call. |
| /d task or $d task in Direct mode | Send to the same active CLI, removing the trigger and one separator only. |
| /d alone or /d while CLI is off | Explain missing task or activation; do not dispatch or activate. |
| /debug, quoted /d or /d later in prose | Ordinary host content in Direct mode. |
| /d /context in Direct mode | Validate and dispatch the provider command after removing the direct prefix. |
| Stop, restart or compact | Preserve routing choice; retain host/delegated distinction; never replay a dispatched task. |
| Mode changes while dispatch is still doing preflight | Reject stale dispatch without affecting activation/session. |

## Codex CLI (sixth agent)

- `/cli` lists Codex CLI as option 6; `/cli codex` and `$cli bind codex` resolve correctly.
- Setup checks standalone CLI and login status, including successful status on stderr.
- Model then model-specific effort then native access are applied and read back.
- Direct and Passthrough preserve the same ACP session; `/d /status` stays on ACP.
- Codex rejects Prompt and Auto-edit; Allow writes within the isolated test workspace.
- Three connected research turns retain context, save an artifact and close/reopen.
- Idle restart, model/effort tuning, cancellation and final shutdown preserve ownership.
