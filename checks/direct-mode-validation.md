# Direct/Passthrough routing validation — 2026-09-22

Routing additions to v0.1.8, based on `464a602`; the release archive has been
rebuilt to include these changes. The updated local plugin is installed as
`0.1.8+codex.20260922115126`.

**334 tests passed** from source and from a newly built, independently extracted
development package. Plugin/skill validation and whitespace checks passed.
The tests use isolated state and fixture providers with real subprocess pipes;
no additional paid provider prompts were required or sent.

Covered behavior:

- One-page mode menu while on or off, including independent dismissal with X.
- Preservation of activation, main session, model/effort/access, pending provider
  settings, generation and in-flight work when selecting or dismissing modes.
- Existing state defaults to Passthrough; invalid saved routing values fail closed.
- Direct mode blocks unprefixed and empty sends before calling any of five adapters.
- Leading `/d` and `$d` tokens, case/whitespace handling, unchanged payload line
  endings, and native-command validation after prefix removal.
- Host versus delegated hook behavior and Codex subagent restrictions.
- Compaction, completed-turn replay prevention, stop/reactivation persistence
  and a routing change during dispatch preflight.
- Actual controller subprocess menu rendering and settings updates.

The mode menu calls no provider lifecycle or settings commands. Running work is
not canceled by a routing change; an unstarted dispatch checks the current mode
before launch. The feature does not independently certify Desktop rendering or
fresh-task hook trust. Start a new task to load the installed skill update.

Logs: `%USERPROFILE%\.codex\validation\cli-mode\2026-09-22\direct-mode-tests.log`
and `direct-package-tests.log` in the same directory.

Direct command follow-up: all four ACP providers exercise their real command validators
with both triggers, help/context/commands, exact arguments, unknown-command rejection
and session continuity. Antigravity exercises native handoff once, native follow-ups,
and literal argv for teamwork alias expansion, help and usage. These are fixture
transport checks, not live execution of every provider workflow or a teamwork team.
The explicit provider-help routing gap was fixed. Logs: `direct-command-tests.log`
and `direct-command-package-tests.log` in the evidence directory above.

Agent Settings now includes the current routing mode and choice 3 to change it.
All five agent pages are checked for the initial Passthrough default, selection
and dismissal returning to setup, and preserving pending state without activation.
The routing menu uses the shared numbered-choice builder and inline renderer.

Latest full-suite logs: `mode-menu-tests.log` and `mode-menu-package-tests.log`.

Release archive verification: `v018-final-package-tests.log` in the same evidence
directory; the final portable v0.1.8 archive includes the routing and menu changes.
