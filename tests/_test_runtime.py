"""Process-local crash reporting and temporary paths for test tools only."""
import faulthandler
import os
from pathlib import Path
import sys
import tempfile

_fault_streams = []  # Keep faulthandler's descriptor alive until process exit.
SEM_NOGPFAULTERRORBOX = 0x0002


def suppress_crash_dialogs():
    if sys.platform == 'win32':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.SetErrorMode(kernel.GetErrorMode() | SEM_NOGPFAULTERRORBOX)


def install_crash_diagnostics(path, timeout=None):
    suppress_crash_dialogs()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('w', encoding='utf-8')
    _fault_streams.append(stream)
    faulthandler.enable(file=stream, all_threads=True)
    if timeout is not None:
        faulthandler.dump_traceback_later(max(1, timeout), file=stream)
    return path


def use_local_temp(path):
    """Also reset tempfile's cache when a tool already imported tempfile."""
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    for key in ('TMP', 'TEMP', 'TMPDIR'):
        os.environ[key] = str(path)
    tempfile.tempdir = str(path)
    return path
