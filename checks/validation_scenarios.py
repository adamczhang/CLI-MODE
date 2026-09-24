"""The shared end-to-end scenario for both hosts (validation plan section 7.3).

`claude_user_validation.py` (Claude Code) and `codex_user_validation.py` (Codex) run the same
STEPS through `run_steps()`. A harness supplies a host object with:

    host         'claude-code' or 'codex'
    send(prompt, scenario, kill_after=None) -> turn dict (problems, notes, modelTurns, ...)
    shown(turn)  what the user saw, as text (Codex views are read back as their rows)
    state()      the conversation's CLI-MODE state
    words(request_id)  the agent's last message for a request, from the public event log
    project      the throwaway project folder

Each step names the feature IDs it covers (FEATURES, from the plan's section 3), so
`validation_report.py` can build the feature x host x agent matrix from every run's
summary.json. Prompts may hold {placeholders} filled from what earlier steps saw; a
step whose placeholder is unknown is skipped with the reason.
"""
import re

# Feature IDs from the plan's inventory; the report lists each with its short name.
FEATURES = {
    'A1': 'Codex install from GitHub', 'A2': 'Codex install from a folder', 'A3': 'Claude zip installer',
    'A4': 'Claude install from GitHub', 'A5': '/cli shortcuts', 'A6': 'upgrade keeps saved data',
    'A7': 'skill listed only on Codex',
    'B1': 'home menu', 'B2': 'choose agent', 'B3': 'first-time check', 'B4': 'setup-status', 'B5': 'stop cancels install',
    'B6': 'readiness gates', 'B7': 'setup menu navigation',
    'C1': 'menu 1 defaults', 'C2': 'change-defaults chain', 'C3': 'routing mode at activation', 'C4': '/cli bind',
    'C5': 'activation card', 'C6': 'usage beside activation', 'C7': 'wider access asks first', 'C8': 'one readiness prompt',
    'D1': 'menu/mode/model open settings', 'D2': 'model list', 'D3': 'effort list', 'D4': 'access list', 'D5': 'routing sub-menu',
    'D6': 'progress toggle', 'D7': 'X closes, agent active', 'D8': 'direct setting commands', 'D9': 'warm/cold change',
    'D10': 'settings persist',
    'E1': 'Direct keeps host prompts', 'E2': '/d forwards unchanged', 'E3': '/d empty', 'E4': '/d inactive',
    'E5': 'Passthrough', 'E6': '/d with menu open', 'E7': 'help stays local', 'E8': 'X semantics', 'E9': 'case and $',
    'E10': 'unknown /cli verb',
    'F1': 'Passing to once', 'F2': 'X says verbatim', 'F3': 'work line', 'F4': 'plans', 'F5': 'tool rows', 'F6': 'artifacts',
    'F7': 'errors and permission stop', 'F8': 'usage line', 'F9': 'Codex Markdown then view', 'F10': 'Claude final text',
    'F11': 'Claude long answer in parts', 'F12': 'Claude attachment note', 'F13': 'preamble separated',
    'G1': 'queue FIFO', 'G2': '/cli queue', 'G3': '/cli cancel', 'G4': 'host interrupted', 'G5': 'unseen output first',
    'G6': '/cli resume', 'G7': 'worker failures', 'G8': 'not-sent reason', 'G9': 'relay records kept',
    'H1': 'read-only native command', 'H2': 'Antigravity handoff', 'H3': 'refused commands', 'H4': 'unknown command',
    'I1': '/cli stop', 'I2': 'stop mid-turn', 'I3': 'no leftover processes', 'I4': 'second stop',
    'J1': 'Stop guard', 'J2': 'compaction mid-relay', 'J3': '/cli reset', 'J4': 'busy control reported', 'J5': 'idle reconnect',
    'J6': 'readiness wake fallback', 'J7': 'state per conversation and host',
    'K1': '/cli display', 'K2': '/cli color', 'K3': 'help card', 'K4': 'menus 40 columns', 'K5': 'Codex themes',
    'K6': 'activation rows',
    'L1': 'view on/off saved', 'L2': 'one window per turn', 'L3': 'closing is safe', 'L4': 'read-only',
    'L5': 'PowerShell 7 and 5.1', 'L6': 'viewer draws every event', 'L7': 'viewer Markdown', 'L8': 'viewer on both hosts',
    'L9': 'no viewer left',
    'P1': 'event types per renderer', 'P2': 'content cases', 'P3': 'Claude $ and green', 'P4': 'agent quirks',
    'P5': 'labels',
    'M': 'usage reporting', 'N': 'hook cost', 'O': 'cross-host',
}

