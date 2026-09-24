"""CLI-MODE lifecycle and dispatch. Run --help; hooks import the same controller.

Controller composes focused mixins: menus (setup and settings), binding (owned
session lifecycle), dispatch (one provider turn) and queue_worker (detached
FIFO worker and receipts). Module-level helpers live in operations.
"""
import argparse
import json
import os
from pathlib import Path
import sys

import adapters
import help_view
import host
import installer
import menu_view
from binding import BindingMixin
from dispatch import DispatchMixin
from menus import MenuMixin
from operations import emit, operation_running, pending_work, status_age  # noqa: F401 (re-exported)
from presentation import chat_menu, menu_block, relay_plain
from progress import PROGRESS_MODES
from queue_worker import QueueMixin
from state import Store, route, ROUTING_MODES


class Controller(QueueMixin, MenuMixin, BindingMixin, DispatchMixin):
    def __init__(self, store, backend=None, agent=None):
        self.store = store
        self.override = backend
        # Set while a menu choice or setting change may activate and will show the confirmation:
        # activate() then starts the usage lookup alongside the provider work (see prefetch_usage).
        self.prefetching = False
        self.prefetched = None
        # A registry record alone is not an implementation, and one backend's
        # saved state must never be replayed through another provider.
        self.use(agent or store.read().get('backend') or 'agy')

    def use(self, agent):
        """Bind this controller to one backend's adapter and transport."""
        self.agent = agent
        self.adapter = adapters.module(agent)
        adapters.descriptor(agent)
        self.backend = self.override or self.adapter.Backend()
        return self.adapter


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thread', default=os.environ.get('CODEX_THREAD_ID'))
    parser.add_argument('--workspace', default=os.getcwd())
    parser.add_argument('--data-root', help=argparse.SUPPRESS)
    parser.add_argument('--host', choices=host.HOSTS, default=host.current(),
                        help='The host running CLI-MODE: codex (inline views) or claude-code (chat text).')
    parser.add_argument('--menu-output', help='Write the returned menu as an inline HTML fragment at this task-owned path.')
    parser.add_argument('--message-output', help='Write an inline message with green host/attribution text and normal-color provider content.')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'off', 'refresh', 'activation-message', 'queue', 'resume'):
        sub.add_parser(name)
    sub.add_parser('commands')
    p = sub.add_parser('observe'); p.add_argument('--request', required=True)
    p.add_argument('--cursor', type=int, default=0); p.add_argument('--limit', type=int, default=100)
    p = sub.add_parser('relay'); p.add_argument('--request', required=True, action='append')
    p.add_argument('--cursor', type=int, default=0); p.add_argument('--wait', type=float)
    p.add_argument('--view-dir')
    p = sub.add_parser('pump', help=argparse.SUPPRESS); p.add_argument('--token', required=True)
    p = sub.add_parser('choose'); p.add_argument('number', type=int)
    p = sub.add_parser('navigate'); p.add_argument('action', choices=['b', 'r', '>', '<'])
    p = sub.add_parser('settings'); p.add_argument('--dismiss', action='store_true')
    p = sub.add_parser('progress'); p.add_argument('--choice', choices=PROGRESS_MODES)
    p = sub.add_parser('view'); p.add_argument('--choice', choices=('on', 'off'))
    p = sub.add_parser('mode'); group = p.add_mutually_exclusive_group()
    group.add_argument('--choice', choices=ROUTING_MODES); group.add_argument('--dismiss', action='store_true')
    p = sub.add_parser('catalog'); p.add_argument('--agent')
    p = sub.add_parser('bind'); p.add_argument('--agent', default='agy')
    p = sub.add_parser('frontend'); p.add_argument('--agent', default='agy'); p.add_argument('--page', type=int, default=1)
    p = sub.add_parser('options'); p.add_argument('--agent'); p.add_argument('--page', type=int, default=1)
    p.add_argument('--phase', choices=['model', 'effort', 'access'], required=True)
    p = sub.add_parser('format-message'); p.add_argument('--file', required=True)
    p.add_argument('--agent'); p.add_argument('--kind', choices=['agent', 'passing', 'activation'], default='agent')
    p = sub.add_parser('format-menu'); p.add_argument('--file', required=True)
    p = sub.add_parser('format-progress'); p.add_argument('--file', required=True); p.add_argument('--agent')
    p = sub.add_parser('setup-start'); p.add_argument('--approved', action='store_true'); p.add_argument('--agent')
    sub.add_parser('setup-status')
    sub.add_parser('setup-manual')
    p = sub.add_parser('first-time-check'); p.add_argument('--agent', default='agy')
    p = sub.add_parser('route'); p.add_argument('--file', required=True)
    p = sub.add_parser('draft'); p.add_argument('--phase', choices=['agent', 'activation', 'model', 'effort', 'access'], required=True); p.add_argument('--file', required=True)
    p = sub.add_parser('activate'); p.add_argument('--model'); p.add_argument('--access')
    p.add_argument('--effort'); p.add_argument('--agent')
    p = sub.add_parser('tune'); p.add_argument('--phase', choices=['model', 'effort', 'access'], required=True)
    p.add_argument('--apply', action='store_true', help='Match and apply the text typed after the command.')
    p = sub.add_parser('send')
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument('--file'); source.add_argument('--request')
    sub.add_parser('cancel')
    p = sub.add_parser('acknowledge'); p.add_argument('--operation', required=True)
    return parser


