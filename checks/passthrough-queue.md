# ACPX focus and steering review

OpenClaw's bound ACP route is owned by its Gateway, not by an open host-model
inference. Its ACPX plugin registers `reply_dispatch` for ACP traffic
(`OpenClaw/extensions/acpx/index.ts:65`), before the ordinary reply resolver.
OpenClaw then admits one source reply operation at a time
(`src/auto-reply/reply/reply-turn-admission.ts:623-665`) and serializes turns
that share an ACP session through `SessionActorQueue`
(`src/acp/control-plane/manager.core.ts:327-357,591-604` and
`session-actor-queue.ts:47-66`). An ordinary new message waits; it does not
cancel or inject into the running ACP turn. The ACPX agent-harness path even
rejects live injection (`extensions/acpx/src/harness-attempt.ts:79-90`).
OpenClaw treats `/acp cancel`, `/stop`, and authorized whole-message abort
triggers separately from ordinary messages. We added explicit `/cli cancel`
for the active turn and kept `/cli stop` for full shutdown; arbitrary prose
such as “stop” remains ordinary provider input in this plugin.

Previously CLI-MODE's hook superseded every unsubmitted capture on the next
`UserPromptSubmit`, while host inference had to call blocking `send --request`.
A user steering message could therefore interrupt the Codex response that
owned the submitter, discard a not-yet-submitted prompt, or change `turnRoute`
before the earlier request reached the provider.

Now the hook commits exact input to a bounded FIFO queue and starts one
conversation-owned, detached Python worker. The worker alone drains requests
to the same saved ACPX session. Each request retains its routing snapshot, so
a later Direct/Passthrough choice does not rewrite accepted work. The host
uses nonblocking `observe --request <id> --cursor <offset>` to read durable
receipts and filtered public events. `queue` shows redacted status and
`resume` restarts draining after a missing worker or explicit reconciliation.
A dead submitter with an admitted operation blocks the queue as `uncertain`;
it is never replayed automatically. The queue holds at most 32 waiting
messages. Later ordinary messages and `/help` preserve queued work.

The offline ACPX fixture runs the hook in a separate process and verifies that
its process exits before a slow turn finishes. A follow-up waits and then
completes in the same provider session,
and `/cli cancel` settles the active turn before the queued follow-up runs.
Unit tests cover FIFO admission, mode snapshots, event cursors, dead-worker
recovery, exact input and off cleanup. At this development checkpoint, the
temporary package passed all 438 offline tests from a fresh extraction and home; see
`passthrough-package-evidence.json`. The worker continues when the Codex
response ends, but only an active Codex response can relay its public events
into chat. This plugin does not have OpenClaw's always-running Gateway or a
Codex API for independently posting later agent output to the chat.
