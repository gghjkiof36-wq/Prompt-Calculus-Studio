"""Isolated PCS test runner. See FAST_SUITE.md for commands and evidence.

Always use the selected Python with -I. --vendor adds that environment's vendor
explicitly; each child discovers from tests/ with the checkout as its cwd.
Known failures remain real failures (exit 1); the report classifies them only.
"""
import time
PROCESS_ENTERED = time.monotonic()

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from _test_runtime import install_crash_diagnostics, suppress_crash_dialogs, use_local_temp
STAGE = '''storage_transactions storage_save_dedup stage_atomic_transitions stage_capture_isolation stage_image_snapshot
material_cursor_upgrade queue_metadata 087_queue_history stage_refresh_projection
variant_composition_queue stage_parameter_persistence native_queue
stage_status_performance material_links prompt_groups prompt_material_resolution
stage_parameters stage_apply_boundaries maintenance_package shared_core_boundary prompt_batches
stage_cancel_all stage_click_regression stage_run_controls stage_borrow_guard'''.split()
CANVAS = '''087_canvas_operations canvas_wheel_backing canvas_module_header_cache
canvas_wheel_interaction canvas_pan_cache canvas_proxy_cache canvas_prompt_pan
canvas_surface canvas_preview_continuity canvas_startup canvas_recent_execution
canvas_combination_copy canvas_refresh_performance canvas_incremental_refresh canvas_gestures
canvas_variant_navigation canvas_navigation_rendering canvas_raster
088_canvas_entrypoints canvas_delete_keys canvas_cohesion canvas_four_modules canvas_card_menus'''.split()
WORKSPACE = ['workspace_ambient', 'request_lifetime', 'stage_invariants']
DOCUMENT = ['document_write_entries', 'document_write_routes', 'document_write_compatibility']
QUICK_STAGES = [
    'test_084_stages.StageTests.test_reopen_and_workspace_results_do_not_restart',
    'test_084_stages.StageTests.test_results_failure_refetch_without_generation_and_pause_resume',
    'test_084_stages.StageTests.test_pause_stops_supply_and_removed_waiting_item_is_not_submitted',
]

# Keep scheduling policy separate from the test lists: other channels append
# coverage there. Barriers retain the original list order and do not reduce it.
EXCLUSIVE_FILES = frozenset({
    'test_workspace_ambient.py',  # Fixed-frame paint counters and GUI timers.
    'test_event_snapshot.py',    # Real long-poll elapsed-time assertion.
    'test_modal_guard.py',       # Real five-second watchdog deadline.
})


def exclusive_file(name):
    pattern = name.split('::')[0]
    return pattern in EXCLUSIVE_FILES or pattern.endswith(('_performance.py', '_cost.py'))


def scheduling_groups(files):
    """Yield normal batches and singleton exclusive barriers, in list order."""
    batch = []
    for name in files:
        if exclusive_file(name):
            if batch:
                yield batch
                batch = []
            yield [name]
        else:
            batch.append(name)
    if batch:
        yield batch


def execute_files(files, jobs, run_file):
    """Yield completed rows; callers store them in the original input order."""
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for group in scheduling_groups(files):
            pending = [pool.submit(run_file, name) for name in group]
            for future in as_completed(pending):
                yield future.result()


def system_load_snapshot():
    """Read machine-wide counters, without dependencies or process inspection."""
    row = dict(utc=datetime.now(timezone.utc).isoformat(), monotonic=time.monotonic())
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            ticks = lambda value: (value.dwHighDateTime << 32) | value.dwLowDateTime
            row.update(cpu_idle=ticks(idle), cpu_total=ticks(kernel) + ticks(user),
                       cpu_counter_source='GetSystemTimes')

        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD)] + [
                (name, ctypes.c_ulonglong) for name in ('total_physical', 'available_physical',
                    'total_pagefile', 'available_pagefile', 'total_virtual', 'available_virtual', 'extended_virtual')]
        memory = MemoryStatus()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            row.update(memory_used_percent=memory.load, available_memory_bytes=memory.available_physical)
    elif hasattr(os, 'getloadavg'):
        row['load_average'] = list(os.getloadavg())
    return row


