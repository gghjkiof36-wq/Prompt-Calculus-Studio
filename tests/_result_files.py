"""Mutate synthetic result files after workers drain, despite brief OS scans."""
import time


def change_result_file(path, content=None):
    """Only WinError 32 is transient; timeout and every other error stay failures.

    Callers must still wait for their own workers. This does not hide leaked
    handles: an unreleased handle fails after at most 500 ms of retry waits.
    """
    deadline=time.monotonic()+.5
    while True:
        try:
            return path.unlink() if content is None else path.write_bytes(content)
        except OSError as exc:
            remaining=deadline-time.monotonic()
            if getattr(exc,'winerror',None)!=32 or remaining<=0:raise
            time.sleep(min(.01,remaining))
