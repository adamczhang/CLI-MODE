"""Opt-in sequential six-CLI validation. Spends provider quota; no user installs.

Run live_parity_probe.py for all agents into OUTPUT/parity-codex first.
Each stage has fresh test ownership; completed stages are retained on resume.
This orchestrates existing probes, not proof of every native CLI capability.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ['agy', 'claude', 'grok-build', 'cursor', 'copilot', 'codex']
PHASES = ['coding', 'settings', 'cancel', 'monitor', 'controls', 'research', 'images', 'text-host', 'actual-host']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--agents', nargs='+', choices=AGENTS, default=AGENTS)
    parser.add_argument('--phases', nargs='+', choices=PHASES, default=PHASES)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith('CLI_MODE_')}
    env['CODEX_PERMISSION_PROFILE'] = ':danger-full-access'
    matrix = output / 'matrix.json'
    rows = json.loads(matrix.read_text()) if matrix.exists() else []
    completed = {(r['agent'], r['phase']) for r in rows}
    def save():
        matrix.write_text(json.dumps(rows, indent=2), encoding='utf-8')
    for phase in args.phases:
        for agent in args.agents:
            if (agent, phase) in completed:
                continue
            preflight = output / 'parity-codex' / agent / 'result.json'
            if not preflight.exists():
                raise RuntimeError('Run and finish parity preflight first: ' + str(preflight))
            baseline = json.loads(preflight.read_text())
            failure = baseline.get('error', '')
            if failure and any(word in failure.lower() for word in ('upgrade', 'plan', 'quota', 'usage limit', 'sign', 'auth', 'login')):
                rows.append(dict(agent=agent, phase=phase, status='BLOCKED', reason=failure))
                save()
                continue
            folder = output / phase / agent
            folder.mkdir(parents=True, exist_ok=True)
            specs = {
                'coding': ('live_coding_queue.py', ['--agent', agent, '--output', str(folder / 'run')]),
                'settings': ('live_settings_continuity.py', ['--agents', agent, '--output', str(folder)]),
                'cancel': ('live_cancel_queue.py', ['--agent', agent, '--output', str(folder / 'run')]),
                'monitor': ('live_monitor_resume.py', ['--agent', agent, '--output', str(folder / 'run')]),
                'controls': ('five_cli_controls.py', ['--agents', agent, '--output', str(folder)]),
                'research': ('five_cli_live.py', ['--agents', agent, '--output', str(folder)]),
                'images': ('live_image_menu_validation.py', ['--agents', agent, '--output', str(folder), '--images-only', '--image-numbers', '1']),
                'text-host': ('live_parity_probe.py', ['--agents', agent, '--host', 'claude-code', '--output', str(folder)]),
                'actual-host': ('claude_live_relay.py', ['--agents', agent, '--model', 'sonnet', '--keep']),
            }
            script, options = specs[phase]
            command = [sys.executable, '-u', str(ROOT / 'checks' / script), *options]
            (output / 'current.json').write_text(json.dumps(dict(agent=agent, phase=phase, started=time.time())), encoding='utf-8')
            print('START', phase, agent, flush=True)
            started = time.monotonic()
            with (folder / 'run.log').open('w', encoding='utf-8') as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            row = dict(agent=agent, phase=phase, status='PASS' if result.returncode == 0 else 'FAIL',
                       exitCode=result.returncode, seconds=round(time.monotonic() - started, 1), log=str(folder / 'run.log'))
            # A zero process exit is not sufficient for probe scripts that report errors as JSON.
            for path in list(folder.rglob('result.json')) + list(folder.rglob('evidence.json')):
                value = json.loads(path.read_text(encoding='utf-8'))
                if value.get('error') or value.get('errors') or value.get('passed') is False or value.get('success') is False:
                    row['status'] = 'FAIL'
                    row['reason'] = value.get('error') or value.get('errors') or 'Evidence assertion failed'
                if phase == 'images' and row['status'] == 'PASS' and value.get('images'):
                    image = value['images'][0]
                    if not image.get('files'):
                        answer = image.get('answer', '')
                        row['status'] = 'N/A' if image.get('status') == 'completed' and any(word in answer.lower() for word in
                            ('unavailable', 'not available', 'do not have', "don't have", 'no native', 'cannot', "can't")) else 'FAIL'
                        row['reason'] = answer[:800] or 'No native image output'
                shutdown = value.get('finalShutdown') or value.get('shutdown') or value
                if isinstance(shutdown, dict) and shutdown.get('shutdownComplete') is False:
                    row['status'] = 'FAIL'
                    row['reason'] = 'Shutdown incomplete; stop validation and inspect ownership'
                    rows.append(row); save()
                    raise RuntimeError(row['reason'])
            rows.append(row)
            save()
            print('END', phase, agent, row['status'], row['seconds'], flush=True)
    (output / 'current.json').write_text(json.dumps(dict(done=True)), encoding='utf-8')
    return 1 if any(r['status'] == 'FAIL' for r in rows) else 0


if __name__ == '__main__':
    raise SystemExit(main())