class LoadSampler:
    def __init__(self, interval=2):
        self.interval = interval
        self.samples = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name='test-load-sampler', daemon=True)

    def _sample(self):
        try:
            row = system_load_snapshot()
        except (OSError, AttributeError) as error:
            row = dict(utc=datetime.now(timezone.utc).isoformat(), error=str(error))
        if self.samples and 'cpu_total' in row and 'cpu_total' in self.samples[-1]:
            previous = self.samples[-1]
            total = row['cpu_total'] - previous['cpu_total']
            if total > 0:
                row['cpu_busy_percent'] = round(100 * (1 - (row['cpu_idle'] - previous['cpu_idle']) / total), 3)
        self.samples.append(row)

    def _run(self):
        while not self.stop_event.wait(self.interval):
            self._sample()

    def __enter__(self):
        self._sample()
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop_event.set()
        self.thread.join()
        self._sample()

    def summary(self):
        first, last = self.samples[0], self.samples[-1]
        total = last.get('cpu_total', 0) - first.get('cpu_total', 0)
        busy = [row['cpu_busy_percent'] for row in self.samples if 'cpu_busy_percent' in row]
        return dict(logical_cpus=os.cpu_count(), sample_interval_seconds=self.interval,
                    samples=len(self.samples),
                    cpu_busy_mean_percent=round(100 * (1 - (last['cpu_idle'] - first['cpu_idle']) / total), 3) if total else None,
                    cpu_busy_peak_percent=max(busy, default=None),
                    scope='Machine-wide load includes this suite and concurrent external work.')


def timing_rankings(rows):
    files = sorted((dict(file=row['file'], seconds=row['seconds'],
                         timings=row.get('timings', {})) for row in rows),
                   key=lambda row: row['seconds'], reverse=True)
    tests = sorted((dict(file=row['file'], **test) for row in rows
                    for test in row.get('test_timings', [])),
                   key=lambda row: row['seconds'], reverse=True)
    return dict(files=files, tests=tests)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def result_class(guard, borrow_guard, records, fixture_outcomes):
    """Record unittest outcomes without changing setup/teardown dispatch."""
    class Result(unittest.TextTestResult):
        active_id = None

        def startTest(self, test):
            self.test_started = time.monotonic()
            self.active_id = test.id()
            guard.current_test = test.id()
            borrow_guard.current_test = test.id()
            self.event_offset = len(guard.events)
            self.borrow_offset = len(borrow_guard.events)
            self.row = dict(id=test.id(), outcomes=[])
            records.append(self.row)
            super().startTest(test)

        def stopTest(self, test):
            self.row['seconds'] = round(time.monotonic() - self.test_started, 6)
            self.active_id = None
            super().stopTest(test)

        def outcome_row(self, test):
            if self.active_id == test.id():
                return self.row
            # unittest reports setUpClass/tearDownClass/module failures through
            # _ErrorHolder without startTest. Keep them out of real test counts.
            row = dict(id=test.id(), outcomes=[])
            fixture_outcomes.append(row)
            return row

        def addSuccess(self, test):
            if (len(guard.events) > self.event_offset or
                    any(not event['expected'] for event in borrow_guard.events[self.borrow_offset:])):
                try:
                    raise AssertionError('Test guard violation was caught or escaped through a Qt callback')
                except AssertionError:
                    self.addFailure(test, sys.exc_info())
            else:
                self.row['outcomes'].append(dict(status='pass'))
                super().addSuccess(test)

        def addFailure(self, test, err):
            self.outcome_row(test)['outcomes'].append(dict(status='fail', detail=self._exc_info_to_string(err, test)))
            super().addFailure(test, err)

        def addError(self, test, err):
            self.outcome_row(test)['outcomes'].append(dict(status='error', detail=self._exc_info_to_string(err, test)))
            super().addError(test, err)

        def addSkip(self, test, reason):
            self.outcome_row(test)['outcomes'].append(dict(status='skip', detail=reason))
            super().addSkip(test, reason)

        def addExpectedFailure(self, test, err):
            self.outcome_row(test)['outcomes'].append(dict(status='expected_failure', detail=self._exc_info_to_string(err, test)))
            super().addExpectedFailure(test, err)

        def addUnexpectedSuccess(self, test):
            self.outcome_row(test)['outcomes'].append(dict(status='unexpected_success'))
            super().addUnexpectedSuccess(test)

        def addSubTest(self, test, subtest, err):
            if err:
                self.row['outcomes'].append(dict(status='fail', subtest=str(subtest),
                                                 detail=self._exc_info_to_string(err, test)))
            super().addSubTest(test, subtest, err)
    return Result


