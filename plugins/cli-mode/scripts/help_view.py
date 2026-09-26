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
CLAUDE_SETTINGS = (
    ('/cli display ...', 'chat or instant'),
    ('/cli color on|off', 'green or plain'),
)
AGENTS = ('agy (Antigravity)  cla (Claude Code)', 'cod (Codex CLI)    gro (Grok Build)',
          'cop (GitHub Copilot) cur (Cursor)')
COMMANDS = tuple(row for _, rows in SECTIONS for row in rows)


def sections():
    """The sections for this host: Claude Code adds its display and colour settings."""
    if not host.claude():
        return SECTIONS
    return SECTIONS[:-1] + ((SECTIONS[-1][0], SECTIONS[-1][1] + CLAUDE_SETTINGS),)


def commands():
    """Every command row shown on this host."""
    return tuple(row for _, rows in sections() for row in rows)


def text():
    """Unframed card text: grouped command lines, then the rarer commands and what the placeholders mean."""
    lines = ['CLI-MODE', 'Help']
    for index, (heading, rows) in enumerate(sections()):
        lines += ([''] if index else []) + [heading] + [command.ljust(COLUMN) + description
                                                       for command, description in rows]
        if heading == 'SEND WORK':
            lines.append('Only /d reaches an agent.')
    lines += ['', *(['More: /cli attach|resume|shortcuts,', '/cli reset, /cli help (this page)'] if host.claude()
                    else ['More: /cli attach, /cli resume,', '/help (this page)']),
              '<agent>: a tag or full name:', *AGENTS, '<name>: a name, tag (gro) or -7K',
              '$ works in place of / everywhere.']
    if host.claude():
        lines.append('/cli-mode:cli if /cli clashes.')
    lines.append('X. Close help')
    return '\n'.join(lines)


def render():
    """The help card as framed menu text, rendered like every other menu."""
    return menu_block(text())
