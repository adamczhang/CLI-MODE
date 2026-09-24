"""Observe test-owned Windows process trees without terminating any process.

Start alongside full_scale_validation.py; write stop-audit in its output folder
after the runner finishes. PID plus creation time protects against PID reuse.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


def shared_json(path):
    """Do not block an ACPX atomic replacement while observing its records."""
    if os.name != 'nt':
        return json.loads(path.read_text(encoding='utf-8'))
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    with os.fdopen(fd, 'r', encoding='utf-8') as source:
        return json.load(source)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    previous = root / 'process-audit.json'
    history = json.loads(previous.read_text())['history'] if previous.exists() else []
    known = {str(v['ProcessId']) + '|' + str(v['CreationDate']): v for v in history}
    query = ('Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,'
             'CreationDate,CommandLine | ConvertTo-Json -Compress')
    while True:
        result = subprocess.run(['powershell', '-NoProfile', '-Command', query],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        result.check_returncode()
        processes = json.loads(result.stdout)
        sessions = set()
        for path in (Path.home() / '.acpx/sessions').glob('*.json'):
            if path.name == 'index.json':
                continue
            try:
                value = shared_json(path)
            except (OSError, ValueError):
                continue
            cwd = str(value.get('cwd', '')).replace('\\', '/').lower()
            if str(root).replace('\\', '/').lower() in cwd:
                sessions.add(value.get('acp_session_id'))
        owners = set()
        for path in (Path.home() / '.acpx/queues').glob('*.lock'):
            try:
                value = shared_json(path)
            except (OSError, ValueError):
                continue
            if value.get('sessionId') in sessions:
                owners.add(value['pid'])
        matched = set(owners)
        for value in processes:
            command = value.get('CommandLine') or ''
            key = str(value['ProcessId']) + '|' + str(value['CreationDate'])
            if key in known or ('full_scale_validation.py' in command and '--output' in command
                               and 'powershell' not in command.lower() and 'pwsh' not in command.lower()):
                matched.add(value['ProcessId'])
        while True:
            descendants = {v['ProcessId'] for v in processes if v['ParentProcessId'] in matched}
            if descendants <= matched:
                break
            matched.update(descendants)
        current = []
        for value in processes:
            if value['ProcessId'] in matched:
                key = str(value['ProcessId']) + '|' + str(value['CreationDate'])
                known.setdefault(key, dict(value, firstSeen=time.time()))['lastSeen'] = time.time()
                current.append(value)
        report = dict(observed=len(known), current=current, history=list(known.values()), readOnly=True)
        (root / 'process-audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        if (root / 'stop-audit').exists():
            print(json.dumps(dict(observed=len(known), remaining=len(current))))
            return 1 if current else 0
        time.sleep(5)


if __name__ == '__main__':
    raise SystemExit(main())