def child(args):
    bootstrap_started = time.monotonic()
    sys.path[:0] = [str(ROOT / 'tests'), str(ROOT), str(Path(args.vendor).resolve())]
    sys.dont_write_bytecode = True
    import PySide6
    import _modal_guard as guard
    guard.install()
    import _borrow_guard as borrow_guard
    borrow_guard.install_from_env()
    bootstrap_seconds = time.monotonic() - bootstrap_started
    records, fixture_outcomes = [], []

    discovery_started = time.monotonic()
    loader = unittest.TestLoader()
    if args.quick_stages:
        # Discover first, so sibling imports follow the same path as a full run.
        loader.discover(str(ROOT / 'tests'), pattern='test_084_stages.py')
        suite = loader.loadTestsFromNames(QUICK_STAGES)
    else:
        suite = loader.discover(str(ROOT / 'tests'), pattern=args.child)
    if args.case:
        # Discovery still establishes sibling imports; retain exact public ids.
        suite = loader.loadTestsFromNames(args.case)
    if args.repeat > 1:
        def ids(group):
            for item in group:
                if isinstance(item, unittest.TestSuite):
                    yield from ids(item)
                else:
                    yield item.id()
        selected = list(ids(suite))
        suite = unittest.TestSuite(loader.loadTestsFromNames(selected) for _ in range(args.repeat))
    discovery_seconds = time.monotonic() - discovery_started
    suite_started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2,
        resultclass=result_class(guard, borrow_guard, records, fixture_outcomes)).run(suite)
    suite_seconds = time.monotonic() - suite_started
    timings = dict(bootstrap_seconds=round(bootstrap_seconds, 6),
                   discovery_seconds=round(discovery_seconds, 6),
                   suite_seconds=round(suite_seconds, 6),
                   test_seconds=round(sum(row['seconds'] for row in records), 6),
                   fixture_and_reporting_seconds=round(suite_seconds - sum(row['seconds'] for row in records), 6),
                   child_seconds=round(time.monotonic() - PROCESS_ENTERED, 6))
    write_json(Path(args.result), dict(tests=records, tests_run=result.testsRun, skipped=len(result.skipped),
               fixture_outcomes=fixture_outcomes, modal_timeouts=guard.events,
               borrow_guard=os.environ.get('PCS_BORROW_GUARD') == '1',
               borrow_events=borrow_guard.events, borrow_calls=borrow_guard.calls,
               python=sys.version, pyside=PySide6.__version__, timings=timings))
    return 0 if (result.wasSuccessful() and result.testsRun and not result.skipped
                 and not result.expectedFailures and not guard.events and not borrow_guard.unexpected_events()) else 1


