"""Shared menus and read-only dependency checks; installer launch requires approval."""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import acpx
import adapters
import host
import installer
from state import backend_records, lock
from presentation import access_display, access_note, effort_display, effort_key, effort_rank, menu_block, options_menu
from progress import DEFAULT_PROGRESS_MODE

PLUGIN = Path(__file__).resolve().parents[1]
RELOAD_NOTICE = ['**This task loaded an older plugin.', 'Start a new task, then run /cli.**']
# Claude Code keeps a session's hooks on the plugin copy it started with until
# /reload-plugins; it holds back hooks until the folder's workspace is trusted.
CLAUDE_RELOAD_NOTICE = ['**This session loaded an older plugin.', 'Run /reload-plugins, then /cli.**']
CLAUDE_HOOK_ADVICE = ['**Trust this folder, check /hooks,', 'then start a new session.**']
CODEX_HOOK_ADVICE = ['**Desktop: Plugins > CLI-MODE >', 'Hooks > Review / Trust all.**',
                     '**Approve, then recheck setup.**']


def reload_notice():
    return CLAUDE_RELOAD_NOTICE if host.claude() else RELOAD_NOTICE

# Non-Windows probe list per backend. None means the shared ACPX launcher.
BACKEND_TOOLS = {item['id']: tuple(tuple(probe) for probe in item['prerequisites'])
                 for item in backend_records()}


def backends():
    return backend_records()


def receipt_path(root, backend):
    if backend not in {item['id'] for item in backends()}:
        raise ValueError('Unknown CLI-MODE backend.')
    return Path(root) / 'onboarding' / (backend + '.json')


def confirmed(root, backend):
    path = receipt_path(root, backend)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value.get('schema') == 1 and value.get('confirmed') is True
    except (OSError, ValueError, AttributeError):
        return False


def routing_readiness(state):
    """Observe host evidence only; never manufacture a hook receipt."""
    seen = state.get('hookSeen') or {}
    scope = 'session' if host.claude() else 'task'
    if not seen:
        reason = 'missing'
        message = 'No routing hook has run in this ' + scope + '.'
    elif seen.get('plugin') != str(PLUGIN):
        reason = 'different-plugin'
        message = 'This ' + scope + ' last received a hook from a different plugin copy.'
    elif not 0 <= time.time() - seen.get('time', 0) <= 600:
        reason = 'stale'
        message = 'The routing hook observation is older than 10 minutes or has an invalid timestamp.'
    else:
        return dict(ready=True, reason=None, message='Routing hook verified for this ' + scope + '.')
    if host.claude():
        advice = (' In Claude Code, run /reload-plugins or start a new session so hooks load from this plugin copy.'
                  if reason == 'different-plugin' else
                  ' In Claude Code, make sure the cli-mode plugin is enabled (/plugin), accept the workspace trust'
                  ' prompt for this folder, and check /hooks (hooks do not run with disableAllHooks or --bare).'
                  ' Then start a new session.')
    else:
        advice = (' In Codex Desktop, open Plugins > CLI-MODE > Hooks, choose Review and approve the definitions'
                  ' (or Trust all for this plugin). Return here and recheck setup. If approved hooks still do not'
                  ' run, start a new task to reload the plugin. Installing a plugin does not automatically trust'
                  ' its hooks.')
    return dict(ready=False, reason=reason, message=message + advice)


def first_start(root, agent, routing=None):
    agents = backends() if agent == 'home' else [dict(id=agent)]
    return (not any(confirmed(root, item['id']) for item in agents) or
            routing is not None and routing.get('reason') in ('missing', 'different-plugin'))


def access_readiness():
    if host.claude():
        # Claude Code has no Full Access profile to check. CLI-MODE's hook
        # approves only its own controller commands for this session.
        return dict(ready=True, status='enabled', message='Claude Code runs CLI-MODE commands through its shell tools.')
    profile = os.environ.get('CODEX_PERMISSION_PROFILE', '')
    enabled = profile == ':danger-full-access'
    known_restricted = profile in (':workspace', ':read-only', ':workspace-write')
    return dict(ready=enabled, status='enabled' if enabled else 'disabled' if known_restricted else 'unknown',
                message=('Full Access in Codex is enabled.' if enabled else
                         'Select Full Access in this Codex task, then run /cli again.' if known_restricted else
                         'Full Access could not be verified from the host permission profile. Check this task\'s permissions and select Full Access, then run /cli again.'))