def run(args, control=None):
    """One controller command as a result dict: the CLI prints it, and a hook
    can show it without starting another process.

    On a host without inline views (Claude Code), confirmations come back as
    text, the relay writes no HTML, and no view reference is added.
    """
    if host.current() != args.host:
        host.select(args.host)  # Wording helpers read the process's host; keep them in step.
    views = host.views(args.host)
    confirm_to = args.message_output if views else None
    confirming = bool(args.message_output) or not views
    control = control or Controller(Store(args.thread, args.workspace, args.data_root))
    command = args.command
    if command == 'status': result = control.store.read()
    elif command == 'queue': result = control.queue_status()
    elif command == 'resume': result = control.resume_monitoring()
    elif command == 'observe': result = control.observe(args.request, args.cursor, args.limit)
    elif command == 'relay':
        # A text host posts nothing until the agent finishes, so each call waits longer (its tool timeout is 30 s).
        wait = min(max(args.wait if args.wait is not None else 8.0 if views else 25.0, 0), 30)
        if not views:  # A text host relays a turn's earlier, cut-off requests with its own, in one loop.
            result = control.relay_chain(args.request, args.cursor, wait)
        elif len(args.request) == 1:
            result = control.relay(args.request[0], args.cursor, wait, args.view_dir)
        else:
            raise ValueError('relay takes one --request on a host with inline views.')
    elif command == 'pump': result = control.pump(args.token)
    elif command == 'activation-message':
        if views and not args.message_output:
            raise ValueError('activation-message requires --message-output.')
        result = control.activation_message(confirm_to)
    elif command == 'choose':
        control.prefetching = confirming  # The last choice activates; its usage lookup overlaps the provider work.
        try:
            result = control.choose(args.number, require_hooks=True)
        finally:
            control.prefetching = False
        activated = result.get('active') and not result.get('pending') and 'activationMenu' not in result
        if confirming and activated:
            result = dict(result, activation=control.activation_message(confirm_to))
    elif command == 'refresh': result = control.refresh()
    elif command == 'navigate': result = control.navigate(args.action)
    elif command == 'settings': result = control.settings_menu(args.dismiss)
    elif command == 'progress': result = control.progress(args.choice)
    elif command == 'view': result = control.view(args.choice)
    elif command == 'mode': result = control.mode(args.choice, args.dismiss)
    elif command == 'catalog':
        result = control.use(args.agent or control.agent_of(control.store.read())).catalog(control.store.root)
    elif command == 'commands':
        # The same framed card as every other menu; `text` is its fallback.
        page = help_view.render()
        result = {'text': page, 'activationMenu': page}
    elif command == 'format-message':
        if not args.message_output:
            raise ValueError('format-message requires --message-output at a task-owned path.')
        target = args.agent or control.agent_of(control.store.read())
        label = control.use(target).LABEL
        with Path(args.file).open(encoding='utf-8-sig', newline='') as source:
            text = source.read()
            if args.kind == 'passing':
                text = control.adapter.PASSING
            elif args.kind == 'activation':
                raise ValueError('Use activation-message for the verified shared activation template.')
            result = {'messageView': dict(
                path=menu_view.write_message(text, args.message_output, label, args.kind),
                format='inline-html', label=label, kind=args.kind)}
    elif command == 'format-progress':
        if not args.message_output:
            raise ValueError('format-progress requires --message-output at a task-owned path.')
        target = args.agent or control.agent_of(control.store.read())
        result = menu_view.write_progress(args.file, args.message_output, control.use(target).LABEL)
    elif command == 'format-menu': result = {'text': menu_block(Path(args.file).read_text(encoding='utf-8-sig'))}
    elif command == 'bind': result = control.bind(args.agent, message_output=confirm_to, confirm=confirming)
    elif command == 'tune' and args.apply:
        control.prefetching = confirming  # A setting change reactivates, as a menu choice does.
        try:
            result = control.tune_choice(args.phase)
        finally:
            control.prefetching = False
        if confirming and result.get('active') and not result.get('pending'):
            result = dict(result, activation=control.activation_message(confirm_to))
    elif command == 'tune': result = control.tune(args.phase)
    elif command == 'frontend': result = control.frontend(args.agent, args.page)
    elif command == 'options':
        current = control.store.read()
        target = args.agent or control.agent_of(current)
        control.use(target)
        result = control.options(args.phase, target, args.page)
    elif command == 'setup-start': result = control.setup_start(args.approved, args.agent)
    elif command == 'setup-status': result = control.setup_status()
    elif command == 'setup-manual':
        result = installer.call('Manual', backend=control.agent_of(control.store.read()))
    elif command == 'first-time-check': result = control.first_time_check(args.agent)
    elif command == 'draft': result = control.draft(args.phase, json.loads(Path(args.file).read_text(encoding='utf-8-sig')))
    elif command == 'route': result = route(Path(args.file).read_text(encoding='utf-8-sig'), control.store.read())
    elif command == 'activate':
        current = control.store.read()
        target = args.agent or control.agent_of(current)
        defaults = control.use(target).DEFAULTS
        # Another backend's saved model never seeds this one.
        saved = (current.get('settings') or {}) if current.get('backend') in (None, target) else {}
        model = args.model or saved.get('model', defaults['model'])
        access = args.access or saved.get('access', defaults['access'])
        effort = args.effort or saved.get('effort', defaults.get('effort'))
        prefetched = control.prefetch_usage(target, model, access, effort) if confirming else None
        result = control.activate(model, access, effort=effort, agent=target, require_hooks=True)
        if confirming:
            result = dict(result, activation=control.activation_message(confirm_to, prefetched))
    elif command == 'off': result = control.off()
    elif command == 'send':
        if args.request:
            result = control.send_request(args.request)
        else:
            with Path(args.file).open(encoding='utf-8-sig', newline='') as source:
                result = control.send(source.read())
    elif command == 'cancel': result = control.cancel()
    else:
        result = control.acknowledge(args.operation)
    block = result.get('activationMenu') or (result.get('text') if command == 'format-menu' else None)
    if block and args.menu_output:
        path = menu_view.write(block, args.menu_output)
        result['menuView'] = dict(path=path, format='inline-html')
    return with_references(result) if views else result


def main():
    args = build_parser().parse_args()
    host.select(args.host)
    if host.claude():
        # Git Bash gives Python a legacy code page; never fail on a character.
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    result = run(args)
    if args.command == 'relay' and not host.views(args.host):
        # Claude Code shows this tool output to anyone who opens it: plain words, not JSON.
        print(relay_plain(result), flush=True)
        return
    if not host.views(args.host) and isinstance(result, dict) and isinstance(result.get('activationMenu'), str):
        # Claude posts this menu in chat, where its title band can be green, in the same box.
        color = host.chat_color(args.data_root or host.data_root(args.host), args.host)
        result = dict(result, activationMenu=chat_menu(result['activationMenu'], color))
    emit(result)


def with_references(result):
    """Add the exact line Codex renders beside every view, so the host just prints it."""
    for key in ('menuView', 'messageView'):
        view = result.get(key)
        if isinstance(view, dict) and view.get('path') and 'reference' not in view:
            view['reference'] = menu_view.reference(view['path'])
    if isinstance(result.get('activation'), dict):
        with_references(result['activation'])
    return result


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        emit({'error': str(exc)})
        sys.exit(1)
