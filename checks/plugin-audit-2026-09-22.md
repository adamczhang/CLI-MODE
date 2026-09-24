# Six-backend plugin audit — 2026-09-22

## Scope and result

Audited the current working tree, including the uncommitted Codex backend and unified settings changes after commit `5195070`. This is not a certification of the published v0.1.8 archive. No runtime source, settings, installed plugin, tag or release was changed during this audit.

The architecture is viable: one shared controller, conversation-scoped state, a shared ACPX transport base, explicit adapter registration, shared presentation, and backend-owned capability verification are good boundaries. Six providers already have genuinely different capabilities; preserving those differences is correct. Fix the defects below before expanding the backend list. An incremental refactor is sufficient.

Validation: all **343 existing offline tests passed** in 42.208 seconds. Additional isolated probes reproduced the findings below using temporary state and a fake provider. They made no paid model calls. Existing live validation reports provide historical evidence; this audit did not repeat six-provider live authentication, subscription, native-command or desktop hook/UI testing.

## Confirmed defects

### 1. P2 — Effort menus ignore the selected draft model

Location: `plugins/cli-mode/scripts/controller.py:752–758`, `frontends.py:149–177`.

The `options` command always passes `saved_settings` to `phase_menu`, ignoring `pending.draft.settings` and the draft catalog snapshot. Reproduction: activate Codex with Astra, begin model tuning, save Luna in the draft, then request effort options. The implementation supplies Astra's Low/Medium/High/Xhigh/Max/**Ultra** list; Luna's catalog stops at Max. The later selection validator can reject the invalid combination, but the menu has already offered an impossible choice. Re-reading the catalog also undermines stable numbering during a pending setup flow.

Fix: resolve effective settings from the pending draft for the matching backend, falling back to saved settings; use one persisted catalog/choice snapshot throughout a menu transaction. Refresh should explicitly replace that snapshot and invalidate incompatible drafts. Add a behavioral test that selects a model with a different effort set before advancing.

### 2. P2 — Opening settings during preflight does not prevent dispatch

Location: `plugins/cli-mode/scripts/controller.py:329–332`, `454–473`, `602–630`.

`send` checks pending menus initially, then performs metadata/preflight operations. `prompt` checks active/generation and routing mode under the final dispatch lock, but does not recheck pending settings for ordinary sends. Opening Agent Settings keeps active and generation unchanged.

Reproduction: open settings from a fake provider's metadata callback after the initial send check. The prompt is dispatched and echoed while state still has `pending.phase == settings`. This violates the intended setup gate under concurrent control/prompt operations.

Fix: recheck all normal-dispatch admission conditions under the final lock, including pending setup, while retaining the explicit activation-token path used for readiness prompts. Test opening settings, starting tuning and switching routing between preflight and dispatch.

### 3. P2 — Empty command-capability updates cannot clear cached commands

Location: `plugins/cli-mode/scripts/native_commands.py:78–88`; `observe` in Claude, Codex, Cursor, Copilot and Grok adapters.

`observed` returns `names or None`; each adapter also uses `if names`. A valid `available_commands_update` with an empty list is therefore discarded. All five adapter probes returned None for that event. When streamed updates are the capability source, previously advertised commands can remain admitted after the provider withdraws them. Persisted metadata containing an empty list is handled correctly, but does not repair the stream fallback itself.

Fix: distinguish “no update” (None) from “known empty capabilities” ([]), at both extraction and adapter layers. Test a nonempty update followed by an empty update and verify the former command is refused.

### 4. P3 — Antigravity effort menus omit the current marker

Location: `plugins/cli-mode/scripts/frontends.py:187–197`, `agy.py:49–64`.

The shared renderer looks for `settings.effortValue`, while Antigravity stores effort in its combined model ID and display label. With the default Gemini Flash High selection, model and access each have a current marker, but effort has none. Other combined-model variants also need family-aware model marking rather than comparison with the family's first effort ID.

Fix: expose canonical selected model-family and effort choice keys from every adapter; render markers from those keys. Preserve native IDs for runtime calls.

### 5. P3 — Agent selection does not honor pagination

Location: `plugins/cli-mode/scripts/frontends.py:101–121`.

Both onboarding and normal agent pickers enumerate every backend directly and ignore `page`. With a synthetic 12-backend registry, pages 1 and 2 were identical and displayed 12 numbered rows plus Exit, violating the ten-option menu contract. The current six-agent list fits; this is a verified growth limitation.

Fix: build both agent lists through the same pagination helper as model choices, and retain the displayed backend-ID mapping. Add a registry-size test above the limit.

## Menu and capability consistency