def setup_menu(result, routing, access=None):
    access = access if access is not None else access_readiness()
    agent = result.get('backend', 'agy')
    lines = ['CLI-MODE', 'Setup CLI Agent', adapters.module(agent).DISPLAY_NAME, '']
    for item in result.get('checks', []):
        lines.append(item['name'] + ': ' + ('Installed' if item['installed'] else 'Needs installation'))
    if not result['confirmed'] and result.get('checks'):
        lines += ['', 'I. Install missing components', 'M. Manual installation help', 'Installation needs your approval.']
    lines += ['', 'Hooks: ' + ('On' if routing['ready'] else '!Attention!')]
    if not routing['ready']:
        if routing.get('reason') == 'different-plugin':
            lines += reload_notice()
        else:
            lines += CLAUDE_HOOK_ADVICE if host.claude() else CODEX_HOOK_ADVICE
    if not host.claude():  # Claude Code has no Full Access setting.
        lines += ['Full Access: ' + ('On' if access['ready'] else '!Attention!')]
        if not access['ready']:
            lines += ['*Full access in codex is required*']
    lines += ['', 'R. Recheck setup', 'B. Back to agents']
    return menu_block('\n'.join(lines))


def menu(root, agent, settings=None, routing=None, access=None, page=1):
    blocked = routing is not None and not routing['ready']
    if agent != 'home':
        receipt_path(root, agent)
    if first_start(root, agent, routing) or access is not None and not access['ready']:
        agents = backends() if agent == 'home' else [item for item in backends() if item['id'] == agent]
        warning = [] if access is not None and access['ready'] else ['*Full access in codex is required*']
        if blocked and routing.get('reason') == 'different-plugin':
            warning += reload_notice()
        title = 'Setup needs attention' if any(confirmed(root, item['id']) for item in backends()) else 'New User Detected.'
        built = options_menu(title, [item['displayName'] for item in agents],
                             lead=['Setup CLI Agent.', *warning, ''], page=page)
        return menu_block(built['text'])
    if agent == 'home':
        agents = backends()
        labels = [item['displayName'] + ('' if confirmed(root, item['id']) else ' (Setup needed)') for item in agents]
        built = options_menu('Select CLI Agent', labels, page=page,
                             lead=['Hook check pending.', ''] if blocked else [])
        return menu_block(built['text'])
    receipt_path(root, agent)
    text = settings_text(agent, settings)
    text += '\n\n1. Recheck routing' if blocked else '\n\n1. Yes - use these defaults'
    text += '\n2. Change defaults'
    if blocked:
        text += '\n\nHook check pending.\nActivation is unavailable.'
    return menu_block(text + '\nB. Back to agents')


def settings_text(agent, settings=None):
    """Structured settings fields; adapter prose is never parsed as a UI API."""
    adapter = adapters.module(agent)
    selected = settings or adapter.selection(adapter.CATALOG.parent, **adapter.DEFAULTS)
    return '\n'.join(['CLI-MODE', 'Agent Settings', '', adapter.DISPLAY_NAME,
                      'Model: ' + selected['modelName'], 'Effort: ' + effort_display(selected['effort']),
                      'Access: ' + access_display(selected['access'], selected.get('accessName')) +
                      access_note(selected['access'])])


def selected_key(root, backend, phase, settings, snapshot=None):
    selected = settings or {}
    if phase == 'access':
        return selected.get('access')
    data = snapshot if snapshot is not None else adapters.module(backend).catalog(root)
    model = selected.get('model')
    family = next((f for f in data['modelFamilies'] if f.get('modelId') == model
                   or any(e.get('modelId') == model for e in f.get('efforts', []))), None)
    if not family:
        return None
    if phase == 'model':
        return family.get('modelId') or family['efforts'][0]['modelId']
    return selected.get('effortValue') if family.get('modelId') else model


def active_settings_menu(agent, settings, progress=DEFAULT_PROGRESS_MODE):
    text = settings_text(agent, settings)
    return menu_block(text + '\nProgress: ' + progress.title() +
        '\n\n1. Change model\n2. Change effort\n3. Change access\n4. Toggle activity progress\nX. Close settings')


def phase_options(root, backend, phase, settings=None, snapshot=None):
    """Advertised choices for one setup phase, as (label, value) pairs."""
    adapter = adapters.module(backend)
    data = snapshot if snapshot is not None else adapter.catalog(root)
    if phase == 'model':
        label = getattr(adapter, 'model_label', str)
        return [(label(family['name']), family.get('modelId') or family['efforts'][0]['modelId'])
                for family in data['modelFamilies']]
    if phase == 'effort':
        chosen = (settings or {}).get('model') or adapter.DEFAULTS['model']
        family = next((f for f in data['modelFamilies']
                       if f.get('modelId') == chosen
                       or any(e.get('modelId') == chosen for e in f.get('efforts') or [])), None)
        if family is None:
            raise ValueError('No advertised effort control for the selected model; refresh the catalog.')
        options = family.get('efforts') or []
        if not options:
            # Never invent levels for a model that advertises none.
            return [('Provider default', None)]
        options = sorted(options, key=lambda item: effort_rank(item['name'], item.get('value')))
        return [(effort_display(item['name'], item.get('value')), item.get('value') or item['modelId'])
                for item in options]
    if phase == 'access':
        # Present the shared aliases in one documented order for every backend,
        # regardless of the order a runtime happens to advertise them in.
        order = {alias: index for index, alias in enumerate(ACCESS_ORDER)}
        options = sorted(data['accessControl']['options'],
                         key=lambda item: order.get(item['access'], len(order)))
        return [(access_display(item['access'], item['nativeName']) + access_note(item['access']), item['access'])
                for item in options]
    raise ValueError('Unknown setup phase.')


