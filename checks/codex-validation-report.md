# Codex host validation of CLI-MODE 0.3.7 — 2026-09-26

Carried out with `checks/codex-validation-plan.md`: the plugin installed from GitHub (`v0.3.7`), the user's real Codex
configuration through `codex app-server`, real agents (Grok Build, Codex CLI) on their own accounts, throwaway
projects. Codex's weekly limit went from 91% to 93% over the whole validation.

## Part 1: the shared scenario

`codex_user_validation.py --agent grok-build --depth full --extras gates format viewer agents tools`: 67 real turns
across menus, settings, routing, relays, native commands, queue and resume, errors, closing, the Full Access gate,
formatting, the viewer, two named agents and the change-receipt tools.

- 61 clean.
- 5 flagged the host writing its note in the project brief (a new 0.3.7 action the harness did not know). The
  harness now accepts a command or file change that touches only `Agent_Working_Folder/BRIEF.md`.
- 1 real bug: with `/cli progress quiet`, an agent's text on either side of a tool call under one message ID (Grok
  Build's) was joined mid-line ("...one sentence.The tests passed..."). Fixed in the bridge; test added.

## Part 2: 0.3.6 and 0.3.7 features

`codex_release_validation.py` (new): the project brief, approvals, attachments in the Codex desktop app's own text,
undo and the test gate, and a quiet-progress relay.

First run, on the released 0.3.7 (30 steps): 24 passed. Flagged:

- A2, A5, C2: harness false alarms (the host's note edit; Codex reading an attached file on its own turn). The
  harness now leaves a turn that is not CLI-MODE's to the host.
- B4: Grok runs commands through ACPX's terminal without asking first; when ACPX refused one, the answer said the
  permission "could not be read" and `/cli approve` had nothing to approve. Fixed: the refused call is the question.
- B6, B8: after `/cli approve always` for edits, Grok also ran commands without asking. An approved turn runs at
  ACPX's approve-all (its write and terminal checks read only the mode), and kind rules only govern agents that ask
  first. Decided with the user: honest approvals per agent. An agent that acts without asking (Grok from the start,
  any other once seen doing it) is told that approving lets it act freely for that one turn, and `approve always`
  is refused with a pointer to `/cli access allow`.

Second run, on the development build with the fixes (33 steps, adding the quiet-progress relay): **33 passed**.

Confirmed live: Codex writes a real host note (for example "The user is building a small garden-planner app. The
app will cover planting dates and watering reminders. No implementation work has been requested or completed yet."),
the agent list stays current and a closed agent drops out, each task tells the agent who it is, approve / deny /
nothing waiting / a new /d settling the question, attachments copied, read, listed and removed on close, undo
byte for byte on a CRLF file under `core.autocrlf=true`, undo leaving another agent's same-turn edit, the test gate
line, and quiet relays keeping paragraphs apart.

## Part 3: the Codex Desktop window

Not run: driving the Codex app window was declined. Its checks (views rendering as cards, a pasted screenshot, the
viewer window) remain for a person, with the steps in the plan.

## Afterwards

The user's Codex was restored to the `v0.3.7` release (cache compared with the release zip) and its configuration to
its state before the validation (the test threads' trusted-project entries removed).
