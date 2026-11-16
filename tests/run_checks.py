"""Serial checks using the same crash/data/temp isolation as fast_suite.

Pass --vendor, --output and --files (or --file-list), as for fast_suite.
This replaces task-local unittest bootstraps that lost native crash evidence.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _test_runtime import suppress_crash_dialogs
from fast_suite import main


if __name__ == '__main__':
    suppress_crash_dialogs()
    if not any(arg == '--jobs' or arg.startswith('--jobs=') for arg in sys.argv[1:]):
        sys.argv += ['--jobs', '1']
    raise SystemExit(main())
