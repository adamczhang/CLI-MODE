# Backend contract, version 1 (validated descriptors)

CLI-MODE is the plugin and its shared controls. The only discoverable skill is `cli-mode`. Each provider has an internal
`backends/<id>/backend.md` guide, selected through the menu or `/cli <id>`.
Backend guides are supporting resources, not separately invocable skills. The catalog in `backends.json` is a discovery registry
read by the host; it is not an executable router or a plugin hook.

## Shared responsibilities

The shared skill owns /cli agent selection and shared first-time checks,
/cli bind agent, /cli stop, /cli model/effort/access/permissions tuning,
/help, and conversation-scoped
mode state, intact whole-message forwarding to one main session, native Codex
subagent suppression, and public progress/recovery conventions. Backends decide
their own internal delegation; host worker pools are not supported.
Provider adapters own model names, effort IDs, access mapping, quota
provider, authentication strategy, executable discovery and direct-message banner.
The shared state/command parser and hooks must not branch on task modality.

Only a `/d` or `$d` prompt reaches the agent. Those prompts and controller file
input converge on the same captured-request admission and dispatch lifecycle.
ACPX's pinned public runtime owns protocol handling, reconnect and canonical
completion. Adapters must not add transport retries, fresh-session fallbacks,
RPC response correlation or exit-code workarounds around that runtime.
The shared controller permits ACPX's own empty-session recovery for the first
readiness prompt, before any user task. It pins the resulting provider identity
before settings and uses strict shared-runtime resume afterward. This startup
rule belongs to the shared transport, not a provider adapter.

Keep one active backend per host conversation. Backend switching is an explicit
control/activation operation. Settle or cancel and close the outgoing backend's
owned sessions before switching. Preserve the old backend label on its history;
do not imply another provider inherited its context. Do not silently append a handoff brief to user prompts. If new activation fails, report inactive/pending
state explicitly instead of silently reverting or claiming the new backend works.

## Backend responsibilities

Each backend guide must define:

- A backend frontend command, implementation of shared `/cli stop` shutdown,
  ACPX profile, and prerequisites/login verification. New backend frontends must
  not redefine the shared agent-selection, help, tuning or shutdown commands.
- A model -> effort -> access setup flow with native IDs, valid combinations,
  defaults, and fresh-versus-cached metadata semantics.
- How access maps to both provider controls and host permission policies.
- Persistent session identity, readiness evidence, idle lifetime, shutdown, and
  reconnect behavior. Catalog checks must not launch another agent alongside the main session.
- The exact direct-request announcement and delegation capabilities/limitations.
- Quota retrieval and account attribution, or an explicit unavailable result.
- Portable scripts/resources using relative paths and command discovery, with
  runtime secrets and session state outside the distributable package.

Use only capabilities actually supported by that provider. Do not copy AGY's
`mode=yolo`, internal OAuth handling, combined effort IDs, or its `/usage`
transport into another backend without verification. Claude Code, for example,
exposes model and effort as separate selectors, maps `allow` to
`bypassPermissions`, needs no separate ACP runtime download, and expands slash
commands inside the same ACP session, and Grok Build advertises no
permission-mode selector at all, so it offers only the access levels its
runtime can actually honor; Cursor advertises opaque bracketed model IDs with
no effort control; and Copilot advertises neither a model catalog nor an effort
control, but does expose a real `allow_all` permission selector.

A phase with nothing to choose shows one explicit Provider default row. A
setting a backend never requested is never verified and never reported as
applied. Access is presented in one shared order, allow first, regardless of
the order a runtime advertises its modes in.

A backend must not assume its catalog is short. Menus are capped at ten
selectable rows and paginate through the shared formatter, so a long or
plan-dependent model list stays readable without a backend-specific menu. The shared skill supplies the behavior;
the backend supplies its implementation and provider-specific exceptions.

## Adding a backend

1. Add `backends/<id>/backend.md` inside the plugin. Do not add another
   `SKILL.md` or skill UI metadata for a provider.
2. Link `../../codex/skills/cli-mode/SKILL.md` from its guide and meet the responsibilities
   above. Keep its scripts, assets, and provider references inside that folder.
3. Add one registry record. `entrypoint` is relative to the registry directory
   and must stay inside the plugin. Do not add placeholders for unimplemented
   backends or borrow another backend's account/session.
4. Implement and register an executable adapter alongside `scripts/agy.py` with
   catalog selection, profile/setting mapping, native-setting verification and
   provider conversation identity. Inherit the shared ACPX runtime transport,
   public relay and owned-session shutdown. Implement `validate_prompt` for
   provider-specific saved-access restrictions; it runs before both preflight and
   launch, including runtime bridge submissions. Identity must use the actual
   provider conversation ID, not ACPX's durable record ID.
   Extend controller activation and hook
   frontend recognition deliberately; a registry record alone does not implement
   another runtime. Register the adapter module in `scripts/adapters.py`; an
   unregistered backend raises rather than falling back. Never send one
   backend's saved state through another provider as an implicit fallback.
5. Add behavioral tests and acceptance scenarios and run skill/plugin/package validation. The package
   builder includes every registered backend without a provider-specific build step.

Plugin discovery may namespace skill names, but the user-facing controls remain
/cli (or $cli), /cli backend, /cli bind backend, /cli stop and shared tuning.
Extend the command parser intentionally when adding a backend; do not add
backend-specific shutdown or direct-message aliases.

The controller persists conversation/workspace state; bundled prompt and session
hooks restore routing instructions in the host. Tool hooks guard native worker
calls. Hook installation/trust and real host execution must be verified separately.
The plugin does not implement a background scheduler or force state across unrelated
Codex conversations. Dependencies such as ACPX and provider runtimes remain
separately installed; packaging does not install or sign in to them.

New backends register their menu label, guide entrypoint, prerequisites, effort
representation, command transport, quota availability and refresh strategy in
backends.json. adapters.descriptor validates registry/adapter parity and required
callables. Shared menus render structured settings fields, never adapter menu prose.
Keep platform-specific install/auth implementations in setup.ps1 and test their
registry coverage. The shared frontend and doctor derive prerequisite probes from
the registry; no additional provider switch belongs in those modules. Confirmation is
stored per backend; never treat one backend's setup receipt as proof for another.
