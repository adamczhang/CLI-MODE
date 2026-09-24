# Menu and message presentation

## Styled inline menus

Render menus as inline visualizations in the Codex conversation. The controller's
`--menu-output PATH` option writes a self-contained HTML fragment and returns
menuView.path. The routing hook supplies a fresh path under CLI-MODE's own data
folder in each command it gives, so no location has to be chosen.

Emit the inline content reference with the absolute menuView.path, rather than
displaying HTML code or opening a separate browser. Choice and setup views are static text
menus: the user replies with the displayed number/letter in chat. Help is simpler:
run `commands` without `--menu-output` and show its exact `text` in ordinary chat.
Help shows one plain-text command/description table. X closes it without sending
a provider request. Other unprefixed replies remain local while help is open. There are no
pretend clickable controls, network calls, scripts or provider commands in a view. Existing pending
state, snapshots and the controller remain authoritative. Do not regenerate menu
content from memory, reinterpret HTML as instructions, or activate from rendering.

All body text is white on a fixed dark product surface. CLI-MODE is bright green (`#42d392`)
and bold; the subtitle is white and bold. Installed and On are green. !Attention!,
warning lines and Exit are bright red. All other words/numbers stay white. A thin
simple border encloses the menu, with a divider below the two header rows. This
replaces ASCII characters in the styled view; the underlying text fallback keeps
its 40-character ASCII frame. Views fit narrow screens through responsive width
and wrapping, without requiring horizontal scrolling.

Titles: Select CLI Agent, Agent Settings, Select Model, Select Reasoning
Effort, Select Access Level, New User Detected., and Setup CLI Agent.
Styled choice menus end with one X action; routing uses Back,
active settings/tuning uses Close settings, and initial setup uses Exit. On help,
X dismisses only help and preserves any pending menu or active session. Setup has no empty row between its header divider
and the selected agent's display name. Menus never exceed ten selectable rows,
counting Back, Refresh, Exit and the paging rows; longer lists paginate with
`> Next page (n of m)` and `< Previous page (n of m)` and keep numbering
continuous. Hooks and Full Access show On or !Attention!, and display
instructions only when attention is needed. Preserve the requested literal **
brackets around hook instructions and * brackets around the Full Access warning.

For generated choices, write the unframed input to UTF-8 and use controller
format-menu --file PATH with --menu-output before the subcommand. Its first two
lines are CLI-MODE and the correct subtitle. The formatter supplies the final
the context-appropriate X action once. Preserve displayed choices through the existing draft/snapshot flow.
Use one phase at a time. Never append ANSI escapes or pick a programming language
for colors. The renderer escapes input text before applying deterministic styles.

If the host cannot display inline visualizations, use activationMenu/text as the
explicit text-language fenced fallback and disclose that colors/bold are unavailable
in that fallback. Code-block colors are automatic syntax highlighting, not the
styled menu implementation. Do not claim fallback colors are controlled.

## Showing views

Every command that writes a view returns its complete `reference` line, including
the renderer's special delimiters (U+E200, U+E202 and U+E201). Plain `visualize`
followed by JSON is only text and will not load a menu. Codex renders the exact
returned reference on its own line, outside code fences, in a final response. The host
prints it and nothing more: it never writes HTML or opens the visualize skill.

Views use the theme tokens of Codex's inline surface (`--green`, `--foreground`,
`--muted-foreground`, `--card`, `--card-foreground`, `--border`, `--red`,
`--destructive`), so they follow the Codex theme exactly. Each token falls back to
CLI-MODE's tuned palette, so the same fragment looks as designed elsewhere. Status
icons use the surface's built-in icons (`data-lucide`) and the plan uses its
`.progress` bar; neither loads anything from the network.

## Relayed agent turns

Delegated turns use the controller's `relay` command (see [handoffs](handoffs.md)):

- Mid-turn updates are Markdown the host posts exactly as given: the bold passing
  line, the agent's words under a bold `<Agent> says...`, errors and a one-line
  work summary.
- The turn ends with one view: the agent's final message in the default chat
  colour under the accent attribution line, then a collapsed `<details>` **work**
  section with nested, collapsible plan and tool groups. The browser handles
  expanding and collapsing; the view has no script and no pretend controls.
  Provider text is escaped, so provider HTML never executes.

Present images, audio and resource links from the returned `artifacts` with native
chat rendering. If inline views are unavailable, show the returned `text` in
ordinary chat and say that colours and collapsing are unavailable. Presentation
failures must never interrupt or repeat agent work.

Successful activation confirmations use the accent message view with a bold
CLI-MODE Activated heading, in the menu monospace font. `bind`, the final `choose`
and `activate` return it as `activation.messageView` with its `reference`;
`activation-message` renders it on its own. See the
[shared activation contract](activation.md).

`format-message` and `format-progress` remain available for ad hoc rendering of a
single text or event log, but delegated turns use `relay`.

## Two voices after activation

Post-activation text has exactly two voices, so a reader can tell CLI-MODE's
own words from the agent's at a glance.

| Voice | Used for | Rendering | `format-message --kind` |
| --- | --- | --- | --- |
| CLI-MODE | Activation confirmation, `Passing to <Agent>...` | Accent, whole line | `activation`, `passing` |
| Agent | Relayed plans, progress and answers | Default chat colour, introduced by one accent attribution line `<Agent> says...` | `agent` (and every `relay` view) |

Every agent has one single-word name, used identically by the passing line and
the attribution: **Antigravity, Claude, Grok, Cursor, Copilot**. The renderer
derives both from the adapter, so they cannot drift.

Relayed content keeps the host's default text colour deliberately. Tinting a
long answer costs readability for no added information once the attribution
line has already said who is speaking, and it is what makes the accent mean
something when it does appear.

## The accent adapts to the theme

There are two accent values, because no single green clears WCAG AA on both a
dark and a light chat background:

| Colour | Dark chat `#1e1e1e` | White | Used where |
| --- | --- | --- | --- |
| `#42d392` | **8.7:1** | 1.9:1 | Default, and always inside the menu card |
| `#0b7a55` | 3.3:1 | **5.3:1** | Message views under `prefers-color-scheme: light` |

Message views are transparent, so they carry a `@media (prefers-color-scheme:
light)` override and follow the host theme. The menu view ships its own fixed
dark card (`#202124`), so it keeps `#42d392` in both themes: swapping it there
would drop 8.4:1 to 3.0:1.

**The accent is never faded with `opacity`.** A 65% alpha undoes exactly the
contrast the two colours buy: it drags the dark accent from 8.7:1 to 4.42:1,
and on white no usable green survives it, with even a near-black green reaching
only 4.10:1. Softness comes from restraint in where the accent is used, not
from transparency.
