"""The help page: a compact card, one short line per command, grouped by what you want to do.

It is framed like every other CLI-MODE menu (40 columns, the phone's code-block width), so each command and its
description share one line: the command column is COLUMN wide and descriptions stay short. The README explains
every command in full.
"""
import host
from presentation import menu_block

COLUMN = 20  # Command width: the longest command is 18 characters, and two spaces always follow it.

SECTIONS = (
    ('AGENTS', (
        ('/cli', 'choose an agent'),
        ('/cli <agent>', 'its setup page'),
        ('/cli spawn <agent>', 'start another'),
        ('/cli list', 'running agents'),
        ('/cli use <name>', 'make it current'),
        ('/cli close [name]', 'close (or all)'),
    )),
    ('SEND WORK', (
        ('/d <task>', 'to current agent'),
        ('/d <name> <task>', 'to one agent'),
        ('/d <a>,<b> <task>', 'to several'),
        ('/cli approve|deny', 'answer its ask'),
    )),
    ('RESULTS', (
        ('/cli diff [name]', 'last turn\'s diff'),
        ('/cli dir [name]', 'where files go'),
        ('/cli usage [name]', 'plan usage left'),
        ('/cli queue', 'queued requests'),
        ('/cli cancel [name]', 'stop its turn'),
        ('/cli undo [name]', 'undo last turn'),
    )),
    ('SETTINGS', (
        ('/cli menu [name]', 'model, effort...'),
        ('/cli timeout ...', 'idle time (1 h)'),
        ('/cli view on|off', 'live window'),
        ('/cli progress ...', 'activity/quiet'),
        ('/cli test <cmd>', 'auto-run tests'),
        ('/cli brief ...', 'shared brief'),
    )),
)
# Claude Code's SEND WORK section ends with its modes instead: in AUTO, Claude hands the work over, not /d.
MODE_ROW = ('/cli mode ...', 'DIRECT or AUTO')
CLAUDE_SETTINGS = (
    ('/cli display ...', 'chat or instant'),
    ('/cli color on|off', 'green or plain'),
)
AGENTS = ('agy (Antigravity)  cla (Claude Code)', 'cod (Codex CLI)    gro (Grok Build)',
          'cop (GitHub Copilot) cur (Cursor)')
COMMANDS = tuple(row for _, rows in SECTIONS for row in rows)
# Claude Code in AUTO: Claude runs the agents, so the card shows only the user's own controls. The commands that act
# on one agent's work (undo, diff, queue, dir, test...) stay in the plugin for Claude and for DIRECT.
AUTO_SECTIONS = (
    ('AUTO', (
        ('/cli list', 'handoffs, agents'),
        ('/cli approve|deny', 'answer its ask'),
        ('/cli cancel', 'stop every turn'),
        ('/cli usage [name]', 'plan usage left'),
        ('/cli off', 'close all agents'),
    )),
    ('SETTINGS', (
        MODE_ROW,
        ('/cli view on|off', 'live window'),
    ) + CLAUDE_SETTINGS),
)
AUTO_ASK = ('Ask Claude to undo, show diffs,', 'run tests or change the agents.')


def sections():
    """The sections for this host: Claude Code adds its display and colour settings."""
    if not host.claude():
        return SECTIONS
    return SECTIONS[:-1] + ((SECTIONS[-1][0], SECTIONS[-1][1] + CLAUDE_SETTINGS),)


def commands():
    """Every command row shown on this host."""
    return tuple(row for _, rows in sections() for row in rows)


def auto_text():
    """AUTO's card (Claude Code): the user's own controls, and what to ask Claude for instead."""
    lines = ['CLI-MODE', 'Help (AUTO)']
    for heading, rows in AUTO_SECTIONS:
        lines += ['', heading] + [command.ljust(COLUMN) + description for command, description in rows]
    lines += ['', *AUTO_ASK, 'More: /cli shortcuts, /cli reset,', '/cli help (this page)',
              '$ works in place of / everywhere.', '/cli-mode:cli if /cli clashes.', 'X. Close help']
    return '\n'.join(lines)


def text(auto=False):
    """Unframed card text: grouped command lines, then the rarer commands and what the placeholders mean."""
    if auto and host.claude():
        return auto_text()
    lines = ['CLI-MODE', 'Help']
    for index, (heading, rows) in enumerate(sections()):
        lines += ([''] if index else []) + [heading] + [command.ljust(COLUMN) + description
                                                       for command, description in rows]
        if heading == 'SEND WORK':
            lines.append(MODE_ROW[0].ljust(COLUMN) + MODE_ROW[1] if host.claude() else 'Only /d reaches an agent.')
    lines += ['', *(['More: /cli attach|resume|shortcuts,', '/cli reset, /cli help (this page)'] if host.claude()
                    else ['More: /cli attach, /cli resume,', '/help (this page)']),
              '<agent>: a tag or full name:', *AGENTS, '<name>: a name, tag (gro) or -7K',
              '$ works in place of / everywhere.']
    if host.claude():
        lines.append('/cli-mode:cli if /cli clashes.')
    lines.append('X. Close help')
    return '\n'.join(lines)


def render(auto=False):
    """The help card as framed menu text, rendered like every other menu; `auto` for AUTO's own card."""
    return menu_block(text(auto))
