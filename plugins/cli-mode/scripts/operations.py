"""Shared operation facts: liveness, admission and output. No provider calls."""
import json
import math
import os
import time

_detached_workers = []  # Keep Popen handles until a later launch can reap exited workers.


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def operation_running(operation):
    if not operation.get('running', True):
        return False
    pid = operation.get('submitterPid', operation.get('pid'))
    if not isinstance(pid, int) or pid <= 0:
        return True  # Unknown liveness is not proof that work stopped.
    if os.name == 'nt':
        # os.kill(pid, 0) is not a safe liveness probe on Windows.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Missing PID; access denied stays unknown/live.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def follow_path(store, request_id):
    """Where a running `follow` of this request (Claude Code's background task) keeps its process ID."""
    return store.request_path(request_id).with_name('follow-' + request_id + '.pid')


def following(store, request_id):
    """True while a `follow` of this request is running."""
    try:
        pid = int(follow_path(store, request_id).read_text(encoding='ascii'))
    except (OSError, ValueError):
        return False
    return operation_running(dict(pid=pid))


def pending_work(state, request_id=None, include_queue=True, session=None):
    """One admission gate for prompts, settings, and legacy uncertain receipts.

    With `session`, only that agent's work counts: agents run side by side, each with its own queue.
    """
    operations = [op for op in state['inflight'].values() if session is None or op.get('session') == session]
    requests = [(key, record) for key, record in state.get('requests', {}).items()
                if session is None or record.get('session') == session]
    return (any(op.get('requestId') != request_id or request_id is None for op in operations) or
            any(key != request_id and record['status'] in ('submitting', 'uncertain') for key, record in requests) or
            (include_queue and any(key != request_id and record['status'] == 'captured'
                                   for key, record in requests)))


def menu_holds(state, session):
    """True while an open menu is changing this agent (its settings, or the native switch): nothing is sent to it.
    A menu for another agent, or for a new one, never holds this agent's queue."""
    pending = state.get('pending') or {}
    return bool(pending) and (pending.get('session') == session or pending.get('phase') == 'native')


def status_age(started, now=None):
    """A clock fact for queue UX, never a claim about provider progress."""
    if type(started) not in (int, float):
        return None
    try:
        if not math.isfinite(started):
            return None
    except OverflowError:
        return None
    now = time.time() if now is None else now
    return max(0, int(now - started))