OPTION = re.compile(r'^\|?\s*(\d+)\.\s+(.+?)\s*\|?\s*$')
NATIVE = {'claude': '/context', 'grok-build': '/context', 'copilot': '/context', 'codex': '/status'}
CODING = ('/d In calc.py add multiply(a, b), create test_calc.py with unittest tests for add and multiply, '
          'run the tests, and report the result in two sentences.')
LONG_TASK = ('/d Create five files one.txt to five.txt, each holding its own number written as a word, one file '
             'at a time; then list the folder and describe what you made in three sentences.')
SHORT_TASK = ('/d Create three files a.txt to c.txt, each holding its letter, one file at a time; then list the '
              'folder and describe what you made in three sentences.')


def step(ident, scenario, prompt, features, check=None, depth=('full',), hosts=('claude-code', 'codex'), **extra):
    return dict(id=ident, scenario=scenario, prompt=prompt, features=features, check=check, depth=depth, hosts=hosts,
                **extra)


BOTH = ('full', 'smoke')
STEPS = [
    # 1. Help and menus, as a first-time user of this agent.
    step('help', 'menus', {'claude-code': '/cli help', 'codex': '/help'}, ['K3', 'K4'], 'help'),
    step('help-x', 'menus', 'x', ['E8'], 'help_closed'),
    step('home', 'menus', '/cli', ['B1', 'K4'], 'home'),
    step('choose', 'menus', '{home_number}', ['B2', 'C1'], 'activation_menu'),
    step('change', 'change models', '2', ['C2', 'D2'], 'model_list'),
    step('model', 'change models', '{model_number}', ['C2', 'D2'], 'after_model'),
    step('effort', 'change models', '{effort_number}', ['C2', 'D3'], 'after_effort'),
    step('access', 'change models', '{allow_number}', ['C2', 'C5', 'D4', 'C6', 'C8', 'M'], 'activated'),
    # Smoke path: activate with saved or first-use defaults.
    step('bind', 'smoke', '/cli bind {agent}', ['C4', 'C5'], 'activated', depth=('smoke',)),
    # 2. Settings while active.
    step('menu', 'settings', '/cli menu', ['D1', 'K4'], 'settings_page'),
    step('menu-x', 'settings', 'x', ['D7', 'E8'], 'settings_closed'),
    step('case', 'settings', '$CLI MENU', ['E9', 'D1'], 'settings_page'),
    step('d-menu-open', 'routing', '/d Reply with only the word menu.', ['E6', 'E2'], 'relayed'),
    step('models-menu', 'settings', '/cli menu', ['D1'], 'settings_page', depth=BOTH),
    step('models-list', 'settings', '1', ['D2'], 'model_list', depth=BOTH),
    step('models-x', 'settings', 'x', ['D7'], 'settings_closed', depth=BOTH),
    # The one alternate model, the cheapest-looking one, and straight back to the default.
    step('model-direct', 'settings', '/cli model {other_model}', ['D8', 'D9'], 'model_changed', depth=BOTH),
    step('model-back', 'settings', '/cli model {model}', ['D8', 'D9'], 'model_changed', depth=BOTH),
    step('effort-direct', 'settings', '/cli effort low', ['D8'], 'effort_changed'),
    step('d-empty', 'routing', '/d', ['E3'], 'nothing_sent'),
    # 3. A relayed coding turn, then quiet progress.
    step('coding', 'emissions', CODING, ['E2', 'F1', 'F2', 'F3', 'F5', 'F10', 'F9', 'F13'], 'coding', depth=BOTH),
    step('quiet', 'emissions', '/cli progress quiet', ['D6'], 'local'),
    step('coding-quiet', 'emissions', '/d Run the tests again and reply with the result in one sentence.', ['D6', 'F3'],
         'quiet_relay'),
    step('activity', 'emissions', '/cli progress activity', ['D6'], 'local'),
    # 4. Claude Code's display styles.
    step('instant', 'formatting', '/cli display instant', ['K1'], 'instant', hosts=('claude-code',)),
    step('instant-menu', 'formatting', '/cli menu', ['K1', 'K4'], 'instant_menu', hosts=('claude-code',)),
    step('instant-x', 'formatting', 'x', ['K1'], 'local', hosts=('claude-code',)),
    step('chat', 'formatting', '/cli display chat', ['K1'], 'chat', hosts=('claude-code',)),
    step('color-off', 'formatting', '/cli color off', ['K2'], 'color_off', hosts=('claude-code',)),
    step('color-menu', 'formatting', '/cli menu', ['K2'], 'plain_menu', hosts=('claude-code',)),
    step('color-x', 'formatting', 'x', ['K2'], 'local', hosts=('claude-code',)),
    step('color-on', 'formatting', '/cli color on', ['K2'], 'color_on', hosts=('claude-code',)),
    # 5. Routing modes.
    step('routing-menu', 'routing', '/cli menu', ['D5'], 'settings_page'),
    step('routing-open', 'routing', '{routing_number}', ['D5'], 'routing_menu'),
    step('passthrough', 'routing', '{passthrough_number}', ['D5', 'E5'], 'mode_set'),
    # Choosing a mode returns to Agent Settings, which stays open: an ordinary prompt typed now is taken as a
    # menu reply and silently not sent (finding, 2026-09-24). Record it, then close the menu.
    step('pass-absorbed', 'routing', 'Reply with only the word early.', ['E5', 'E8'], 'absorbed_by_menu'),
    step('pass-close', 'routing', 'x', ['D7'], 'settings_closed'),
    step('pass-prompt', 'routing', 'Reply with only the word pass.', ['E5', 'F2'], 'relayed'),
    step('pass-help', 'routing', {'claude-code': '/cli help', 'codex': '/help'}, ['E7'], 'help'),
    step('pass-help-x', 'routing', 'x', ['E8'], 'help_closed'),
    step('pass-queue', 'routing', '/cli queue', ['E5', 'G2'], 'queue'),
    step('direct', 'routing', '/cli mode direct', ['D5', 'E1'], 'mode_set'),
    # 6. Provider commands.
    step('native', 'native commands', '/d {native}', ['H1'], 'relayed'),
    step('refused', 'native commands', '/d /model', ['H3'], 'refused'),
    step('unknown-native', 'native commands', '/d /definitely-not-a-command', ['H4'], 'refused_or_relayed'),
    # 7. Losing control mid-relay, resuming, and queueing behind a running turn.
    step('long', 'queue and resume', LONG_TASK, ['G4'], 'interrupted', kill_after=12),
    step('queue', 'queue and resume', '/cli queue', ['G2'], 'queue_lists_interrupted'),
    step('resume', 'queue and resume', '/cli resume', ['G6'], 'resumed'),
    step('long-2', 'queue and resume', SHORT_TASK, ['G1'], 'interrupted', kill_after=10),
    step('queued', 'queue and resume', '/d Reply with only the word queued.', ['G1', 'G5'], 'queued'),
    step('queue-2', 'queue and resume', '/cli queue', ['G2'], 'queue'),
    step('resume-2', 'queue and resume', '/cli resume', ['G6'], 'local'),
    # 8. Direct mode keeps host questions with the host.
    step('host', 'host turns', 'What is 7 times 6? Reply with just the number.', ['E1'], 'host_answer'),
    # 9. Closing, error paths and rebinding.
    step('unknown', 'errors', '/cli frobnicate', ['E10'], 'hint'),
    step('stop', 'closing', '/cli stop', ['I1'], 'stopped', depth=BOTH),
    step('queue-off', 'closing', '/cli queue', ['E4'], 'inactive'),
    step('d-off', 'closing', '/d hello', ['E4'], 'inactive'),
    step('bind-bad', 'errors', '/cli bind nosuchagent', ['C4'], 'not_active'),
    step('rebind', 'closing', '/cli bind {agent}', ['C4', 'D10'], 'activated'),
    step('stop-2', 'closing', '/cli stop', ['I1'], 'stopped'),
    step('stop-again', 'closing', '/cli stop', ['I4'], 'stop_again'),
]


