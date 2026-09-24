"""The host running CLI-MODE: Codex or Claude Code.

One plugin folder serves both hosts. The router and controller receive
`--host`, which is exported as CLI_MODE_HOST so the queue worker and bridges
they start agree. Only what differs by host lives here: where state is kept,
which folder is the workspace, whether results can be inline HTML views, how
typed prompts arrive, and the words that name the host. Codex is the default,
so an unset host behaves exactly as before.
"""
import os
from pathlib import Path
import re

CODEX, CLAUDE = 'codex', 'claude-code'
HOSTS = (CODEX, CLAUDE)
NAMES = {CODEX: 'Codex', CLAUDE: 'Claude Code'}
# Claude Code marks pasted text with a line before and after it.
PASTE_MARK = re.compile(r'</?pasted_content id="[^"\r\n]*">')


def current():
    value = os.environ.get('CLI_MODE_HOST', CODEX)
    return value if value in HOSTS else CODEX


def select(value):
    """Adopt a host for this process and every child it starts."""
    if value not in HOSTS:
        raise ValueError('Unknown CLI-MODE host: ' + repr(value))
    os.environ['CLI_MODE_HOST'] = value
    return value


def claude(value=None):
    return (value or current()) == CLAUDE


def name(value=None):
    return NAMES[value or current()]


def views(value=None):
    """Codex renders inline HTML views; Claude Code shows chat text."""
    return not claude(value)


def display_choices(root):
    """How Claude Code shows CLI-MODE's replies, saved for every session: {'display': ..., 'color': ...}."""
    import json
    try:
        saved = json.loads((Path(root) / 'display.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}
    return saved if isinstance(saved, dict) else {}


def chat_color(root, value=None):
    """Dark green titles and attribution in Claude Code's chat, unless /cli color off (for the terminal,
    which shows the LaTeX that colours them as raw text). Codex has its own green views."""
    return claude(value) and display_choices(root).get('color', 'on') != 'off'


def data_root(value=None):
    """Where conversation state lives. It stays put across plugin updates."""
    if os.environ.get('CLI_MODE_DATA'):
        return Path(os.environ['CLI_MODE_DATA'])
    if claude(value):
        # Hooks receive CLAUDE_PLUGIN_DATA; commands carry --data-root. Its
        # folder depends on how the plugin was installed, so never guess one:
        # a guessed folder would split a conversation's state in two.
        if os.environ.get('CLAUDE_PLUGIN_DATA'):
            return Path(os.environ['CLAUDE_PLUGIN_DATA'])
        raise ValueError('Claude Code state needs --data-root or CLAUDE_PLUGIN_DATA.')
    home = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
    return home / 'plugin-data' / 'cli-mode'


def workspace(event, value=None):
    """The conversation's workspace.

    Codex reports a fixed task folder as `cwd`. Claude Code's `cwd` follows
    every `cd`, so it uses the project folder the session started in, which
    never changes during a session.
    """
    if claude(value) and os.environ.get('CLAUDE_PROJECT_DIR'):
        return os.environ['CLAUDE_PROJECT_DIR']
    return event['cwd']


def unwrap_prompt(text, value=None):
    """The prompt as the user typed and pasted it.

    Claude Code puts a marker line before and after pasted text; the agent
    receives the pasted text without them. Codex prompts pass through unchanged.
    """
    if not claude(value) or 'pasted_content' not in text:
        return text
    lines = text.split('\n')
    kept = [line for line in lines if not PASTE_MARK.fullmatch(line.rstrip('\r'))]
    return '\n'.join(kept)


def home(value=None):
    """The host's own configuration folder."""
    if claude(value):
        return Path(os.environ.get('CLAUDE_CONFIG_DIR', Path.home() / '.claude'))
    return Path(os.environ.get('CODEX_HOME', Path.home() / '.codex'))
