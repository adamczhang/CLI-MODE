# Delegation and relay

Only leading `/d` or `$d` requests go to the main provider; the worker strips the
trigger exactly once. Every other prompt stays with Codex. Never rewrite, split,
add context to or generate follow-up work for a delegated prompt. The agent owns
strategy, implementation, checks, media and any native subagents.

## The relay loop

The hook captures the message, queues it for the conversation's worker and gives
the relay command with its request ID:

```
python controller.py --thread THREAD --workspace WORKSPACE relay --request REQUEST_ID [--cursor N] [--wait SECONDS]
```

`relay` waits up to `--wait` seconds (default 8, at most 30) for something worth
showing, then returns:

| Field | Meaning |
| --- | --- |
| `markdown` | The mid-turn update to post as a chat message, exactly as given. Empty when nothing new arrived. |
| `done` | True once the request has settled (completed, failed, canceled, uncertain or not sent). |
| `reference` | When `done`: the complete rendering reference, including special delimiters, for the turn's one inline view. |
| `messageView` | When `done`: that view's path and label. |
| `cursor` | Pass back as `--cursor` on the next call. |
| `status` | The request receipt status. |
| `idleSeconds` | Measured seconds since the last public event. Waiting time, not activity. |
| `artifacts` | Images, audio and resource links from this batch, to present natively. |

Give the command a timeout of at least 20 seconds, and call it again at once with
the returned cursor while `done` is false; never sleep in between. Post each
non-empty `markdown` right away: these are the periodic mid-turn updates. When
`done` is true, the final response is the `reference` line, on a line of its own.

Why two forms: Codex renders inline views only in a final response, and each view
is a sandboxed frame, so mid-turn updates are ordinary Markdown and the turn gets
exactly one view. That view shows the agent's final message (earlier messages were
already posted), artifacts, errors, and a collapsible work section.

The first call returns at once with the passing line. Existing receipts are
observations, never a new dispatch: after compaction, continue with the last
cursor you used (or 0, which repeats earlier output). Uncertain work is inspected
with `/cli queue`, never retried.

Batching happens in the controller, like OpenClaw's block streaming. A call
returns when at least 800 characters of agent text are ready, after 1.5 seconds
without new events, after holding a batch for 6 seconds, or when the turn settles.
Do not use observe, format-message or format-progress for delegated turns.

## What an update and the final view contain

A Markdown update carries, in order: the bold passing line (first call only), the
agent's words under a bold `<Agent> says...` line, artifacts, errors and warnings
(including permission stops), and a one-line italic work summary (tool counts, the
plan step in progress and the latest running tool). A line of agent output that
Codex would read as a view reference is defused, so an agent can never open a view.

The final view shows the agent's last message with bold, inline code and fenced
code preserved, then a collapsed **work** section built from browser-native
`<details>`: the plan (open, with a progress bar and steps marked done, in progress
or pending) and tool activity grouped by kind, each group collapsible with
unfinished work first, and usage. `/cli progress quiet` omits all work details.
Execute and unknown tools show generic titles, so command arguments never appear.
Raw tool inputs and outputs, and private reasoning, are excluded before events are
logged; never recover them from ACPX history.

Public message events do not reliably separate progress from final answers, and
tool completion is not task success: the request status is authoritative. Do not
invent activity, counts or elapsed time. If the agent is quiet, a factual
elapsed-time note at most once a minute is enough.

## Provider commands

After the `/d` trigger is removed, provider commands are checked before
dispatch. `/d /help` asks the provider for help; bare `/help` is CLI-MODE help.
Unsupported commands fail before dispatch instead of becoming model text.
Antigravity's `/teamwork` and `/teamwork-preview` use its native CLI handoff. See
[native commands](native-commands.md).