def run_file(args, known, name):
    quick = name.endswith('::quick')
    pattern = name.split('::')[0]
    folder = args.output / name.replace('.py', '').replace('::', '-')
    folder.mkdir()
    temp = folder / 'tmp'
    temp.mkdir()
    data = folder / 'data'
    data.mkdir()
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PROMPT_STUDIO_DATA=str(data),
               TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp), PYTHONIOENCODING='utf-8',
               PCS_TEST_MODAL_GUARD='1' if args.guard == 'on' else '0',
               PCS_BORROW_GUARD='1' if args.borrow_guard else '0')
    command = [sys.executable, '-I', str(Path(__file__).resolve()), '--vendor', args.vendor,
               '--child', pattern, '--timeout', str(args.timeout), '--result', str(folder / 'tests.json'),
               '--dump', str(folder / 'stacks.txt')]
    if quick:
        command.append('--quick-stages')
    if getattr(args, 'case', None):
        command += ['--case', *args.case]
    if getattr(args, 'repeat', 1) != 1:
        command += ['--repeat', str(args.repeat)]
    tick = time.monotonic()
    with (folder / 'stdout.txt').open('w', encoding='utf-8') as stdout, (folder / 'stderr.txt').open('w', encoding='utf-8') as stderr:
        proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stdout, stderr=stderr)
        try:
            code = proc.wait(timeout=args.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            code = 'timeout'
    elapsed = time.monotonic() - tick
    errors = (folder / 'stderr.txt').read_text(encoding='utf-8', errors='replace')
    result = json.loads((folder / 'tests.json').read_text(encoding='utf-8')) if (folder / 'tests.json').exists() else {}
    failures = [dict(id=test['id'], **outcome) for test in result.get('tests', []) + result.get('fixture_outcomes', [])
                for outcome in test['outcomes'] if outcome['status'] != 'pass']
    for failure in failures:
        failure['known'] = any(failure['id'] in item['test_ids'] and
                               item['match'] in failure.get('detail', '') for item in known)
    timings = result.get('timings', {})
    if 'child_seconds' in timings:
        # Includes interpreter startup/imports before PROCESS_ENTERED, final JSON
        # write and interpreter shutdown; it is not pure interpreter startup.
        timings['process_overhead_seconds'] = round(max(0, elapsed - timings['child_seconds']), 6)
    remaining = sorted(p.relative_to(temp).as_posix() for p in temp.iterdir())
    row = dict(file=name, code=code, seconds=round(elapsed, 3), command=command,
               winerror32=errors.count('WinError 32'), winerror3=errors.count('[WinError 3]'),
               remaining_temp_dirs=sum(p.is_dir() for p in temp.iterdir()), remaining_temp=remaining,
               tests_run=result.get('tests_run', len(result.get('tests', []))),
               passed=sum(bool(t['outcomes']) and all(o['status'] == 'pass' for o in t['outcomes']) for t in result.get('tests', [])),
               failures=failures, modal_timeouts=result.get('modal_timeouts', []),
               borrow_violations=[e for e in result.get('borrow_events', []) if not e['expected']],
               borrow_positive_controls=sum(e['expected'] for e in result.get('borrow_events', [])),
               borrow_calls=result.get('borrow_calls', {}),
               python=result.get('python'), pyside=result.get('pyside'))
    row.update(timings=timings,
               test_timings=[dict(id=test['id'], seconds=test['seconds'])
                             for test in result.get('tests', []) if 'seconds' in test],
               skipped=result.get('skipped', sum(any(o['status'] == 'skip' for o in t['outcomes']) for t in result.get('tests', []))),
               exclusive=exclusive_file(name), started_monotonic=round(tick, 6),
               ended_monotonic=round(tick + elapsed, 6))
    row['incomplete_tests'] = [test['id'] for test in result.get('tests', []) if not test['outcomes']]
    row['accepted'] = (code in (0, 1) and row['tests_run'] > 0 and not row['modal_timeouts']
                       and row['tests_run'] == len(result.get('tests', [])) and not row['incomplete_tests']
                       and not row['borrow_violations']
                       and not remaining and not row['winerror32']
                       and all(f['known'] for f in failures) and (code == 0 or bool(failures)))
    write_json(folder / 'result.json', row)
    return row


def main():
    suppress_crash_dialogs()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--files', nargs='+')
    parser.add_argument('--file-list', type=Path)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--jobs', type=int, default=2, help='Concurrent isolated files (default: 2; serial baseline: 1)')
    parser.add_argument('--guard', choices=('on', 'off'), default='on')
    parser.add_argument('--borrow-guard', action='store_true', default=os.environ.get('PCS_BORROW_GUARD') == '1')
    parser.add_argument('--source-commit', help='Provenance for an exported tree without .git')
    parser.add_argument('--child')
    parser.add_argument('--result')
    parser.add_argument('--dump')
    parser.add_argument('--quick-stages', action='store_true')
    parser.add_argument('--case', nargs='+', help='Exact unittest ids; diagnostics with one --files entry only')
    parser.add_argument('--repeat', type=int, default=1, help='Consecutive fresh test fixtures per process')
    args = parser.parse_args()
    if args.child:
        use_local_temp(Path(args.result).parent / 'tmp')
        install_crash_diagnostics(args.dump, max(10, args.timeout - 10))
        # Leave the watchdog active through interpreter/Qt teardown too. A
        # successful unittest result does not prove the process can exit.
        # CPython cancels this watchdog during its normal finalization.
        return child(args)
    if not args.output:
        parser.error('--output is required')
    fast_mode = not args.files and not args.file_list
    if args.repeat < 1:
        parser.error('--repeat must be positive')
    if (args.case or args.repeat != 1) and fast_mode:
        parser.error('Case selection/repetition requires explicit --files/--file-list diagnostics')
    if args.case and (not args.files or len(args.files) != 1):
        parser.error('--case requires exactly one --files entry')
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if args.jobs <= 0:
        parser.error('--jobs must be positive')
    if fast_mode and (args.timeout > 120 or args.guard != 'on'):
        parser.error('The fast suite requires guard on and a per-file limit of at most 120 seconds; use --files/--file-list for diagnostics')
    files = args.files or (args.file_list.read_text(encoding='utf-8-sig').split() if args.file_list else
                          ['test_' + name + '.py' for name in STAGE + CANVAS + WORKSPACE + DOCUMENT] + ['test_084_stages.py::quick'])
    if not files or len(set(files)) != len(files):
        parser.error('Expected a nonempty file list without duplicates')
    for name in files:
        pattern = name.removesuffix('::quick')
        if '/' in pattern or '\\' in pattern or ':' in pattern or not pattern.startswith('test_') or not pattern.endswith('.py'):
            parser.error(f'Expected test filename, got {name!r}')
        if name.endswith('::quick') and pattern != 'test_084_stages.py':
            parser.error('::quick is only supported for test_084_stages.py')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    known_path = ROOT / 'tests' / 'known_failures.json'
    known = json.loads(known_path.read_text(encoding='utf-8')) if known_path.exists() else []
    started = time.monotonic()
    started_utc = datetime.now(timezone.utc).isoformat()
    rows = []
    positions = {name: index for index, name in enumerate(files)}
    with LoadSampler() as load:
        for row in execute_files(files, args.jobs, lambda name: run_file(args, known, name)):
            rows.append(row)
            rows.sort(key=lambda item: positions[item['file']])
            write_json(args.output / 'results.json', rows)
            print(f"{row['file']}: exit={row['code']} {row['seconds']:.2f}s "
                  f"pass={row['passed']}/{row['tests_run']} w32={row['winerror32']} "
                  f"remaining={len(row['remaining_temp'])}", flush=True)
    write_json(args.output / 'load.json', load.samples)
    write_json(args.output / 'timings.json', timing_rankings(rows))
    try:
        commit = subprocess.check_output(['git', '-c', f'safe.directory={ROOT}', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = args.source_commit
    elapsed = round(time.monotonic() - started, 3)
    budget = 360 if fast_mode else None
    report = dict(commit=commit,
                  root=str(ROOT), guard=args.guard, borrow_guard=args.borrow_guard, platform='offscreen', vendor=args.vendor,
                  seconds=elapsed, budget_seconds=budget, files=rows, jobs=args.jobs,
                  started_utc=started_utc, ended_utc=datetime.now(timezone.utc).isoformat(),
                  exclusive_files=[name for name in files if exclusive_file(name)], load=load.summary(),
                  tests_run=sum(row['tests_run'] for row in rows), passed=sum(row['passed'] for row in rows),
                  skipped=sum(row['skipped'] for row in rows),
                  accepted=all(row['accepted'] for row in rows) and (budget is None or elapsed <= budget))
    write_json(args.output / 'summary.json', report)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
