"""Agent names: generate, validate and resolve them. Pure functions; no state is written here.

Every agent CLI-MODE runs has a name, shown in capitals: one the user gives at spawn (`/cli spawn gro
ELONMUSK`, 1-10 letters and digits) or a generated one, the agent's three-letter code, a dash and a
two-character id (`COD-7K`). Targeting ignores case. A generated name is also reached as `cod7k` (without the
dash) or as `-7K` when exactly one live agent has that id; never as the bare `7k`, so an ordinary word at the
start of a prompt is never taken for an agent.
"""
import hashlib
import json
from pathlib import Path
import re

# The agent registry both hosts load (state.REGISTRY). Each agent's three-letter tag (`cod`) is also a command
# word (/cli cod, /cli spawn cod) and, in capitals, the code of its generated names (COD-7K).
REGISTRY = Path(__file__).resolve().parents[1] / 'codex/skills/cli-mode/references/backends.json'
CODES = {item['id']: item['tag'].upper() for item in json.loads(REGISTRY.read_text(encoding='utf-8'))['backends']}
# 33 characters: no 0, I or O, which read like each other.
ID_CHARS = '123456789ABCDEFGHJKLMNPQRSTUVWXYZ'
CUSTOM_MAX = 10
CUSTOM = re.compile(r'[A-Za-z0-9]{1,' + str(CUSTOM_MAX) + '}')
_IDS = '[' + ID_CHARS + ID_CHARS.lower() + ']'
GENERATED = re.compile('(' + '|'.join(CODES.values()) + ')-(' + _IDS + '{2})', re.IGNORECASE)
UNDASHED = re.compile('(' + '|'.join(CODES.values()) + ')(' + _IDS + '{2})', re.IGNORECASE)
SHORT = re.compile('-(' + _IDS + '{2})')
# Words a custom name can't be: CLI-MODE's own command words and targets.
RESERVED = frozenset((
    'all', 'off', 'on', 'use', 'ultra', 'close', 'stop', 'spawn', 'bind', 'menu', 'settings', 'model', 'effort',
    'access', 'permissions', 'cancel', 'queue', 'resume', 'agents', 'list', 'help', 'commands', 'max', 'view',
    'progress', 'display', 'color', 'colour', 'shortcuts', 'reset', 'mode', 'home', 'x'))


def generate(backend, seed, used=()):
    """A generated name for a new `backend` agent, derived from `seed` (its session's unique name).

    Derived rather than random: the same session always gets the same name, which also names a session saved
    before names existed. `used` holds names this conversation already gave out; they are never reused.
    """
    code = CODES.get(backend, 'AGT')
    taken = {name.upper() for name in used}
    for attempt in range(2000):
        digest = hashlib.sha256((str(seed) + ':' + str(attempt)).encode()).digest()
        name = code + '-' + ID_CHARS[digest[0] % len(ID_CHARS)] + ID_CHARS[digest[1] % len(ID_CHARS)]
        if name not in taken:
            return name
    raise RuntimeError('No free ' + code + ' name is left in this conversation.')


def custom_error(name, live=()):
    """Why `name` can't be a custom agent name, or None when it can."""
    import state
    if not CUSTOM.fullmatch(name or ''):
        return ('An agent name is 1-' + str(CUSTOM_MAX) + ' letters or digits, with no spaces or symbols: ' +
                (name or '(empty)') + '.')
    folded = name.casefold()
    if folded in RESERVED or state.resolve_backend(folded):
        return name.upper() + ' is a CLI-MODE command or agent word, so it can\'t name an agent.'
    if UNDASHED.fullmatch(name):
        return name.upper() + ' reads as a generated name (' + generated_form(name) + '); choose another.'
    if any(folded == other.casefold() for other in live):
        return 'An agent named ' + name.upper() + ' is already running.'
    return None


def generated_form(word):
    """`cod7k` -> `COD-7K`."""
    match = UNDASHED.fullmatch(word)
    return (match.group(1) + '-' + match.group(2)).upper() if match else word.upper()


def resolve(word, agents):
    """Which live agent `word` names.

    `agents` maps each live agent's name to its session. Returns ('match', session), ('ambiguous', names)
    for a short form several agents share, or None.
    """
    folded = (word or '').casefold()
    if not folded:
        return None
    for name, session in agents.items():
        if name.casefold() == folded:
            return 'match', session
    if UNDASHED.fullmatch(word):
        wanted = generated_form(word).casefold()
        for name, session in agents.items():
            if name.casefold() == wanted:
                return 'match', session
    short = SHORT.fullmatch(word)
    if short:
        found = [(name, session) for name, session in agents.items()
                 if GENERATED.fullmatch(name) and name.casefold().endswith(folded)]
        if len(found) == 1:
            return 'match', found[0][1]
        if found:
            return 'ambiguous', sorted(name for name, _ in found)
    return None


def looks_like_name(word):
    """True for a word only a generated name has the shape of (`COD-7K`, `-7K`): with no live agent to match,
    such a word is refused rather than sent to the current agent as text."""
    return bool(GENERATED.fullmatch(word or '') or SHORT.fullmatch(word or ''))
