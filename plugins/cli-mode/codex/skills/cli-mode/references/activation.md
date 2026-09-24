# Shared activation presentation

All six backends use the same executable template. After successful activation
or binding, run:

```text
python controller.py --thread THREAD --workspace WORKSPACE --message-output OUTPUT.html activation-message
```

Display the returned messageView using the shared inline presentation contract.
Do not reconstruct the message, add the provider to its heading or query usage
again. The controller requires a ready active session and rechecks it after the
quota lookup. Activation failures must never use the success template.

The output contains a bold CLI-MODE Activated heading, one blank line, accepted
Model / Effort / Access / Question labels, then one blank line and Utilization.
Question is `/help`. The entire activation message uses green accents and the
shared monospace font. Provider replies use normal chat text with a green
`<Agent> says...` attribution; passing announcements are green.

Antigravity and Claude use the existing native read-only usage helpers, executed
in the task workspace. Those helpers supply observed quota windows and reset
countdowns; additional windows are retained and continuation rows are indented
13 spaces. Never infer quota from a model answer, token counts or host-account
limits. Helper source/account information remains diagnostic metadata; it is not
proof that native and ACP accounts match. A known mismatch or API-key billing
uses the unavailable fallback. Do not sign in, install or poll repeatedly to
populate activation usage.

Unsupported quota retrieval, failed queries or unusable data all display exactly:

```text
Utilization: Usage not available through CLI
```

The same fallback applies to every provider. Failure to obtain usage does not
undo a successful activation. Inline rendering unavailable: show the returned
text and disclose that the host cannot display the requested colors.
