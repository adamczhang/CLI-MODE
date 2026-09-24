"""Tie submitter children to the process that started them.

On Windows, every bridge, ACPX CLI or native CLI child is placed in one job
object owned by this process. When this process exits for any reason, including
being killed, Windows closes the job and ends those children, so a dead worker
never leaves an unobserved submitter behind. Silent breakaway keeps their own
children, such as the detached ACPX session owner, outside the job: accepted
work and the retained provider connection survive exactly as ACPX intends.

Elsewhere this is a no-op. Windows is the supported platform.
"""
import os

_job = None


def _create_job():
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
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimits()
    KILL_ON_JOB_CLOSE, SILENT_BREAKAWAY_OK = 0x2000, 0x1000
    limits.BasicLimitInformation.LimitFlags = KILL_ON_JOB_CLOSE | SILENT_BREAKAWAY_OK
    extended_limit_information = 9
    if not kernel.SetInformationJobObject(job, extended_limit_information, ctypes.byref(limits), ctypes.sizeof(limits)):
        return None
    # The handle is intentionally never closed: process exit closes it.
    return kernel, job


def bind(process):
    """Best effort; returns True when the child will not outlive this process."""
    global _job
    if os.name != 'nt':
        return False
    if _job is None:
        _job = _create_job() or False
    if not _job:
        return False
    kernel, job = _job
    try:
        return bool(kernel.AssignProcessToJobObject(job, int(process._handle)))
    except (AttributeError, TypeError, ValueError, OSError):
        return False  # Not a live Windows process handle.