def _normal(text):
    return re.sub(r'[^a-z0-9.]+', '', str(text).casefold())


def match_choice(options, text, phase):
    """Advertised values matching a typed setting, e.g. `opus`, `extra high`, `allow`.

    Exact matches (on the value, the shown label, an effort's shared name or an
    access alias or provider name) win; otherwise every typed word must appear
    in one option. Returns the distinct matching values; the caller applies a
    unique match and shows the menu otherwise.
    """
    wanted = _normal(text)
    if not wanted:
        return []

    def names(label, value):
        found = {_normal(value), _normal(label)}
        if phase == 'effort':
            key = effort_key(label, value if isinstance(value, str) else None)
            if key:
                found.add(key)
        if phase == 'access':
            head = label.split(' \u2014 ')[0]
            found |= {_normal(head.split(' (')[0]), _normal(head[head.find('(') + 1:head.rfind(')')] if '(' in head else '')}
        return found - {''}

    target = effort_key(text) if phase == 'effort' else None
    exact = [value for label, value in options if wanted in names(label, value) or (target and target in names(label, value))]
    if exact:
        return list(dict.fromkeys(exact))
    words = [_normal(word) for word in str(text).split() if _normal(word)]
    loose = [value for label, value in options
             if words and all(word in _normal(label) + ' ' + _normal(value) for word in words)]
    return list(dict.fromkeys(loose))


# The documented access order, shared by every backend that advertises them.
ACCESS_ORDER = ('allow', 'auto-edit', 'prompt')
PHASE_TITLES = {'model': 'Select Model', 'effort': 'Select Reasoning Effort',
                'access': 'Select Access Level'}
PHASE_TAILS = {'model': ['R. Refresh models', 'B. Back'],
               'effort': ['B. Back'], 'access': ['B. Back']}


def phase_menu(root, backend, phase, settings=None, page=1, snapshot=None, tuning=False):
    """One setup phase, paginated so a long provider catalog stays readable."""
    choices = phase_options(root, backend, phase, settings, snapshot)
    current = selected_key(root, backend, phase, settings, snapshot)
    labels = []
    for label, value in choices:
        default = value is not None and str(value).casefold() == str(current).casefold()
        labels.append(label + ('  (current)' if default else ''))
    built = options_menu(PHASE_TITLES[phase], labels, tail=PHASE_TAILS[phase], page=page)
    if tuning:
        built['text'] += '\nX. Close settings'
    return dict(built, menu=menu_block(built['text']), phase=phase, backend=backend,
                choices=[{'label': l, 'value': v} for l, v in choices])


def installation_check(backend):
    """Probe this backend's prerequisites only; one backend never vouches for another."""
    if backend not in {item['id'] for item in backends()}:
        raise ValueError('Unknown CLI-MODE backend.')
    adapters.module(backend)
    installer.refresh_paths()
    if os.name == 'nt':
        return dict(installer.call('Scan', backend=backend), backend=backend)
    checks = []
    for name, binary in BACKEND_TOOLS[backend]:
        try:
            if binary is None:
                install = acpx.runtime_install()
                command = [install['node'], str(Path(install['package']) / 'dist/cli.js')]
            else:
                found = shutil.which(binary)
                if not found:
                    raise RuntimeError(name + ' is missing from PATH.')
                command = [found]
            result = subprocess.run(command + ['--version'], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError(name + ' could not run --version; repair its installation.')
            checks.append(dict(name=name, installed=True))
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            checks.append(dict(name=name, installed=False, reason=str(exc)))
    return dict(backend=backend, confirmed=all(item['installed'] for item in checks), checks=checks,
                note='Installation only. ACP runtime connectivity and sign-in are verified at activation.')


def check_and_save(root, backend, check=None):
    path = receipt_path(root, backend)
    result = (check or installation_check)(backend)
    value = dict(result, schema=1, checkedAt=time.time())
    with lock(path.with_suffix('.lock')):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, indent=2)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    if not value['confirmed']:
        value['message'] = 'Install or repair missing prerequisites: choose I for guided setup, M for manual links, or install separately and rerun /cli.'
    else:
        value['message'] = 'First Time User Check passed. ACPX and ' + adapters.module(backend).DISPLAY_NAME + ' are installed.'
    return value
