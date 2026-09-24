"""Behavioral tests for public activity, display preferences and rendering."""
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_controller import PLUGIN, FakeBackend, Controller, Store, hook, runtime_process, runtime_result
from acpx import PublicRelay
import menu_view
from progress import public_progress, usage_text, ActivityRelay
from state import route


def activity(ident='read', status='pending', **extra):
    return dict(type='activity', toolCallId=ident, status=status, kind='read', title='Read source', **extra)


class Activity(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for the bridge projector')
    def test_bridge_projection_handles_partial_private_malformed_and_interleaved_tools(self):
        script = ('import {createProgressProjector} from ' +
                  json.dumps((PLUGIN / 'scripts/public-progress.mjs').as_uri()) + ';' +
                  'let text=""; for await(const chunk of process.stdin) text+=chunk;' +
                  'const project=createProgressProjector();' +
                  'process.stdout.write(JSON.stringify(JSON.parse(text).map(project).filter(Boolean)));')
        events = [
            dict(sessionUpdate='tool_call', toolCallId='a', kind='read', title='Read first', status='pending'),
            dict(sessionUpdate='tool_call', toolCallId='b', kind='search', title='Search next', status='in_progress'),
            dict(sessionUpdate='tool_call_update', toolCallId='a', status='completed', rawOutput='PRIVATE'),
            dict(sessionUpdate='tool_call', toolCallId='c', kind='think', title='PRIVATE'),
            dict(sessionUpdate='tool_call_update', toolCallId='c', title='PRIVATE', status='completed'),
            dict(sessionUpdate='tool_call_update', toolCallId='a', status='pending'),
            dict(sessionUpdate='tool_call_update', toolCallId='orphan', title='PRIVATE unknown arguments', status='failed'),
            dict(sessionUpdate='usage_update', used=-1, size=0, cost={'amount': -1, 'currency': 'USD'}),
            dict(sessionUpdate='usage_update', used=True, size='123', _meta={'usage': {'input_tokens': 0}}),
            dict(sessionUpdate='agent_thought_chunk', content={'type': 'text', 'text': 'PRIVATE'}),
            dict(sessionUpdate='tool_call_update', toolCallId=None), None,
        ]
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script],
                                input=json.dumps(events), capture_output=True, text=True, encoding='utf-8', timeout=10, check=True)
        projected = json.loads(result.stdout)
        self.assertEqual(len(projected), 6)
        self.assertNotIn('PRIVATE', result.stdout)
        self.assertEqual(projected[2], dict(activity('a', 'completed'), title='Read first'))
        self.assertEqual(projected[3], dict(type='activity', toolCallId='orphan', kind='other', status='failed'))
        self.assertEqual(projected[-1], dict(type='usage', breakdown={'inputTokens': 0}))
        # The second boundary retains precisely the public bridge contract.
        self.assertEqual([public_progress(e) for e in projected], projected)

    def test_lifecycle_partial_metadata_duplicates_and_late_notifications(self):
        relay = PublicRelay()
        first = activity()
        self.assertEqual(relay.feed(first), [first])
        self.assertEqual(relay.feed(first), [])
        running = activity(status='in_progress')
        self.assertEqual(relay.feed(running), [running])
        self.assertEqual(relay.feed(first), [])
        changed = activity(status='in_progress', locations=[dict(path='source.py', line=7)])
        self.assertEqual(relay.feed(changed), [])
        done = activity(status='completed', locations=changed['locations'])
        self.assertEqual(relay.feed(done), [done])
        self.assertEqual(relay.feed(running), [])
        self.assertEqual(relay.feed(done), [])
        self.assertEqual(relay.activity.flush(), [])
        # Repeated titles with different tool IDs represent different work.
        other = activity('another')
        self.assertEqual(relay.feed(other), [other])

    def test_usage_coalesces_and_flushes_before_canonical_completion(self):
        relay = PublicRelay()
        with patch('progress.time.monotonic', return_value=10):
            self.assertEqual(relay.feed(dict(type='usage', used=1, size=100)), [])
            self.assertEqual(relay.feed(dict(type='usage', used=2, size=100)), [])
        with patch('progress.time.monotonic', return_value=10.6):
            self.assertEqual(relay.due(), [dict(type='usage', used=2, size=100)])
        self.assertEqual(relay.feed(dict(type='usage', used=2, size=100)), [])
        relay.feed(dict(type='usage', used=3))
        relay.feed(dict(type='message', text='Answer'))
        done = dict(type='done', stopReason='end_turn')
        self.assertEqual(relay.feed(done), [dict(type='message', text='Answer'), dict(type='usage', used=3), done])

    def test_quiet_hides_only_activity_and_usage(self):
        relay = PublicRelay('quiet')
        self.assertEqual(relay.feed(activity()), [])
        self.assertEqual(relay.feed(dict(type='usage', used=4)), [])
        message = dict(type='message', text='Answer\n')
        self.assertEqual(relay.feed(message), [])
        self.assertEqual(relay.flush(), [message])
        for event in (dict(type='plan', entries=[]), dict(type='artifact', content={}),
                      dict(type='error', message='failed'), dict(type='done', stopReason='error')):
            self.assertEqual(relay.feed(event), [event])

    def test_allowlist_validation_bounds_and_escape_sequences(self):
        raw = activity(locations=[dict(path='\x1b[31msource.py\n', line=0, secret='PRIVATE')],
                       rawInput='PRIVATE', rawOutput='PRIVATE', content='PRIVATE')
        raw['title'] = '\x1b[31mRead\nsource\u202e' + 'x' * 400
        safe = public_progress(raw)
        self.assertNotIn('PRIVATE', json.dumps(safe))
        self.assertNotIn('\x1b', safe['title'])
        self.assertEqual(len(safe['title']), 240)
        self.assertEqual(safe['locations'], [dict(path='source.py', line=0)])
        raw.update(kind='execute', title='command secret')
        self.assertNotIn('title', public_progress(raw))
        for patch_ in (dict(kind='think'), dict(kind=[]), dict(status={}), dict(toolCallId=''), dict(status='unknown')):
            self.assertIsNone(public_progress(dict(raw, **patch_)))
        usage = public_progress(dict(type='usage', used=True, size=-1,
            cost=dict(amount=float('inf'), currency='USD'), breakdown=dict(inputTokens=3, secret='PRIVATE')))
        self.assertEqual(usage, dict(type='usage', breakdown=dict(inputTokens=3)))
        self.assertIsNone(public_progress(dict(type='usage', cost=dict(amount=10**1000, currency='USD'))))
        self.assertEqual(usage_text(dict(type='usage', used=0, size=0)), 'Context: 0 tokens')

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the bridge projector')
    def test_unicode_title_boundary_and_invalid_surrogates_render_safely(self):
        script = ('import {createProgressProjector} from ' +
                  json.dumps((PLUGIN / 'scripts/public-progress.mjs').as_uri()) + ';' +
                  'let text="";for await(const c of process.stdin)text+=c;' +
                  'process.stdout.write(JSON.stringify(createProgressProjector()(JSON.parse(text))));')
        title = 'x' * 239 + '\U0001f642' + '\ud800'
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', script],
            input=json.dumps(dict(sessionUpdate='tool_call', toolCallId='unicode', kind='read', title=title)),
            capture_output=True, text=True, encoding='utf-8', timeout=10, check=True)
        event = json.loads(result.stdout)
        self.assertEqual(event['title'], 'x' * 239 + '\U0001f642')
        self.assertEqual(public_progress(dict(event, title='Read\ud800source'))['title'], 'Read source')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'public.jsonl'
            source.write_text(json.dumps(event) + '\n', encoding='utf-8')
            view = menu_view.write_progress(source, root / 'view.html', 'Codex')
            self.assertIn('\U0001f642', Path(view['messageView']['path']).read_text(encoding='utf-8'))

    def test_tool_state_is_bounded_without_evicting_identity(self):
        relay = ActivityRelay()
        for index in range(4097):
            relay.feed(activity(str(index)))
        self.assertEqual(len(relay.tools), 4096)
        self.assertEqual(relay.feed(activity('4096', 'completed')), [])
        self.assertEqual(relay.feed(activity('0', 'completed')), [activity('0', 'completed')])

    def test_display_preferences_persist_without_touching_provider_or_routing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            backend = FakeBackend()
            control = Controller(Store('preference', root, root / 'state'), backend)
            self.assertEqual(control.store.read()['progressMode'], 'activity')
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            before = control.store.read()
            calls = len(backend.calls)
            control.settings_menu()
            self.assertEqual(route('4', control.store.read()), dict(route='progress', choice='quiet'))
            result = control.progress('quiet')
            self.assertIn('Progress: Quiet', result['activationMenu'])
            self.assertEqual(len(backend.calls), calls)
            for field in ('main', 'settings', 'generation', 'routingMode', 'owned', 'active'):
                self.assertEqual(result[field], before[field])
            control.off()
            self.assertEqual(control.store.read()['progressMode'], 'quiet')

    def test_progress_controls_and_compaction_never_delegate(self):
        for active in (True, False):
            for prefix in ('/', '$'):
                state = dict(active=active, routingMode='direct')
                self.assertEqual(route(prefix + 'cli progress QUIET', state), dict(route='progress', choice='quiet'))
                self.assertEqual(route(prefix + 'cli progress invalid', state)['route'], 'hint')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            event = dict(session_id='progress', cwd=str(root), hook_event_name='UserPromptSubmit', prompt='/cli progress quiet')
            context = hook.handle(event, root / 'data')['hookSpecificOutput']['additionalContext']
            self.assertIn('progress --choice quiet`', context)
            store = Store('progress', root, root / 'data')
            Controller(store).progress('quiet')
            event.update(hook_event_name='SessionStart', source='compact')
            context = hook.handle(event, root / 'data')['hookSpecificOutput']['additionalContext']
            self.assertIn('progress display control completed', context)
            self.assertNotIn('requests', store.read())

    def test_activity_alone_cannot_make_a_turn_successful(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            backend = FakeBackend()
            control = Controller(Store('activity-only', root, root / 'state'), backend)
            control.frontend()
            control.activate('gemini-3.8-flash-high', 'allow')
            with patch.object(backend, 'start', side_effect=lambda *a, **k: runtime_process([
                    activity(status='completed'), dict(type='usage', used=10), runtime_result()])):
                with self.assertRaisesRegex(RuntimeError, 'did not complete successfully'):
                    control.send('/d task', output=lambda e: None)

    def test_renderer_replaces_rows_bounds_display_and_escapes_untrusted_text(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'public.jsonl'
            events = [activity('same'), dict(activity('same', 'completed'), title='<script>alert(1)</script>')]
            events += [activity(str(i), 'completed') for i in range(25)]
            events += [dict(type='usage', used=20, size=100, cost=dict(amount=.01, currency='USD')),
                       dict(type='agent_thought_chunk', text='PRIVATE'), dict(type='done', stopReason='end_turn')]
            source.write_text(''.join(json.dumps(e) + '\n' for e in events) + '{"type":"activity"', encoding='utf-8')
            before = source.read_bytes()
            result = menu_view.write_progress(source, root / 'view.html', 'Codex')
            self.assertEqual(result['toolCount'], 26)
            self.assertIn('6 other tools', result['text'])
            self.assertIn('20.0%', result['text'])
            html = Path(result['messageView']['path']).read_text(encoding='utf-8')
            self.assertEqual(html.count('<li>'), 20)
            self.assertNotIn('PRIVATE', html)
            self.assertEqual(source.read_bytes(), before)
            # A short snapshot includes the dangerous title, as escaped text.
            source.write_text(''.join(json.dumps(e) + '\n' for e in events[:2]), encoding='utf-8')
            result = menu_view.write_progress(source, root / 'view2.html', 'Codex')
            html = Path(result['messageView']['path']).read_text(encoding='utf-8')
            self.assertEqual(html.count('<li>'), 1)
            self.assertIn('&lt;script&gt;', html)
            self.assertNotIn('<script>', html)
            self.assertNotIn('Pending', html)

    def test_format_progress_cli_is_read_only_and_does_not_infer_tool_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'public.jsonl'
            source.write_text(json.dumps(activity(status='in_progress')) + '\n' +
                              json.dumps(dict(type='done', stopReason='cancelled')) + '\n', encoding='utf-8')
            command = [sys.executable, str(PLUGIN / 'scripts/controller.py'), '--thread', 'progress-view',
                '--workspace', str(root), '--message-output', str(root / 'view.html'),
                'format-progress', '--agent', 'codex', '--file', str(source)]
            result = subprocess.run(command, env=dict(os.environ, CLI_MODE_DATA=str(root / 'data')),
                                    capture_output=True, text=True, timeout=10, check=True)
            value = json.loads(result.stdout)
            self.assertIn('Running: Read source', value['text'])
            self.assertIn('last reported', value['text'])
            self.assertFalse((root / 'data').exists())


if __name__ == '__main__': unittest.main()
