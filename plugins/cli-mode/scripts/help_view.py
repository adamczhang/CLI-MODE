"""The help page: the same framed card as every other CLI-MODE menu."""
import host
from presentation import menu_block

COMMANDS = (
    ('/cli', 'Choose an agent and open its setup.'),
    ('/cli <agent>', 'Open agy (Antigravity), claude (Claude Code), grok (Grok Build), cursor, '
                     'copilot (GitHub Copilot) or codex (Codex CLI).'),
    ('/cli bind <agent>', 'Activate an agent with its saved settings, or its defaults on first use.'),
    ('/d <PROMPT>', 'Send a prompt to the active agent in Direct mode (the default).'),
    ('/cli menu', 'Agent Settings: model, effort, access, routing and progress.'),
    ('/cli progress <mode>', 'activity shows tool work and usage; quiet shows messages and plans only.'),
    ('/cli view on|off', 'Watch each agent turn live in a PowerShell window. Off by default.'),
    ('/cli queue', 'Show queued, running and completed requests.'),
    ('/cli cancel', 'Cancel the current agent turn; keep queued follow-ups.'),
    ('/cli resume', 'Reattach status monitoring and restart a stopped queue worker without resending a prompt.'),
    ('/cli stop', 'Close the active agent and clear queued requests. Same as /cli off.'),
    ('/help', 'Show this page.'),
)


def commands():
    """The rows for this host. Claude Code's own /help is built in, so CLI-MODE's is /cli help."""
    if not host.claude():
        return COMMANDS
    return COMMANDS[:-1] + (('/cli display chat|instant', 'Show CLI-MODE replies as chat messages, or at once '
                                                          'as notices without a model turn.'),
                            ('/cli color on|off', 'Green titles and names in chat, or plain bold for the '
                                                  'terminal.'),
                            ('/cli shortcuts', 'Add /cli and /d to autocomplete (the zip installer '
                                               'already does).'),
                            ('/cli reset', 'Set aside unreadable CLI-MODE state for this session.'),
                            ('/cli help', 'Show this page.'))


def text():
    """Unframed menu text: each command, then its description indented under it."""
    lines = ['CLI-MODE', 'Help', '']
    for command, description in commands():
        lines += [command, '  ' + description]
    lines += ['', '$ works in place of / for these commands.']
    if host.claude():
        lines += ['/cli-mode:cli and /cli-mode:d work if another plugin also uses /cli or /d.']
    lines += ['X. Close help']
    return '\n'.join(lines)


def render():
    """The help card as framed menu text, rendered like every other menu."""
    return menu_block(text())
