"""Golden record of everything CLI-MODE shows Codex, so later changes cannot drift it.

Each scenario drives the real routing hook and controller with an offline
backend, and records exactly what Codex would receive: hook JSON (the
additionalContext it reads) and controller JSON plus the HTML views it renders.
Paths, IDs, times and process IDs are replaced with stable placeholders.

    python checks/codex_golden.py --write   # record (only at a reviewed base commit)
    python -m unittest checks/test_codex_golden.py   # compare
"""
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_controller import PLUGIN, hook  # noqa: E402  (sets the offline test environment)
from test_relay import ScriptedBackend, activity  # noqa: E402
import controller as controller_module  # noqa: E402
from controller import Controller  # noqa: E402
from queue_worker import QueueMixin  # noqa: E402
from state import Store  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'codex-golden.json'
THREAD = 'golden-thread'
# Values that differ run to run: wall-clock times, durations and process IDs.
VOLATILE = ('time', 'capturedAt', 'started', 'startedAt', 'createdAt', 'updatedAt', 'completedAt',
            'settledAt', 'finishedAt', 'lastEventAt', 'lastPublicAt', 'observedAt', 'at', 'since',
            'idleSeconds', 'withoutPublicUpdateSeconds', 'ageSeconds', 'age', 'elapsed', 'waitSeconds',
            'pid', 'submitterPid', 'runnerPid', 'expires', 'expiresAt', 'mtime')
VOLATILE_IN_TEXT = re.compile(r'("(?:' + '|'.join(VOLATILE) + r'|[A-Za-z]+At)": )-?\d+(?:\.\d+)?(?:e-?\d+)?')


def volatile(key):
    """Wall-clock times, durations and process IDs: every `…At` key counts."""
    return key in VOLATILE or (isinstance(key, str) and key.endswith('At') and key[:1].islower())


class Ids:
    """Deterministic uuid4: the n-th call returns UUID(int=n)."""
    def __init__(self):
        self.count = 0

    def __call__(self):
        self.count += 1
        return uuid.UUID(int=self.count)


def variants(path):
    """Every spelling of a path that can appear in output, longest first."""
    text = str(path)
    forms = {text, path.as_posix(), json.dumps(text)[1:-1], json.dumps(path.as_posix())[1:-1]}
    return sorted(forms, key=len, reverse=True)


