# Native command coverage

Reference: https://antigravity.google/docs/cli/reference/

Checked against installed native `/help` metadata on 2026-09-22. This is a routing
matrix, not a claim that terminal features work headlessly. Metadata required
`SUCCESS`, zero model turns, and a native command result. All 35 canonical names
and 10 aliases have explicit handling; unsupported entries fail before handoff.

| Command | Aliases | Current handling |
|---|---|---|
| /add-dir |  | Unavailable headlessly: needs persistent workspace-directory management |
| /agents |  | Advertised native headless handler |
| /boost |  | Documented native workflow; task required |
| /artifact |  | Unavailable headlessly: opens an artifact review panel |
| /btw |  | Documented native workflow; task required |
| /clear | /new | Unavailable headlessly: resets the interactive conversation |
| /config | /settings | Advertised native headless handler |
| /context |  | Unavailable headlessly: opens a context visualization panel |
| /copy |  | Unavailable headlessly: copies to the system clipboard |
| /credits |  | Advertised native headless handler |
| /diff |  | Unavailable headlessly: opens an interactive diff viewer |
| /exit | /quit | Unavailable headlessly: closes the interactive terminal session; use /cli stop in CLI-MODE |
| /fast |  | Unavailable headlessly: changes interactive execution state |
| /feedback |  | Unavailable headlessly: opens the feedback panel |
| /fork | /branch | Unavailable headlessly: creates another conversation |
| /help |  | CLI-MODE local help (reserved); `/commands` is not a help alias |
| /hooks |  | Advertised native headless handler |
| /keybindings |  | Unavailable headlessly: opens the keyboard shortcut editor |
| /logout |  | Unavailable headlessly: changes the native account login |
| /mcp |  | Unavailable headlessly: opens the MCP server manager |
| /model |  | Advertised native headless handler |
| /open |  | Unavailable headlessly: opens an external editor |
| /permissions |  | Advertised native headless handler |
| /planning |  | Unavailable headlessly: changes interactive planning state |
| /rename |  | Unavailable headlessly: renames the native conversation |
| /remote-control |  | Unavailable headlessly: controls the native remote-control daemon |
| /resume | /switch, /conversation | Unavailable headlessly: opens a conversation picker |
| /rewind | /undo | Unavailable headlessly: rewinds native conversation history |
| /skills |  | Advertised native headless handler |
| /statusline |  | Unavailable headlessly: configures a terminal status bar |
| /tasks |  | Unavailable headlessly: opens a background-task manager |
| /teamwork-preview | /teamwork | Documented native workflow; task required |
| /title |  | Unavailable headlessly: changes the terminal title |
| /usage | /quota | Advertised native headless handler |
| /voice | /record | Unavailable headlessly: requires microphone input and interactive dictation |

Additional advertised handlers: /changelog, /effort.

Additional documented workflows: /browser, /goal, /grill-me, /learn, /plan, /schedule.

Native skills and future advertised commands/aliases are discovered dynamically.
Arguments retain native restrictions. Headless handlers may offer only queries or
a subset of terminal behavior. Terminal-only commands were not executed for testing.
Workflow expansion beyond teamwork interview initialization is not live-verified.

Offline tests enumerate every canonical command and alias, check argument preservation,
dynamic discovery, unknown-command rejection, malformed metadata and unchanged session
state after rejected commands.
