"""Managed ACP launcher and explicit, prompt-free interactive sign-in client."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def join_kill_on_close_job():
    """Put this launcher in a job that Windows ends, with everything in it, when the launcher exits.

    agy_acp_server.exe starts a second agy_acp_server.exe (with localharness_external.exe)
    and does not stop it when it exits, and Windows does not end children with their parent,
    so every server start used to leave that pair running (2026-09-23: 19 pairs, about 2 GB).
    The server and its children join the job as they start; no breakaway is allowed. Setup
    copies this file on its own, so it cannot share processes.py. Best effort: returns False
    where Windows refuses (or elsewhere), and the server still runs. Used for serving only.
    """
    if os.name != 'nt':
        return False
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IoCounters),
                    ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        return False
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE; this process holds the only handle.
    extended_limit_information = 9
    return bool(kernel.SetInformationJobObject(job, extended_limit_information, ctypes.byref(limits),
                                               ctypes.sizeof(limits))
                and kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()))


class RPC:
    def __init__(self, command, env):
        self.process = subprocess.Popen(command, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8')
        self.events = queue.Queue()
        self.serial = 0
        def read():
            for line in self.process.stdout:
                try:
                    self.events.put(json.loads(line))
                except ValueError:
                    pass
            self.events.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()

    def request(self, method, params, timeout=60):
        self.serial += 1
        self.write(dict(jsonrpc='2.0', id=self.serial, method=method, params=params))
        deadline = time.monotonic() + timeout
        while True:
            try:
                event = self.events.get(timeout=max(.01, deadline-time.monotonic()))
            except queue.Empty:
                raise RuntimeError('Authentication timed out. Retry or sign in separately.') from None
            if event is None:
                raise RuntimeError('ACP runtime exited before verification.')
            if event.get('id') == self.serial and 'method' not in event:
                if 'error' in event:
                    # Runtime diagnostics may contain auth URLs or credentials.
                    raise RuntimeError('ACP rejected ' + method + '; complete sign-in separately or retry.')
                return event.get('result', {})
            if 'id' in event and 'method' in event:
                self.write(dict(jsonrpc='2.0', id=event['id'],
                    error=dict(code=-32601, message='Setup does not provide workspace tools.')))
            if time.monotonic() >= deadline:
                raise RuntimeError('ACP verification timed out.')

    def write(self, event):
        self.process.stdin.write(json.dumps(event)+'\n')
        self.process.stdin.flush()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()


def login(command, env, workspace, choose=input):
    manifest = Path(__file__).resolve().parents[1] / '.codex-plugin' / 'plugin.json'
    version = json.loads(manifest.read_text(encoding='utf-8'))['version']
    rpc = RPC(command, env)
    try:
        info = rpc.request('initialize', dict(protocolVersion=1, clientCapabilities={},
            clientInfo=dict(name='cli-mode-setup', version=version)))
        methods = [m for m in info.get('authMethods', []) if m.get('type', 'agent') == 'agent']
        if not methods:
            raise RuntimeError('No protocol-driven sign-in advertised. Use an interactive ACP client separately.')
        personal = [m for m in methods if m.get('id') == 'oauth-personal']
        if personal:
            selected = personal[0]
        elif len(methods) == 1:
            selected = methods[0]
        else:
            for i, method in enumerate(methods, 1):
                print(str(i)+'. '+method.get('name', method['id']), flush=True)
            index = int(choose('Choose a sign-in method: '))
            if not 1 <= index <= len(methods):
                raise ValueError('Select a displayed authentication method.')
            selected = methods[index-1]
        print('Complete the ACP sign-in in your browser if it opens.', flush=True)
        rpc.request('authenticate', {'methodId': selected['id']}, timeout=600)
        result = rpc.request('session/new', dict(cwd=str(workspace), mcpServers=[]))
        if not isinstance(result.get('sessionId'), str) or not result['sessionId']:
            raise RuntimeError('ACP did not establish an authenticated session.')
        # No session/prompt is ever sent. Closing the setup connection ends its process.
        print('ACP connection verified. No agent task was sent.', flush=True)
    finally:
        rpc.close()


def main():
    root = Path(__file__).resolve().parent
    env = dict(os.environ)
    for key in ('GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS',
                'GOOGLE_CLOUD_PROJECT', 'GOOGLE_CLOUD_LOCATION', 'GOOGLE_GENAI_USE_VERTEXAI'):
        env.pop(key, None)
    env['ANTIGRAVITY_HARNESS_PATH'] = str(root/'localharness_external.exe')
    env['GEMINI_HOME'] = str(root/'profile')
    env['AGY_ACP_FORCE_FILE_STORAGE'] = '1'
    command = [str(root/'agy_acp_server.exe')]
    if sys.argv[1:] == ['--login']:
        settings = root/'profile/antigravity-acp/settings.json'
        settings.parent.mkdir(parents=True, exist_ok=True)
        if not settings.exists():
            settings.write_text(json.dumps({'auth': {'type': 'oauth-personal'}}), encoding='utf-8')
        login(command, env, root)
    elif not sys.argv[1:]:
        # Serving only: sign-in may open the user's browser, which must not end with setup.
        join_kill_on_close_job()
        raise SystemExit(subprocess.call(command, env=env))
    else:
        raise ValueError('Unsupported launcher arguments.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, IndexError, KeyboardInterrupt) as exc:
        print('ACP setup incomplete: '+str(exc), file=sys.stderr)
        raise SystemExit(1)
