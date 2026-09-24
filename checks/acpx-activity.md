# ACPX activity improvements — 2026-09-22

This extends the completed [core hardening](acpx-hardening.md) on `main`, based
on `cffecf7`. It describes source development; the installed plugin and released
`dist` archives have not been changed.

## Implemented

- One public activity projection for all six ACPX backends, through the existing
  prompt lifecycle in Direct and Passthrough. Direct remains the routing default.
- Tool kind/title, stable tool identity, pending/running/completed/failed state,
  and up to five reported file locations. Partial updates retain prior metadata.
  Late notifications cannot reopen or reverse a settled tool.
- Reported context usage, token breakdown and cost when emitted. These are
  provider reports, not subscription quota or independently measured usage.
- Duplicate suppression, immediate lifecycle transitions, 500 ms coalescing for
  metadata/usage bursts, and final flushing before canonical turn completion.
- Persistent `/cli progress activity|quiet` and `$cli` aliases, plus a settings
  toggle. Activity is the display default. The setting is sampled at dispatch,
  survives stop/restart, and sends no provider command or extra prompt.
- Deterministic inline activity snapshots with a plain-text fallback, one latest
  row per tool, unresolved tools first, at most 20 displayed rows, and omitted-row
  counts. Growing logs tolerate an unfinished last line. A finished turn does
  not imply every tool completed.
- Both the bridge and controller allowlist fields. Raw tool input/output/content,
  thinking tools, private reasoning and arbitrary metadata remain excluded.
  Execute/unknown tool titles use generic labels rather than command arguments.
  ANSI/control/bidi characters are removed; Unicode truncation preserves complete
  characters. HTML is escaped. Tool tracking is bounded without evicting identity.

Public agent messages, plans and artifacts keep their existing semantics. Text
fragments are never deduplicated. Activity alone cannot establish turn success;
ACPX's canonical result and complete output observation are still required.

## Evidence

`test_activity.py` covers lifecycle ordering, interleaved/partial tool updates,
malformed data, privacy, Unicode, burst coalescing, quiet mode, bounded state,
preference persistence, hook routing/compaction, and read-only rendering.

Two additional real pinned-runtime tests use the offline ACP fixture:

- Activity reaches the bridge before its canonical result, preserving file
  locations and excluding private payloads and command arguments.
- Direct activity output and Passthrough quiet output share the same provider
  session. Logs match the public stream, and two requests increment the provider
  prompt count exactly twice.

- Full source suite before the final Unicode edge fix: **426 passed** in 181.345 s.
- Final targeted activity suite: **12 passed**, including the reproduced Unicode
  boundary and safe HTML rendering regression.
- Final fresh-package suite: **428 passed**, **zero skips**, in 183.265 s,
  including **16 actual pinned ACPX runtime tests**.
- All **81 archive files** match normalized current plugin source bytes.
- Plugin/skill validation, Python compilation, Node syntax, `git diff --check`
  and LF/CRLF package reproducibility passed.
- Final development archive SHA256:
  `5817d6f426063b35107a46c1a1094311a6e677b93e160da90222afad6e0d1017`.

[Machine-readable evidence](acpx-activity-evidence.json) records the archive and
fresh-extraction evidence paths, exact test counts and named real-runtime cases.

## Boundaries

Actual chat cadence still depends on host polling/rendering. Views are inline
snapshots, not native Codex tool cards or guaranteed edits to old chat messages.
The browser URL policy blocked the local-file visual preview; HTML escaping,
structure, row replacement and CLI rendering were tested programmatically.
Actual installed-plugin rendering/hook trust and live provider accounts were
not exercised by the offline fixtures.

Antigravity's separate native-command transport retains its verified public
response schema; undocumented native tool steps are not guessed. Shared-runtime
host-injected MCP tools, interactive approval callbacks and process-tree leases
remain the architectural boundaries documented in the core report. This change
does not introduce a persistent host service to implement those capabilities.