def options(text):
    """Numbered rows; a label the 40-column frame wrapped continues on the rows below it."""
    rows, open_row = [], False
    for line in text.splitlines():
        inner = line.strip().strip('|+.\'').strip()
        match = OPTION.match(line.strip())
        if match:
            rows.append([match.group(1), match.group(2).strip()])
            open_row = True
        elif open_row and inner and not re.match(r'^([A-Za-z]\.|[<>] |Showing |-+$)', inner):
            rows[-1][1] += ' ' + inner
        else:
            open_row = False
    return [tuple(row) for row in rows]


def pick(rows, *words, avoid=()):
    for number, label in rows:
        if all(word.casefold() in label.casefold() for word in words) and not any(
                bad.casefold() in label.casefold() for bad in avoid):
            return number, label
    return None, None


LABELS = {'codex': ('Codex CLI', 'Codex'), 'copilot': ('GitHub Copilot CLI', 'Copilot'),
          'agy': ('Antigravity CLI', 'Antigravity'), 'grok-build': ('Grok Build CLI', 'Grok'),
          'claude': ('Claude Code CLI', 'Claude'), 'cursor': ('Cursor CLI', 'Cursor')}


# ---- Checks: each returns nothing and records problems on the turn. `ctx` carries what later steps need.

def expect(turn, condition, message):
    if not condition:
        turn['problems'].append(message)


