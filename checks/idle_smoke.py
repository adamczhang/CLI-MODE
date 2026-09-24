"""Opt-in real idle/reconnect check with a 2-second TEST TTL, never a production change."""
import json
from pathlib import Path
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/cli-mode/scripts'))
import agy
from controller import Controller
from state import Store


class ShortIdle(agy.Backend):
    def command(self, owned, args, timeout=60):
        command = super().command(owned, args, timeout)
        command[command.index('--ttl') + 1] = '2'
        return command


def main():
    folder = Path(tempfile.mkdtemp(prefix='cli-mode-idle-proof-'))
    work = folder / 'workspace'
    work.mkdir()
    controller = Controller(Store('idle-proof-' + uuid.uuid4().hex, work, folder / 'state'), ShortIdle())
    evidence = {'testIdleSeconds': 2, 'productionIdleSeconds': 1800, 'checks': {}}
    def send(text):
        events = []
        result = controller.send('/d ' + text, output=events.append)
        return ''.join(e['text'] for e in events if e['type'] == 'message'), result
    try:
        controller.frontend()
        state = controller.activate('gemini-3.8-flash-high', 'allow')
        owned = state['owned'][0]
        marker = 'idle-' + uuid.uuid4().hex[:8]
        send('Remember ' + marker + ' for my next message. Reply ACK, no tools.')
        time.sleep(4)
        status = controller.backend.control(owned, ['status', '-s', owned['name']])
        evidence['checks']['afterIdle'] = status
        evidence['checks']['routingStillActive'] = controller.store.read()['active']
        print('Idle status: ' + json.dumps(status), flush=True)
        answer, result = send('What exact idle test marker did I just give you? Reply only that marker, no tools.')
        evidence['checks']['memoryAfterReconnect'] = marker in answer
        evidence['checks']['result'] = result
        assert marker in answer and controller.store.read()['active']
        assert status.get('status') in ('idle', 'dead', 'not-running', 'stopped')
        evidence['success'] = True
    except BaseException as exc:
        evidence.update(success=False, error=str(exc))
        raise
    finally:
        evidence['shutdown'] = controller.off()
        if not evidence['shutdown']['shutdownComplete']:
            evidence.update(success=False, shutdownError='Owned session shutdown is incomplete.')
        output = folder / 'evidence.json'
        output.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        print('Evidence: ' + str(output), flush=True)
        print(json.dumps(evidence), flush=True)
        if not evidence['shutdown']['shutdownComplete']:
            raise RuntimeError(evidence['shutdownError'])


if __name__ == '__main__': main()
