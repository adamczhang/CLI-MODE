"""AUTO mode (Claude Code only): the user talks to Claude, and Claude hands work to the user's AUTO agent.

DIRECT is the other mode: /d sends the user's words to an agent. The user picks the AUTO agent (and an optional
backup) once; the choice is saved for every conversation in `auto-mode.json`, like the agent timeout. Switching a
conversation to AUTO starts those agents, which then wait for work, so a handoff never waits for an agent to
start. Codex has no AUTO: it has no background task that wakes the model when an agent finishes.

Which agent does what belongs to CLI-MODE's code in AUTO (starting, replacing and closing the AUTO agents);
what each task is belongs to Claude.
"""
import json
from pathlib import Path
import uuid

import adapters
import frontends
from operations import pending_work
from presentation import menu_block
from state import MODE_PAGES, STRENGTHS, agent_entry, agent_label, routing_mode

ROLES = ('agent', 'backup')
DEFAULT_STRENGTH = 'strong'
STRENGTH_LINES = {
    'normal': ('Claude decides what to hand off.',),
    'strong': ('Claude fixes small things itself;', 'bigger work goes to the agent.'),
    'max': ('Every change goes to the agent;', 'Claude plans and checks.'),
}
# Purposes a pending activation can carry: whose agent it starts, and whether it turns AUTO on.
PURPOSES = {'auto-on': 'agent', 'auto-agent': 'agent', 'auto-backup': 'backup'}


def config_path(root):
    return Path(root) / 'auto-mode.json'


def entry(value):
    """A saved agent choice, {agent, model, effort, access}, or None when it is missing or names no agent."""
    if not isinstance(value, dict) or not isinstance(value.get('agent'), str):
        return None
    try:
        adapters.module(value['agent'])
    except ValueError:
        return None
    return {key: value.get(key) for key in ('agent', 'model', 'effort', 'access')}


def load(root):
    """The user's AUTO choices: {agent, backup, strength}, each agent an entry() or None."""
    try:
        value = json.loads(config_path(root).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        value = {}
    value = value if isinstance(value, dict) else {}
    strength = value.get('strength')
    return {'agent': entry(value.get('agent')), 'backup': entry(value.get('backup')),
            'strength': strength if strength in STRENGTHS else DEFAULT_STRENGTH}


def save(root, config):
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, indent=2), encoding='utf-8')
    temporary.replace(path)


def entry_of(agent, settings):
    """The entry for an agent that just started with `settings` (an adapter selection)."""
    return {'agent': agent, 'model': settings.get('model'), 'effort': settings.get('effortValue'),
            'access': settings.get('access')}


def describe(root, choice, effort=True):
    """`Codex CLI, GPT-6 Sol, High` for a saved entry; `none` without one."""
    if not choice:
        return 'none'
    adapter = adapters.module(choice['agent'])
    try:
        selected = adapter.selection(root, choice['model'], choice['access'], choice.get('effort'))
    except (ValueError, KeyError, OSError, TypeError):
        selected = {}
    parts = [adapter.DISPLAY_NAME, selected.get('modelName') or choice.get('model') or 'its default model']
    if effort and selected.get('effort'):
        parts.append(selected['effort'])
    return ', '.join(parts)


def page_text(page, root, state):
    """One page as framed menu text (menu_block adds X. Exit)."""
    config = load(root)
    if page == 'mode':
        lines = ['Mode', '', 'Now: ' + routing_mode(state).upper(), '',
                 'DIRECT  You drive the agents with', '        /d. Answers come back as', '        written.',
                 'AUTO    Talk to Claude only. It', '        hands work to your AUTO', '        agent, checks it, reports.',
                 '', 'AUTO agent: ' + (describe(root, config['agent'], effort=False) if config['agent'] else
                                       'not chosen yet'),
                 '', '1. DIRECT', '2. AUTO', '3. AUTO settings', 'B. Back']
    elif page == 'auto-settings':
        lines = ['AUTO settings', '', '1. AUTO agent', '   ' + (describe(root, config['agent']) if config['agent']
                                                              else 'not chosen yet'),
                 '2. Backup agent', '   ' + describe(root, config['backup']),
                 '3. Delegation: ' + config['strength'].title(),
                 *('   ' + line for line in STRENGTH_LINES[config['strength']])]
        lines += (['4. Remove backup'] if config['backup'] else []) + ['B. Back']
    else:
        lines = ['Delegation', '', 'Now: ' + config['strength'].title(), '']
        for number, strength in enumerate(STRENGTHS, 1):
            lines += [str(number) + '. ' + strength.title(), *('   ' + line for line in STRENGTH_LINES[strength])]
        lines.append('B. Back')
    return menu_block('\n'.join(lines))


