# Codex through ACPX

Profile: codex. ACPX resolves its maintained @agentclientprotocol/codex-acp
adapter. The standalone CLI is installed with npm install -g @openai/codex.
Sign in through codex login; verify using codex login status.

Use the shared controller for lifecycle. Diagnostic commands for its owned
session include `acpx --cwd <workspace> --format json codex sessions show <name>`
and `acpx --cwd <workspace> codex -s <name> set model <advertised-id>`.
Apply model first, then reasoning_effort, then mode. Verify config_options and
current_model_id after changes. Use acpxRecordId for continuity when available.

Settings shown in assets/models.json are observed snapshots, not a promise of
future account access. Rejected settings must not trigger a silent substitute.
Commands use the advertised ACP command catalog. Only Full access is offered: native workspace writes bypassed headless approval
in the observed read-only preset. Prompt and Auto-edit are rejected.
Close only controller-owned sessions, never all user Codex sessions.

Sources: [ACPX registry](https://acpx.sh/agents.html),
[Codex CLI](https://learn.chatgpt.com/docs/codex/cli).
