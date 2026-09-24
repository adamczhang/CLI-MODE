# Antigravity-owned subagents

CLI-MODE creates one main ACP session per host conversation/workspace. Antigravity
decides whether to use its native subagents inside that session. Codex never
creates or assigns worker sessions, selects their roles or splits task prompts.

Research checked on 2026-09-21:

- Google's [subagent documentation](https://antigravity.google/docs/subagents?tab=cli)
  describes the parent calling `invoke_subagent`, including built-in agents and
  automatically discovered custom definitions. A custom definition's
  `subagent: true` makes that definition invocable; it is not a global enable flag.
- The [CLI reference](https://www.antigravity.google/docs/cli/reference/) describes
  `/agents` as a management panel. `/boost` and `/teamwork-preview` are explicit
  workflows, not prerequisites for ordinary native subagent use.
- The installed `agy --help` exposes no general enable-subagents flag.
- ACPX uses the separate official ACP server, not `agy --print` or its terminal
  UI. The [official ACP launch manifest](https://raw.githubusercontent.com/agentclientprotocol/registry/main/antigravity-acp/agent.json)
  does not supply a subagent-enabling launch argument. Native CLI documentation
  alone does not prove identical capabilities in every ACP runtime build.

Provider slash commands now use the explicit [native CLI handoff](../../../codex/skills/cli-mode/references/native-commands.md). No guessed launch flag or prompt prefix is added.
Use the configured ACP runtime and existing selected access policy. Do not invent
settings, enable a special workflow automatically, create custom agents or alter
profiles. A user's request for parallel work is sent unchanged to the main agent.
If its runtime reports native delegation unavailable, relay that limitation;
do not emulate it by starting more ACP sessions. This update does not claim a
live verification of native subagent spawning or descendant cleanup.

The CLI reference lists `/teamwork` as an alias for `/teamwork-preview`. The controller preserves the task text and normalizes this alias to the canonical
`/teamwork-preview` token for reliable native expansion. CLI alias support does not establish ACP runtime
support: report teamwork activation only with explicit runtime confirmation, not
merely because the prompt was delivered or the requested files were created.