def check(name, h, turn, ctx):
    shown = h.shown(turn)
    agent = ctx['agent']
    short = LABELS.get(agent, (agent, agent))[1]
    request = (h.state().get('turnRoute') or {}).get('requestId')

    def verbatim(request_id):
        words = h.words(request_id) if request_id else ''
        turn['notes'].append('agent words: ' + words[:120].replace('\n', ' / '))
        expect(turn, words and words in shown, "agent's final words not posted verbatim")

    if name == 'help':
        expect(turn, '/cli' in shown and ('help' in shown.casefold()), 'no help card')
    elif name == 'help_closed':
        expect(turn, 'Help closed' in shown or 'closed' in shown.casefold(), 'help did not close')
    elif name == 'home':
        rows = options(shown)
        number, _ = pick(rows, LABELS.get(agent, (agent,))[0])
        expect(turn, number, 'agent not on the home menu: ' + str(rows)[:200])
        ctx['home_number'] = number
    elif name == 'activation_menu':
        expect(turn, 'Agent Settings' in shown or 'Setup CLI Agent' in shown or 'defaults' in shown.casefold(),
               'no activation or setup page')
    elif name == 'model_list':
        models = [(n, label) for n, label in options(shown) if 'Refresh' not in label and 'page' not in label.casefold()]
        expect(turn, models, 'no model list')
        ctx['models'] = models
        chosen = next(((n, label) for n, label in models if 'current' in label), models[0] if models else (None, ''))
        ctx['model_number'], ctx['model'] = chosen[0], chosen[1].replace('(current)', '').strip()
        others = [label.replace('(current)', '').strip() for n, label in models if 'current' not in label]
        cheap = [label for label in others if re.search(r'(?i)mini|flash|haiku|lite|fast|small|nano|\blow\b', label)]
        ctx['other_model'] = (cheap or others or [None])[0]
    elif name in ('after_model', 'after_effort'):
        rows = options(shown)
        expect(turn, rows, 'no list after choosing')
        allow, _ = pick(rows, 'Allow')
        if allow and name == 'after_model':  # This agent has no effort choice: the access list came next.
            ctx['allow_number'] = allow
            ctx.pop('effort_number', None)
            ctx['skip'] = {'effort': 'the agent offers no effort choice'}
            turn['notes'].append('no effort list: access came next')
        elif name == 'after_model':
            ctx['effort_number'] = rows[0][0] if rows else None
            efforts = [label for _, label in rows]
            turn['notes'].append('efforts: ' + ', '.join(efforts)[:160])
            expect(turn, not any('xhigh' in label.casefold() for label in efforts), 'effort spelled Xhigh, not Extra High')
        else:
            ctx['allow_number'] = allow
            expect(turn, allow, 'no Allow access option: ' + str(rows)[:200])
    elif name == 'activated':
        expect(turn, 'CLI-MODE Activated' in shown, 'not activated')
        for label in ('Model', 'Effort', 'Access'):  # "Model:" in chat; a label row in a Codex view.
            expect(turn, re.search(r'(?m)(\b' + label + r':|^' + label + r'$)', shown), 'activation card lacks ' + label)
        expect(turn, 'Allow' in shown, 'activation card does not show Allow access')
        expect(turn, 'Utilization' in shown or 'Usage' in shown or 'usage' in shown, 'activation card has no usage line')
        state = h.state()
        expect(turn, state.get('active') and (state.get('backend') or state.get('agent')) in (agent, None),
               'state is not active for ' + agent)
    elif name == 'settings_page':
        expect(turn, 'Agent Settings' in shown and 'Model:' in shown, 'settings page missing')
        rows = options(shown)
        ctx['routing_number'] = pick(rows, 'rout')[0] or pick(rows, 'mode')[0]
    elif name == 'settings_closed':
        expect(turn, h.state().get('active'), 'X turned the agent off')
    elif name == 'relayed' and 'finished without public output' in shown:
        # A native command that ends without public text (Grok's /context, known since 2026-09-22).
        expect(turn, 'Passing to' in shown, 'no passing line')
        turn['notes'].append('finished without public output')
    elif name == 'relayed':
        expect(turn, 'Passing to' in shown, 'no passing line')
        expect(turn, 'says...' in shown, 'no attribution line')
        verbatim(request)
    elif name == 'model_changed':
        expect(turn, 'Activated' in shown or 'Select Model' in shown or 'Model' in shown,
               'direct model change gave neither a confirmation nor the menu')
    elif name == 'effort_changed':
        expect(turn, 'Activated' in shown or 'Effort' in shown or 'effort' in shown, 'effort change not confirmed')
    elif name == 'nothing_sent':
        expect(turn, 'Nothing was sent' in shown, '/d with no text did not say "Nothing was sent"')
    elif name == 'coding':
        expect(turn, 'Passing to' in shown, 'no passing line')
        expect(turn, short + ' says...' in shown or 'says...' in shown, 'no attribution line')
        expect(turn, ' work:' in shown or ' work ' in shown, 'no work summary line')
        verbatim(request)
        expect(turn, (h.project / 'test_calc.py').is_file(), 'the agent did not create test_calc.py')
    elif name == 'quiet_relay':
        expect(turn, ' work:' not in shown and ' work ·' not in shown, 'quiet mode still showed work lines')
        verbatim(request)
    elif name == 'instant':
        expect(turn, turn['modelTurns'] == 0 and 'instant replies' in turn['result'], 'instant display not confirmed at no cost')
    elif name == 'instant_menu':
        expect(turn, turn['modelTurns'] == 0 and '| CLI-MODE' in turn['result'] and '```' not in turn['result'],
               'instant menu is not an unfenced frame from the hook')
    elif name == 'chat':
        expect(turn, 'normal chat messages' in shown, 'chat display not confirmed')
    elif name == 'color_off':
        expect(turn, 'plain bold' in shown, 'colour off not confirmed ("now plain bold")')
    elif name == 'plain_menu':
        raw = '\n'.join(turn['texts']) or turn['result']
        expect(turn, '\\color{' not in raw and '```diff' not in raw, 'colour off still posts green LaTeX or diff rows')
    elif name == 'color_on':
        expect(turn, 'now green' in shown, 'colour on not confirmed ("now green")')
    elif name == 'routing_menu':
        rows = options(shown)
        ctx['passthrough_number'] = pick(rows, 'Passthrough')[0]
        ctx['direct_number'] = pick(rows, 'Direct')[0]
        expect(turn, ctx['passthrough_number'], 'no Passthrough option: ' + str(rows)[:200])
    elif name == 'absorbed_by_menu':
        sent = 'says...' in shown
        prose = re.sub(r'```.*?```', '', shown, flags=re.S)  # The menu's own rows ("X. Close settings") are no explanation.
        prose = '\n'.join(line for line in prose.splitlines() if not line.strip().startswith('|'))
        explained = re.search(r'(?i)not sent|menu is open|close (the )?(menu|settings)|reply with a number', prose)
        turn['notes'].append('ordinary prompt with Agent Settings open: ' + ('relayed' if sent else 'held by the menu') +
                             (', with an explanation' if explained else ', without an explanation'))
        expect(turn, sent or explained, 'an ordinary prompt typed while Agent Settings was open was silently not sent')
    elif name == 'mode_set':
        expect(turn, 'assthrough' in shown or 'irect' in shown, 'routing change not confirmed')
    elif name == 'queue':
        expect(turn, 'queue' in shown.casefold() or 'Worker' in shown, 'no queue report')
    elif name == 'refused':
        expect(turn, '/cli' in shown, 'refused command does not name the /cli control')
        expect(turn, 'says...' not in shown, 'a refused command reached the agent')
    elif name == 'refused_or_relayed':
        turn['notes'].append('unknown provider command: ' + shown[:200].replace('\n', ' / '))
        ctx.setdefault('unknownNative', []).append(shown[:300])
    elif name == 'interrupted':
        ctx.setdefault('interrupted', []).append(request)
        turn['notes'].append('turn stopped early, as if the user pressed Esc; request ' + str(request))
    elif name == 'queue_lists_interrupted':
        first = (ctx.get('interrupted') or [None])[0]
        expect(turn, first and first[:8] in shown, 'queue does not list the interrupted request')
    elif name == 'resumed':
        verbatim((ctx.get('interrupted') or [None])[0])
    elif name == 'queued':
        running = (ctx.get('interrupted') or [None, None])[-1]
        expect(turn, 'Queued behind' in shown or 'queued' in shown.casefold(), 'the queued request was not reported')
        verbatim(request)
        if h.host == 'claude-code':  # G5: only Claude Code carries earlier unseen output into the next relay.
            verbatim(running)
    elif name == 'host_answer':
        expect(turn, '42' in (turn['result'] or shown), 'host question not answered')
        expect(turn, not turn.get('controllerCalls'), 'host question ran CLI-MODE')
        expect(turn, len(h.state().get('requests') or {}) == ctx.get('requestsBefore', len(h.state().get('requests') or {})),
               'host question was forwarded to the agent')
    elif name == 'hint':
        expect(turn, 'help' in shown, 'unknown verb gave no hint')
    elif name == 'stopped':
        state = h.state()
        # Claude Code posts CLI-MODE's own line; on Codex `off` returns data and the model confirms it.
        expect(turn, 'CLI-MODE is off' in shown or (h.host == 'codex' and re.search(r'(?i)\b(off|stopped)\b', shown)),
               'no shutdown message')
        expect(turn, not state.get('active') and not state.get('owned'), 'agent still active or owned after stop')
    elif name == 'inactive':
        expect(turn, '/cli to activate' in shown, 'no inactive hint')
    elif name == 'not_active':
        expect(turn, not h.state().get('active'), 'a failed bind left an agent active')
        turn['notes'].append('bad bind reply: ' + shown[:200].replace('\n', ' / '))
    elif name == 'stop_again':
        expect(turn, re.search(r'(?i)\b(off|stopped|not active)\b|/cli to activate', shown),
               'second stop gave no clear reply')
        expect(turn, not h.state().get('active'), 'second stop left the agent active')
    elif name == 'local':
        pass
    else:
        raise KeyError(name)