def roster_line(root, state, config):
    """`Codex CLI COD-7K (GPT-6 Sol, High), backup Grok Build GRO-4K`: the AUTO agents running here."""
    roster = state.get('auto') or {}
    parts = []
    for role in ROLES:
        session = roster.get(role)
        if session and agent_entry(state, session) and config.get(role):
            detail = describe(root, config[role]).split(', ', 1)
            text = agent_label(state, session) + (' (' + detail[1] + ')' if len(detail) > 1 else '')
            parts.append(('backup ' if role == 'backup' else '') + text)
    return ', '.join(parts)


def adopted_line(root, state, purpose, session):
    """One line for the activation card: what the agent that just started is now."""
    label = agent_label(state, session)
    if purpose == 'auto-on':
        return ('AUTO is on: Claude hands work to ' + roster_line(root, state, load(root)) + '. /d is off; '
                '/cli mode direct switches back.')
    what = 'backup agent' if purpose == 'auto-backup' else 'AUTO agent'
    return (label + ' is now your ' + what + (': it waits for work.' if routing_mode(state) == 'auto' else
                                              '. /cli mode auto hands it work.'))


def adopt(state, purpose, session, agent, settings, root):
    """An activation with an AUTO purpose finished: save its agent as the user's choice and put it in this
    conversation's AUTO roster. Returns the session it replaces there, if any."""
    role = PURPOSES[purpose]
    config = load(root)
    config[role] = entry_of(agent, settings)
    save(root, config)
    roster = dict(state.get('auto') or {})
    previous = roster.get(role)
    roster[role] = session
    state['auto'] = roster
    if purpose == 'auto-on':
        state['routingMode'] = 'auto'
    return previous if previous != session else None


