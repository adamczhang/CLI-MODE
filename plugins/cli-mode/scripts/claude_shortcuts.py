"""Bare /cli and /d in Claude Code's autocomplete (Claude Code only).

Claude Code names a plugin's commands by plugin (/cli-mode:cli), so typing /cli autocompletes to that
and Enter picks it. Personal commands keep their bare names, so /cli and /d are written there too, doing
exactly what the plugin's do (CLI-MODE's hook answers either). A file of the user's own is never replaced:
only files carrying MARKER are. install-claude.ps1 runs this; a GitHub install gets it with /cli shortcuts.

    python claude_shortcuts.py [--config-dir <Claude Code configuration folder>]
"""
import argparse
import os
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
MARKER = '<!-- Installed by CLI-MODE; reinstalling it or /cli shortcuts replaces this file. -->'
OWNED = 'Installed by CLI-MODE'  # Also matches the 0.2.0 installer's wording.
NAMES = ('cli.md', 'd.md')


def config_dir():
    return Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')


def install(folder=None):
    """Write the personal commands; returns {'added': [...], 'kept': [...], 'folder': path}."""
    commands = Path(folder or config_dir()) / 'commands'
    commands.mkdir(parents=True, exist_ok=True)
    added, kept = [], []
    for name in NAMES:
        path = commands / name
        if path.is_file() and OWNED not in path.read_text(encoding='utf-8', errors='replace'):
            kept.append(name)
            continue
        text = (PLUGIN / 'claude' / 'commands' / name).read_text(encoding='utf-8').replace('\r\n', '\n')
        end = text.index('\n---', 3) + 4  # After the frontmatter's closing line.
        # UTF-8 without a BOM, which would hide the frontmatter.
        path.write_bytes((text[:end] + '\n' + MARKER + text[end:]).encode('utf-8'))
        added.append(name)
    return dict(added=added, kept=kept, folder=str(commands))


def summary(result):
    """Plain lines for the installer and for /cli shortcuts."""
    lines = []
    if result['added']:
        lines.append('/' + ' and /'.join(name[:-3] for name in result['added']) + ' now autocomplete as typed ('
                     + result['folder'] + '). Start a new session to see them.')
    for name in result['kept']:
        lines.append('Kept your own ' + str(Path(result['folder']) / name) + '; CLI-MODE\'s command stays '
                     'available as /cli-mode:' + name[:-3] + '.')
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config-dir', help='Claude Code configuration folder (default: CLAUDE_CONFIG_DIR or ~/.claude).')
    print(summary(install(parser.parse_args().config_dir)))
