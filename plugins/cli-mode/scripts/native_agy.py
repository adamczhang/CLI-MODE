"""Native Antigravity transport for client-expanded teamwork workflows."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

import processes

COMMAND_CATALOG = json.loads((Path(__file__).resolve().parents[1] /
    'backends/agy/assets/commands.json').read_text(encoding='utf-8'))
COMMANDS = COMMAND_CATALOG['commands']
WORKFLOWS = set(COMMAND_CATALOG['workflows'])
ALIASES = {alias: item['name'] for item in COMMANDS for alias in item['aliases']}
COMMAND_TOKEN = re.compile(r'^(\s*)/([a-z][a-z0-9_.:-]*)(?=\s|$)', re.I)


def command_request(text):
    match = COMMAND_TOKEN.match(text)
    return bool(match and match[2].casefold() not in ('cli', 'help'))


def expand_alias(text, aliases=None):
    """Normalize only the first command token; preserve all argument bytes."""
    match = COMMAND_TOKEN.match(text)
    if not match:
        return text
    name = match[2].casefold()
    canonical = {**ALIASES, **(aliases or {})}.get(name, name)
    return text[:match.start(2)] + canonical + text[match.end(2):]


_inventory = {}
INVENTORY_TTL = 600  # The worker now lives across turns; re-read at most every ten minutes.


def native_inventory(workspace, command):
    """Read native metadata without asking a model whether a command exists.

    Each lookup starts the native CLI (about two seconds), so results are kept
    for this process for INVENTORY_TTL seconds.
    """
    key = (str(workspace), command)
    cached = _inventory.get(key)
    if cached and time.monotonic() - cached[0] < INVENTORY_TTL:
        return cached[1]
    metadata = _native_inventory(workspace, command)
    _inventory[key] = (time.monotonic(), metadata)
    return metadata


def _native_inventory(workspace, command):
    binary = shutil.which('agy')
    if not binary:
        raise RuntimeError('Native Antigravity CLI is missing; no command was sent.')
    try:
        result = subprocess.run([binary, '--output-format', 'json', '--print-timeout', '10s', '-p', command],
            cwd=workspace, capture_output=True, text=True, encoding='utf-8', timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError('Native command discovery timed out; no command was forwarded.') from None
    try:
        data = json.loads(result.stdout)
        if (result.returncode or data.get('status') != 'SUCCESS' or data.get('num_turns') != 0
                or data.get('command', {}).get('name') != command[1:]):
            raise ValueError('Not a native metadata response')
        metadata = data['command']['data']
        if not isinstance(metadata, dict):
            raise ValueError('Invalid native metadata')
        return metadata
    except (ValueError, KeyError, TypeError, AttributeError):
        raise RuntimeError('Native command discovery failed; nothing was forwarded as model text.') from None


def validate_command(text, workspace):
    """Require an advertised handler/skill or documented native workflow."""
    token = COMMAND_TOKEN.match(text)
    if not token:
        return {}
    name = token[2].casefold()
    import native_commands
    native_commands.refuse_host_owned(name, 'Antigravity')
    commands = native_inventory(workspace, '/help').get('commands', [])
    if not isinstance(commands, list) or any(not isinstance(item, dict) or
            not isinstance(item.get('name'), str) or not isinstance(item.get('aliases', []), list)
            for item in commands):
        raise RuntimeError('Native command inventory is malformed; nothing was forwarded.')
    advertised = {item['name'] for item in commands if isinstance(item, dict) and isinstance(item.get('name'), str)}
    aliases = {alias: item['name'] for item in commands if isinstance(item, dict) and item.get('name') in advertised
               for alias in item.get('aliases', []) if isinstance(alias, str)}
    # The documented canonical spelling wins over an older client's incomplete alias handling.
    aliases.update(ALIASES)
    canonical = aliases.get(name, name)
    known = next((item for item in COMMANDS if item['name'] == canonical), None)
    if canonical in advertised:
        return aliases
    if canonical in WORKFLOWS:
        if not text[token.end():].strip():
            raise ValueError('Usage: /' + canonical + ' <task>. No task was sent.')
        return aliases
    if known:
        raise RuntimeError('/' + name + ' resolves to /' + canonical + ', which ' + known['requirement'] +
            '. This installed CLI does not advertise a headless handler. Use it in the native Antigravity terminal; '
            'CLI-MODE did not dispatch it or change your session.')
    skills = native_inventory(workspace, '/skills').get('skills', [])
    if not isinstance(skills, list):
        raise RuntimeError('Native skill inventory is malformed; nothing was forwarded.')
    if any(isinstance(skill, dict) and skill.get('name') == canonical for skill in skills):
        return aliases
    import native_commands
    raise native_commands.unknown(name, 'Antigravity')


def prepare(settings):
    binary = shutil.which('agy')
    if not binary:
        raise RuntimeError('Teamwork requires the native Antigravity CLI; install and sign in separately.')
    result = subprocess.run([binary, 'models'], capture_output=True, text=True,
                            encoding='utf-8', timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    label = settings['modelName'] + ' (' + settings['effort'] + ')'
    matches = [line.split('\t', 1)[0] for line in result.stdout.splitlines()
               if '\t' in line and line.split('\t', 1)[1] == label]
    if result.returncode or len(matches) != 1:
        raise RuntimeError('Native CLI did not advertise the selected model/effort; ACP session was not changed.')
    return matches[0]


def marker(owned, suffix):
    return Path(owned['nativeControl'] + suffix)


def start(owned, prompt_path, timeout):
    marker(owned, '.cancel').unlink(missing_ok=True)
    binary = shutil.which('agy')
    if not binary:
        raise RuntimeError('Native Antigravity CLI is unavailable.')
    with Path(prompt_path).open(encoding='utf-8', newline='') as source:
        text = source.read()
    # The controller has already handled host controls and validated this
    # provider payload. Direct requests can explicitly target /help as well.
    slash_command = bool(COMMAND_TOKEN.match(text))
    args = [binary, '--output-format', 'stream-json',
            '--print-timeout', str(timeout) + 's', '--model', owned['nativeModel'],
            '--effort', owned['settings']['effort'].lower()]
    access = owned['settings']['access']
    if access == 'allow':
        args.append('--dangerously-skip-permissions')
    elif access == 'auto-edit':
        args += ['--mode', 'accept-edits']
    elif access != 'prompt':
        raise ValueError('Unsupported native access setting.')
    if owned.get('providerSession'):
        args += ['--conversation', owned['providerSession']]
    if slash_command:
        # CLI-handled commands such as /usage reject streaming stdin mode.
        # Pass one literal argv item, never a shell command or reconstructed prompt.
        if os.name == 'nt' and len(text) > 24000:
            raise ValueError('Native slash command exceeds the Windows argument limit; shorten this command.')
        args += ['-p', expand_alias(text, owned.get('nativeAliases'))]
    else:
        args += ['--input-format', 'stream-json']
    running = marker(owned, '.running')
    running.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(args, cwd=owned['workspace'], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    processes.bind(process)  # Never outlive the submitter that observes this turn.
    running.write_text(str(process.pid), encoding='ascii')
    # Preserve user text exactly, including line endings; never interpolate shell code.
    payload = '' if slash_command else json.dumps({'event': 'user', 'message': {'content': text}}) + '\n'
    def submit():
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    threading.Thread(target=submit, daemon=True).start()
    return process


def close(owned):
    cancel = marker(owned, '.cancel')
    cancel.parent.mkdir(parents=True, exist_ok=True)
    cancel.touch()
    until = time.monotonic() + 10
    while marker(owned, '.running').exists():
        if time.monotonic() > until:
            raise RuntimeError('Native CLI shutdown is still pending; inspect the owned operation before retrying off.')
        time.sleep(.1)


def public_event(event):
    """Only native agent responses are public; thoughts and tool events stay private."""
    if event.get('event') == 'step_update':
        step = event.get('step_update', {})
        text = step.get('text_delta')
        if step.get('step_type') == 'agent_response' and isinstance(text, str) and text:
            return {'type': 'message', 'text': text}
    if event.get('event') == 'result':
        result = event.get('result', {})
        if result.get('status') != 'SUCCESS':
            return {'type': 'error', 'message': result.get('error') or 'Native CLI request failed.'}
        return {'type': 'done', 'stopReason': 'end_turn'}
    return None