class AutoMixin:
    """The Mode page, AUTO's settings, and starting the AUTO agents (Claude Code only)."""

    def mode_control(self, action, to=None, role='agent', agent=None, number=None):
        if action == 'page':
            return self.mode_page('mode')
        if action == 'set':
            return self.set_mode(to)
        if action == 'agent':
            return self.choose_auto_agent(role, agent)
        if action == 'clear-backup':
            return self.clear_backup()
        if action == 'strength':
            return self.set_strength(to)
        if action == 'choose':
            return self.mode_choose(number)
        if action == 'back':
            return self.mode_back()
        return self.mode_close()

    def mode_page(self, page='mode', message=None):
        with self.store.edit() as state:
            if (state.get('pending') or {}).get('stage') == 'verifying':
                raise RuntimeError('An agent is starting; wait for it before changing modes.')
            state['pending'] = dict(id=uuid.uuid4().hex, stage='menu', phase=page)
        result = dict(state, activationMenu=page_text(page, self.store.root, state))
        if message:
            result['message'] = message
        return result

    def close_page(self, state):
        if (state.get('pending') or {}).get('phase') in MODE_PAGES:
            state['pending'] = None

    def set_mode(self, to):
        if to not in ('auto', 'direct'):
            raise ValueError('Choose auto or direct.')
        if to == 'direct':
            with self.store.edit() as state:
                self.close_page(state)
                was = routing_mode(state)
                state['routingMode'] = 'direct'
            running = ' Your agents keep running; /d sends them work.' if state['active'] else \
                ' /cli starts an agent; /d sends it work.'
            return dict(state, message=('DIRECT is on' if was == 'auto' else 'DIRECT is already on') + ': you drive '
                        'the agents with /d, and Claude answers everything else.' + running)
        config = load(self.store.root)
        if not config['agent']:
            return self.open_picker('auto-on', message='Choose your AUTO agent: the agent Claude hands work to. It '
                                    'starts now and waits for work.')
        return self.start_auto()

    def start_auto(self):
        """Start whichever AUTO agents are not already running here, then turn AUTO on."""
        config = load(self.store.root)
        started = []
        for role in ROLES:
            choice = config[role]
            state = self.store.read()
            session = (state.get('auto') or {}).get(role)
            current = agent_entry(state, session) if session else None
            if not choice or (current and current.get('ready') and current.get('backend') == choice['agent']):
                continue  # Not chosen, or already running and waiting.
            self.start_role(role, choice)
            started.append(role)
        with self.store.edit() as state:
            self.close_page(state)
            was = routing_mode(state)
            state['routingMode'] = 'auto'
            lead = (state.get('auto') or {}).get('agent')
            target = agent_entry(state, lead) if lead else None
            if target:  # The AUTO agent is the current one, even when the backup started last.
                state.update(main=target['name'], settings=target['settings'], backend=target['backend'])
        line = roster_line(self.store.root, state, config)
        opening = 'AUTO is already on' if was == 'auto' and not started else 'AUTO is on'
        return dict(state, message=opening + ': Claude hands work to ' + line + '. /d is off; /cli mode direct '
                    'switches back.')

    def start_role(self, role, choice):
        """Start one AUTO agent with its saved settings, the way bind starts one (in the hook, 13-42 s)."""
        purpose = 'auto-backup' if role == 'backup' else 'auto-agent'
        self.use(choice['agent'])
        self.frontend(choice['agent'], purpose=purpose)
        return self.activate(choice['model'], choice['access'], effort=choice.get('effort'), agent=choice['agent'],
                             require_hooks=True)

    def open_picker(self, purpose, agent=None, message=None):
        """The activation flow (agent list, then its page), marked so its activation becomes an AUTO agent."""
        result = self.frontend(agent or 'home', purpose=purpose)
        if message:
            result['message'] = message
        return result

    def choose_auto_agent(self, role, agent=None):
        """`/cli mode agent|backup [<agent> [<model>]]`: the picker, that agent's page, or a direct choice."""
        purpose = 'auto-backup' if role == 'backup' else 'auto-agent'
        typed = ((self.store.read().get('turnRoute') or {}).get('choice') or '').strip()
        what = 'backup agent' if role == 'backup' else 'AUTO agent'
        if not agent or not typed:
            return self.open_picker(purpose, agent, message='Choose your ' + what + '. It starts now' +
                                    (' and waits for work.' if self.auto_is_on() else '.'))
        adapter = self.use(agent)
        settings = adapter.selection(self.store.root, **adapter.DEFAULTS)
        options = frontends.phase_options(self.store.root, agent, 'model', settings, adapter.catalog(self.store.root))
        matches = frontends.match_choice(options, typed, 'model')
        if len(matches) != 1:
            return {'message': ('Several ' if matches else 'No ') + adapter.DISPLAY_NAME + ' models match "' + typed +
                    '". /cli mode ' + role + ' ' + agent + ' opens its page to choose.'}
        choice = {'agent': agent, 'model': matches[0], 'access': settings['access'],
                  'effort': settings.get('effortValue') if matches[0] == settings['model'] else None}
        if self.auto_is_on():
            self.start_role(role, choice)  # adopt() saves it and replaces the old one.
            return self.start_auto()
        config = load(self.store.root)
        config[role] = choice
        save(self.store.root, config)
        return {'message': 'Your ' + what + ' is now ' + describe(self.store.root, choice) + '. It starts when AUTO '
                'is on: /cli mode auto.'}

    def auto_is_on(self):
        return routing_mode(self.store.read()) == 'auto'

    def clear_backup(self):
        config = load(self.store.root)
        config['backup'] = None
        save(self.store.root, config)
        with self.store.edit() as state:
            roster = dict(state.get('auto') or {})
            old = roster.pop('backup', None)
            state['auto'] = roster
        closed = self.retire(old) if old and routing_mode(state) == 'auto' else None
        return self.mode_page('auto-settings', message='Backup removed.' + (' ' + closed if closed else ''))

    def set_strength(self, to):
        if to not in STRENGTHS:
            raise ValueError('Choose normal, strong or max.')
        config = load(self.store.root)
        config['strength'] = to
        save(self.store.root, config)
        return self.mode_page('auto-settings', message='Delegation: ' + to.title() + '.')

    def mode_choose(self, number):
        page = (self.store.read().get('pending') or {}).get('phase')
        config = load(self.store.root)
        actions = {
            'mode': {1: lambda: self.set_mode('direct'), 2: lambda: self.set_mode('auto'),
                     3: lambda: self.mode_page('auto-settings')},
            'auto-settings': {1: lambda: self.choose_auto_agent('agent'), 2: lambda: self.choose_auto_agent('backup'),
                              3: lambda: self.mode_page('auto-strength'),
                              **({4: self.clear_backup} if config['backup'] else {})},
            'auto-strength': {index: (lambda value=value: self.set_strength(value))
                              for index, value in enumerate(STRENGTHS, 1)},
        }.get(page)
        if not actions or number not in actions:
            raise ValueError('Choose a number from the page.')
        return actions[number]()

    def mode_back(self):
        page = (self.store.read().get('pending') or {}).get('phase')
        if page == 'auto-strength':
            return self.mode_page('auto-settings')
        if page == 'auto-settings':
            return self.mode_page('mode')
        if routing_mode(self.store.read()) == 'auto':
            return self.mode_close()  # In AUTO, /cli is the Mode page: there is no agent list behind it.
        return self.frontend('home')

    def mode_close(self):
        with self.store.edit() as state:
            self.close_page(state)
        return dict(state, message='Mode page closed. /cli mode opens it again.')

    def retire(self, session):
        """Close an AUTO agent that was replaced, unless it is still working (then say so)."""
        state = self.store.read()
        target = agent_entry(state, session)
        if not target:
            return None
        name = target.get('alias') or session
        if pending_work(state, session=session):
            return name + ' is still working and keeps running; it closes with /cli mode direct and /cli close.'
        self.close(name)
        return name + ' was closed.'