| Area | Audit assessment |
|---|---|
| `/cli` and backend selection | Six registry entries and explicit adapters; no silent fallback to another provider. Agent picker needs pagination. |
| Active `/cli mode`, `/cli menu`, `/cli model` and `$` aliases | Shared settings route exists. Inactive variants return the required activation hint. |
| Settings display and routing selector | Shared frame/inline renderer; opening/closing settings preserves active configuration. Final dispatch race needs fixing. |
| Model, effort and access phases | Shared paginated choice renderer; draft selection and Antigravity current markers need fixing. |
| Unsupported effort controls | Explicit Provider default rows are appropriate for Cursor/Copilot. Do not invent equivalent effort settings. |
| Direct routing | Complete `/d` or `$d` token is stripped once before backend command handling; ordinary host turns remain local. Covered by existing tests. |
| Native commands | Antigravity has an explicit native handoff with context-loss notice; other adapters use ACP command admission. Empty capability updates need fixing. No claim that every native terminal command works through ACP. |
| Lifecycle | Shared ownership, readiness verification, cancellation and shutdown paths; existing test coverage passes. Real account restrictions remain backend-dependent. |
| Installation, diagnostics, docs | Installer and per-backend checks cover six; diagnostic tool inventory and some workflow instructions lag the expanded architecture. |

Catalog access choices are intentionally different:

| Backend | Exposed access |
|---|---|
| Antigravity | Allow, Prompt |
| Claude Code | Allow, Auto-edit, Prompt |
| Grok Build | Allow, Prompt |
| Cursor | Allow only |
| GitHub Copilot | Allow, Prompt |
| Codex | Allow only |

These differences should remain explicit. Cursor/Codex restrictions reflect prior enforcement validation, not merely missing menu entries.

## Architecture recommendations

1. **Make the backend contract executable.** Add a small validated descriptor/Protocol for identity, prerequisite probes, catalog source, model/effort representation, access policies, slash-command handling, quota availability and transport strategy. Check registry/adapter parity automatically. Keep provider-specific behavior inside adapters; preserve the existing shared ACPX base.
2. **Use structured menu data.** `active_settings_menu` currently splits adapter activation text at `\n\n1.`, and initial setup inserts routing text by replacing a literal English label. Return fields/actions from adapters and render them centrally. A wording change should never remove settings or break menu construction.
3. **Centralize menu transactions.** Code owns some numeric replies, while skill/backend prose owns others, draft interpretation and refresh procedures. Put phase, parent page, snapshot, selection and cancel action in one controller transaction model. Keep natural-language interpretation in the host, but validate every chosen action in the controller.
4. **Reduce repeated backend facts.** IDs and capabilities recur in the JSON registry, adapter map, prerequisite maps, PowerShell installer branches, catalogs, activation assets, help and documentation. Consolidate machine-readable facts and validate necessary platform-specific branches. Avoid a giant generic adapter that erases provider differences.
5. **Standardize refresh.** Expose one controller refresh operation with backend implementations, explicit cached/live status and owned-session rules. Currently refresh depends on backend-specific instructions, with an Antigravity helper and prose-driven metadata handling elsewhere. A visible Refresh choice should have a deterministic implementation and failure path for every backend.
6. **Clarify navigation semantics.** X closes Settings and Routing, but stops CLI-MODE in tuning submenus. This follows current instructions, yet the identical “Exit” label conceals different consequences. Use explicit Close/Back/Stop labels and one documented return path. Shared instructions say B returns to Settings during tuning, while backend phase descriptions also say B returns to the previous phase; distinguish activation from active tuning in code and docs.
7. **Separate onboarding from backend availability.** `first_start(home)` treats any unconfirmed backend as a new user. A person using only one CLI can repeatedly see “New User Detected,” and adding a backend recreates that state. Keep a host onboarding status plus per-backend readiness indicators. The normal home menu's “First Time User Check” branch is effectively preempted by this earlier condition.
8. **Extend diagnostics from registry data.** `doctor.py:23` inventories ACPX, PowerShell, Antigravity and Python only. Include all registered backend prerequisites and report selected-backend readiness without requiring every provider to be installed.

## Recommended implementation order

1. Fix draft/snapshot resolution, final dispatch admission and empty capability updates; add regression coverage for the reproduced failures.
2. Normalize current-selection keys; paginate agent lists; make navigation consequences explicit.
3. Introduce validated backend descriptors and structured menu transactions, then migrate refresh and diagnostics.
4. Repeat source/package checks plus targeted live provider tests for affected capabilities, then verify actual Desktop menu rendering and hook routing. Use free/read-only checks where subscriptions block live work, and report those gaps explicitly.

The existing 343-test suite is useful but is not an exhaustive menu-state or concurrency matrix. Add tests for state transitions and differing capabilities, rather than only expected text or a fixed list of six names. Keep the published v0.1.8 artifacts immutable; release the current development changes under an intentional subsequent release when ready.