def fill(prompt, ctx):
    try:
        return prompt.format(**{key: value for key, value in ctx.items() if value is not None}), None
    except KeyError as missing:
        return None, 'unknown ' + str(missing)


def run_steps(h, agent, depth='full', only=None):
    """Run the shared scenario; returns the per-step results the report reads."""
    ctx = dict(agent=agent, native=NATIVE.get(agent))
    results = []
    for item in STEPS:
        if depth not in item['depth'] or h.host not in item['hosts'] or (only and item['id'] not in only):
            continue
        reason = (ctx.get('skip') or {}).get(item['id'])
        prompt = item['prompt'][h.host] if isinstance(item['prompt'], dict) else item['prompt']
        if not reason:
            prompt, reason = fill(prompt, ctx)
        if item['id'] == 'native' and not ctx.get('native'):
            reason = 'no read-only native command for ' + agent
        if reason:
            results.append(dict(step=item['id'], features=item['features'], status='skip', reason=reason))
            continue
        if item['id'] == 'host':
            ctx['requestsBefore'] = len(h.state().get('requests') or {})
        turn = h.send(prompt, item['scenario'], kill_after=item.get('kill_after'))
        try:
            check(item['check'], h, turn, ctx)
        except Exception as exc:  # A check that cannot read the turn is a failure, not a crash.
            turn['problems'].append('check failed: ' + type(exc).__name__ + ': ' + str(exc)[:200])
        turn['step'] = item['id']
        results.append(dict(step=item['id'], features=item['features'], prompt=prompt,
                            status='fail' if turn['problems'] else 'pass', problems=turn['problems'],
                            notes=turn['notes']))
    return results