class Normalizer:
    def __init__(self, **paths):
        self.pairs = [(form, '<' + name.upper() + '>')
                      for name, path in sorted(paths.items(), key=lambda item: -len(str(item[1])))
                      for form in variants(Path(path))]

    def __call__(self, value, key=None):
        if volatile(key) and isinstance(value, (int, float)) and not isinstance(value, bool):
            return '<' + key + '>'
        if isinstance(value, str):
            for old, new in self.pairs:
                value = value.replace(old, new)
            return VOLATILE_IN_TEXT.sub(lambda match: match.group(1) + '"<volatile>"', value)
        if isinstance(value, dict):
            # Keys can be paths too (views are keyed by file).
            return {self(name): self(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [self(item) for item in value]
        return value


class Session:
    """One disposable conversation: workspace, state root and controller."""
    def __init__(self, root):
        self.root = root
        self.workspace = root / 'work space & co'
        self.workspace.mkdir()
        self.store = Store(THREAD, self.workspace, root / 'data')
        self.backend = ScriptedBackend()
        self.control = Controller(self.store, self.backend)

    def event(self, name, **fields):
        return hook.handle(dict(session_id=THREAD, cwd=str(self.workspace), hook_event_name=name, **fields),
                           self.store.root)

    def prompt(self, text):
        return self.event('UserPromptSubmit', prompt=text)

    def activate(self):
        self.control.frontend()
        self.control.activate('gemini-3.8-flash-high', 'allow')

    def cli(self, *args):
        """The controller exactly as Codex runs it, in process, with its printed JSON."""
        argv = ['controller.py', '--thread', THREAD, '--workspace', str(self.workspace),
                '--data-root', str(self.store.root), *args]
        out = io.StringIO()
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(out):
            controller_module.main()
        result = json.loads(out.getvalue())
        views = {}
        for view in _views(result):
            views[view['path']] = Path(view['path']).read_text(encoding='utf-8')
        return dict(result=result, views=views)


def _views(value):
    if isinstance(value, dict):
        if isinstance(value.get('path'), str) and value.get('format') == 'inline-html':
            yield value
        for item in value.values():
            yield from _views(item)
    elif isinstance(value, list):
        for item in value:
            yield from _views(item)


def prompt_step(session, text):
    output = session.prompt(text)
    route = (session.store.read().get('turnRoute') or {}).get('route')
    return dict(step=text, route=route, output=output)


def prompts(session, *texts):
    return [prompt_step(session, text) for text in texts]


def turns(session, *items):
    """Prompts, interleaved with the controller calls the host model makes in
    response (a (label, callable) pair), so menu replies reach their real routes.
    Calls that would scan this machine's installed CLIs are never simulated."""
    steps = []
    for item in items:
        if isinstance(item, str):
            steps.append(prompt_step(session, item))
        else:
            label, call = item
            steps.append(dict(step='host runs ' + label, output=call()))
    return steps


# Scenarios: each returns a list of {step, output}. Order matters: routes depend on earlier turns.

def inactive_basics(s):
    steps = [dict(step='SessionStart startup', output=s.event('SessionStart', source='startup')),
             dict(step='SessionStart resume', output=s.event('SessionStart', source='resume'))]
    return steps + prompts(s, 'hello there', '/client tools', '/d do the task', '/cli unknown-verb',
                           '/cli queue', '/cli cancel', '/cli resume', '/cli mode', '/cli model',
                           '/cli progress quiet', '/cli progress loud', '/cli stop', '/cli bind nothing')


def home_flow(s):
    home = ('frontend --agent home', lambda: s.control.frontend('home'))
    steps = turns(s, '/cli', home, '2', 'b', '>', '$CLI', home)
    steps.append(dict(step='SessionStart compact with a pending menu', output=s.event('SessionStart', source='compact')))
    return steps + turns(s, 'x')


def frontend_each_agent(s):
    return prompts(s, '/cli agy', '/cli claude', '/cli grok', '/cli cursor', '/cli copilot', '/cli codex',
                   '/cli cla', '$cli GRO')  # Three-letter tags name the same agents.


def setup_replies(s):
    return turns(s, '/cli', ('frontend --agent home', lambda: s.control.frontend('home')), '1', 'I', 'r', 'M')


def reactivation_menu(s):
    s.activate()
    return turns(s, '/cli stop', ('off', s.control.off), '/cli agy',
                 ('frontend --agent agy', lambda: s.control.frontend('agy')), '1', 'x')


def help_flow(s):
    return prompts(s, '/help', 'what now', 'x', '$HELP', '/help extra')


def bind_routes(s):
    return prompts(s, '/cli bind agy', '/cli bind claude', '/cli bind cursor')


def active_direct(s):
    s.activate()
    steps = prompts(s, 'hello there', '/d Explain the parser', '/d', '$d  two  spaces kept')
    steps.append(dict(step='PreToolUse Agent after /d', output=s.event(
        'PreToolUse', tool_name='Agent', tool_input={'prompt': 'x'})))
    steps.append(dict(step='SessionStart compact after /d', output=s.event('SessionStart', source='compact')))
    steps += prompts(s, 'plain host turn')
    steps.append(dict(step='PreToolUse Agent on host turn', output=s.event(
        'PreToolUse', tool_name='Agent', tool_input={'prompt': 'x'})))
    return steps + prompts(s, '/cli queue', '/cli cancel', '/cli resume')


def active_settings(s):
    s.activate()
    c = s.control
    settings = ('settings', c.settings_menu)
    return turns(s, '/cli menu', settings, '1', ('tune --phase model', lambda: c.tune('model')),
                 'x', ('settings --dismiss', lambda: c.settings_menu(True)),
                 '/cli menu', settings, '2', ('tune --phase effort', lambda: c.tune('effort')),
                 '/cli menu', settings, '4', ('progress --choice quiet', lambda: c.progress('quiet')),
                 '/cli menu', settings, 'x', ('settings --dismiss', lambda: c.settings_menu(True)),
                 '/cli mode', '/cli model gemini-3.7-flash',
                 ('tune --phase model --apply', lambda: c.tune_choice('model')),
                 '/cli effort high', ('tune --phase effort --apply', lambda: c.tune_choice('effort')),
                 '/cli access read', '/cli permissions allow',
                 '/cli progress activity', ('progress --choice activity', lambda: c.progress('activity')))


def saved_passthrough(s):
    """A conversation saved in Passthrough mode (removed) opens in Direct: plain text stays with Codex."""
    s.activate()
    with s.store.edit() as state:
        state['routingMode'] = 'passthrough'
    steps = prompts(s, 'Explain the parser')
    steps.append(dict(step='PreToolUse spawn_agent on host turn', output=s.event(
        'PreToolUse', tool_name='spawn_agent', tool_input={})))
    steps += prompts(s, '/cli mode passthrough', '/d back to direct', '/cli stop', 'after stop')
    return steps


def compaction_restores(s):
    """Each controller result that records a turn route, then the compaction restore of it."""
    s.activate()
    c = s.control
    steps = []

    def then_compact(label, call):
        steps.append(dict(step='host runs ' + label, output=call()))
        restored = (s.store.read().get('turnRoute') or {}).get('route')
        steps.append(dict(step='SessionStart compact', route=restored, output=s.event('SessionStart', source='compact')))

    steps.append(prompt_step(s, '/d compaction task'))
    request = s.store.read()['turnRoute']['requestId']
    s.backend.events = [dict(type='message', text='Done.\n')]
    then_compact('send --request', lambda: c.send_request(request, output=lambda event: None))
    steps.append(prompt_step(s, '/cli menu'))
    then_compact('settings', c.settings_menu)
    then_compact('settings --dismiss', lambda: c.settings_menu(True))
    then_compact('progress --choice quiet', lambda: c.progress('quiet'))
    steps.append(prompt_step(s, '/cli model gemini-3.7-flash'))
    then_compact('tune --phase model --apply', lambda: c.tune_choice('model'))
    steps.append(prompt_step(s, '/help'))
    steps.append(dict(step='SessionStart compact', route='help', output=s.event('SessionStart', source='compact')))
    return steps


def resume_blocked(s):
    s.activate()
    s.prompt('/d Queued task')
    blocked = {'worker': 'blocked', 'queued': 1, 'message': 'Inspect and reconcile the earlier operation before resuming the queue.'}
    with patch.object(QueueMixin, 'ensure_pump', return_value=blocked):
        first = s.prompt('/cli resume')
    with patch.object(QueueMixin, 'ensure_pump', side_effect=OSError('process launch denied')):
        second = s.prompt('/cli resume')
    return [dict(step='/cli resume (blocked)', output=first), dict(step='/cli resume (start failed)', output=second)]


def controller_menus(s):
    steps = [dict(step='cli commands', output=s.cli('--menu-output', str(s.root / 'v' / 'help.html'), 'commands')),
             dict(step='cli frontend home', output=s.cli('--menu-output', str(s.root / 'v' / 'home.html'),
                                                         'frontend', '--agent', 'home'))]
    s.activate()
    steps += [dict(step='cli settings', output=s.cli('--menu-output', str(s.root / 'v' / 'settings.html'), 'settings')),
              dict(step='cli queue', output=s.cli('queue')),
              dict(step='cli resume', output=s.cli('resume'))]
    return steps


def controller_relay(s):
    s.activate()
    s.backend.events = [
        dict(type='plan', entries=[dict(content='Inspect', status='completed'),
                                   dict(content='Fix <b>it</b>', status='in_progress')]),
        activity('a'), activity('b', kind='execute', status='in_progress', title=None),
        dict(type='message', text='Found the bug in the parser.\n'),
        dict(type='message', text='Line 3 now handles empty input.\n')]
    request = s.control.send('/d task', output=lambda event: None)['requestId']
    return [dict(step='cli relay', output=s.cli('relay', '--request', request, '--view-dir', str(s.root / 'v'),
                                                 '--wait', '1')),
            dict(step='cli resume after completion', output=s.cli('resume'))]


def several_agents(s):
    """Two named agents: spawn a second, a named /d, the list, the current agent, the close chooser, close one."""
    s.activate()
    c = s.control
    first = s.store.read()['owned'][0]['alias']

    def spawn():  # What `bind --agent agy --name ELON` does, without scanning this machine's CLIs.
        c.frontend('agy')
        with s.store.edit() as state:
            state['pending']['name'] = 'ELON'
        return c.activate('gemini-3.8-flash-high', 'allow', agent='agy')

    def named_send():
        request = s.store.read()['turnRoute']['requestId']
        s.backend.events = [dict(type='message', text='Parsed.\n')]
        return c.send_request(request, output=lambda event: None)

    return turns(s, '/cli spawn agy elon', ('bind --agent agy --name ELON', spawn),
                 '/cli spawn agy ' + first.replace('-', ''), '/cli list', ('agents', c.agents),
                 '/d elon Explain the parser', ('send --request', named_send),
                 '/d -' + first[-2:] + ' short form', '/d COD-99 unknown', '/cli use ' + first,
                 ('use --name ' + first, lambda: c.make_current(first)), '/cli agents max 2',
                 ('agents --max 2', lambda: c.agents(2)), '/cli spawn agy', '/cli settings elon',
                 '/cli close', ('close', c.close), 'x', '/cli close', ('close', c.close), '2',
                 ('close --name ELON', lambda: c.close('ELON')), '/cli close', '/cli stop elon')


def agent_tools(s):
    """One prompt to two agents, a tag as a target, timeouts, a diff without a receipt, and attach."""
    s.activate()
    c = s.control
    first = s.store.read()['owned'][0]['alias']
    c.frontend('agy')
    with s.store.edit() as state:
        state['pending']['name'] = 'ELON'
    c.activate('gemini-3.8-flash-high', 'allow', agent='agy')
    return turns(s, '/d elon,' + first.lower() + ' review the parser', '/d elon, please check it',
                 '/d agy fix it', '/d hi, can you check', '/d elon,nobody check',
                 '/cli timeout', ('timeout', c.timeout), '/cli timeout 90m', '/cli timeout elon 2h',
                 '/cli timeout 1', '/cli diff', ('diff', c.diff), '/cli diff elon', '/cli attach',
                 ('attach', c.attach), '/cli attach 3')


SCENARIOS = [inactive_basics, home_flow, frontend_each_agent, setup_replies, reactivation_menu, help_flow,
             bind_routes, active_direct, active_settings, saved_passthrough, compaction_restores, resume_blocked,
             controller_menus, controller_relay, several_agents, agent_tools]


def record():
    """Run every scenario in its own temporary root and return normalized output."""
    results = {}
    for scenario in SCENARIOS:
        with tempfile.TemporaryDirectory() as temp, patch('uuid.uuid4', Ids()):
            root = Path(temp).resolve()
            session = Session(root)
            normalize = Normalizer(root=root, plugin=PLUGIN)
            results[scenario.__name__] = normalize(scenario(session))
    return results


if __name__ == '__main__':
    data = record()
    if '--write' in sys.argv:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, indent=1, ensure_ascii=True, sort_keys=True) + '\n',
                           encoding='utf-8', newline='\n')
        print('Wrote', FIXTURE, 'with', sum(len(steps) for steps in data.values()), 'steps.')
    else:
        print(json.dumps(data, indent=1, ensure_ascii=True, sort_keys=True))
