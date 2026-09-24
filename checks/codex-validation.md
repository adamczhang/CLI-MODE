# Codex CLI sixth-backend validation — 2026-09-22

Installed standalone Codex CLI 0.155.1 through npm. Existing ChatGPT sign-in
was retained. Codex is registry/menu option 6 and supports /cli codex and
/cli bind codex. Local plugin: 0.1.8+codex.20260922122013.

## Verified

- 341 offline tests passed from source and an independently extracted package.
- Plugin and skill validators passed. Package contains six registered backends.
- Live model metadata was collected after selecting each of five advertised
  models; effort menus preserve model-specific choices.
- Three connected research prompts retained the marker and saved the requested
  checklist. Close/reopen and final owned-session shutdown passed.
- Direct-mode activation, idle expiry/reconnect with memory, allowed file write,
  effort change, model change and memory after tuning passed.
- /d /status completed inside the same ACP conversation. Unknown commands were
  rejected before dispatch and ownership was preserved. Follow-up memory passed.
- Stop during an in-flight turn settled the submitter and confirmed shutdown.
- Fixed PowerShell 5.1 misclassifying successful login-status stderr as failure;
  regression tests exercise both success and failure exit codes.

## Supported limits

Full access is the only Codex access option. The native read-only / Ask for
approval preset wrote a workspace file without an ACP approval callback during
live testing. Prompt is therefore removed, including from stale cached catalogs
and saved dispatch state. Automatic review is not an edits-only policy, so
Auto-edit is also rejected. The failed permission probe and successful supported
rerun are retained as evidence rather than calling all native presets safe.

Quota retrieval at activation is not implemented. Native /status may report
account limits, but there is no structured matching-account quota helper yet.
Desktop hook trust, actual inline UI rendering, all native commands and every
model/effort combination are not certified by these controller tests.

## Evidence

Evidence root: %USERPROFILE%/.codex/validation/cli-mode/2026-09-22

- codex-live/summary.json: three-turn research, artifact, close/reopen.
- codex-controls-verified/summary.json: failed Prompt enforcement probe.
- codex-controls-supported/summary.json: supported Direct controls and cleanup.
- codex-offline-tests.log and codex-package-tests.log: 341 passing tests each.

The catalog-only probe was closed. No release tag, GitHub release or committed
v0.1.8 archive was changed; this is a local development update.

## Unified settings follow-up

343 tests passed from source and the extracted package after unifying active
/cli mode, /cli menu and /cli model (and $). All six agents preserve activation,
settings and sessions when opening/closing this page. Model, effort, access and
routing choices dispatch locally; X/Done dismiss without stopping. Inactive
commands return exactly `CLI-MODE: Agent not activated. /CLI to setup`, including
explicit choices. Logs: unified-source-tests.log and unified-package-tests.log
in the evidence root. Installed update: 0.1.8+codex.20260922122902.
