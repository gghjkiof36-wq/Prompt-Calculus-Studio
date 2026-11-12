"""Serial, isolated PCS test runner. See FAST_SUITE.md for commands and evidence.

Always use the selected Python with -I. --vendor adds that environment's vendor
explicitly; each child discovers from tests/ with the checkout as its cwd.
Known failures remain real failures (exit 1); the report classifies them only.
"""
import argparse
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
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


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def child(args):
    sys.path[:0] = [str(ROOT / 'tests'), str(ROOT), str(Path(args.vendor).resolve())]
    sys.dont_write_bytecode = True
    import PySide6
    import _modal_guard as guard
    guard.install()
    import _borrow_guard as borrow_guard
    borrow_guard.install_from_env()
    records = []

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            guard.current_test = test.id()
            borrow_guard.current_test = test.id()
            self.event_offset = len(guard.events)
            self.borrow_offset = len(borrow_guard.events)
            self.row = dict(id=test.id(), outcomes=[])
            records.append(self.row)
            super().startTest(test)

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
            self.row['outcomes'].append(dict(status='fail', detail=self._exc_info_to_string(err, test)))
            super().addFailure(test, err)

        def addError(self, test, err):
            self.row['outcomes'].append(dict(status='error', detail=self._exc_info_to_string(err, test)))
            super().addError(test, err)

        def addSkip(self, test, reason):
            self.row['outcomes'].append(dict(status='skip', detail=reason))
            super().addSkip(test, reason)

        def addExpectedFailure(self, test, err):
            self.row['outcomes'].append(dict(status='expected_failure', detail=self._exc_info_to_string(err, test)))
            super().addExpectedFailure(test, err)

        def addUnexpectedSuccess(self, test):
            self.row['outcomes'].append(dict(status='unexpected_success'))
            super().addUnexpectedSuccess(test)

        def addSubTest(self, test, subtest, err):
            if err:
                self.row['outcomes'].append(dict(status='fail', subtest=str(subtest),
                                                 detail=self._exc_info_to_string(err, test)))
            super().addSubTest(test, subtest, err)

    loader = unittest.TestLoader()
    if args.quick_stages:
        # Discover first, so sibling imports follow the same path as a full run.
        loader.discover(str(ROOT / 'tests'), pattern='test_084_stages.py')
        suite = loader.loadTestsFromNames(QUICK_STAGES)
    else:
        suite = loader.discover(str(ROOT / 'tests'), pattern=args.child)
    with open(args.dump, 'w', encoding='utf-8') as dump:
        faulthandler.dump_traceback_later(max(10, args.timeout - 10), file=dump)
        try:
            result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
        finally:
            faulthandler.cancel_dump_traceback_later()
    write_json(Path(args.result), dict(tests=records, modal_timeouts=guard.events,
               borrow_guard=os.environ.get('PCS_BORROW_GUARD') == '1',
               borrow_events=borrow_guard.events, borrow_calls=borrow_guard.calls,
               python=sys.version, pyside=PySide6.__version__))
    return 0 if (result.wasSuccessful() and result.testsRun and not result.skipped
                 and not result.expectedFailures and not guard.events and not borrow_guard.unexpected_events()) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--files', nargs='+')
    parser.add_argument('--file-list', type=Path)
    parser.add_argument('--timeout', type=int, default=120)
    parser.add_argument('--guard', choices=('on', 'off'), default='on')
    parser.add_argument('--borrow-guard', action='store_true', default=os.environ.get('PCS_BORROW_GUARD') == '1')
    parser.add_argument('--source-commit', help='Provenance for an exported tree without .git')
    parser.add_argument('--child')
    parser.add_argument('--result')
    parser.add_argument('--dump')
    parser.add_argument('--quick-stages', action='store_true')
    args = parser.parse_args()
    if args.child:
        return child(args)
    if not args.output:
        parser.error('--output is required')
    fast_mode = not args.files and not args.file_list
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    if fast_mode and (args.timeout > 120 or args.guard != 'on'):
        parser.error('The fast suite requires guard on and a per-file limit of at most 120 seconds; use --files/--file-list for diagnostics')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    files = args.files or (args.file_list.read_text(encoding='utf-8-sig').split() if args.file_list else
                          ['test_' + name + '.py' for name in STAGE + CANVAS + WORKSPACE + DOCUMENT] + ['test_084_stages.py::quick'])
    known_path = ROOT / 'tests' / 'known_failures.json'
    known = json.loads(known_path.read_text(encoding='utf-8')) if known_path.exists() else []
    started = time.monotonic()
    rows = []
    for name in files:
        quick = name.endswith('::quick')
        pattern = name.split('::')[0]
        if '/' in pattern or '\\' in pattern or not pattern.startswith('test_') or not pattern.endswith('.py'):
            parser.error(f'Expected test filename, got {name!r}')
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
        failures = [dict(id=test['id'], **outcome) for test in result.get('tests', [])
                    for outcome in test['outcomes'] if outcome['status'] != 'pass']
        for failure in failures:
            failure['known'] = any(failure['id'] in item['test_ids'] and
                                   item['match'] in failure.get('detail', '') for item in known)
        remaining = sorted(p.relative_to(temp).as_posix() for p in temp.iterdir())
        row = dict(file=name, code=code, seconds=round(elapsed, 3), command=command,
                   winerror32=errors.count('WinError 32'), winerror3=errors.count('[WinError 3]'),
                   remaining_temp_dirs=sum(p.is_dir() for p in temp.iterdir()), remaining_temp=remaining,
                   tests_run=len(result.get('tests', [])),
                   passed=sum(all(o['status'] == 'pass' for o in t['outcomes']) for t in result.get('tests', [])),
                   failures=failures, modal_timeouts=result.get('modal_timeouts', []),
                   borrow_violations=[e for e in result.get('borrow_events', []) if not e['expected']],
                   borrow_positive_controls=sum(e['expected'] for e in result.get('borrow_events', [])),
                   borrow_calls=result.get('borrow_calls', {}),
                   python=result.get('python'), pyside=result.get('pyside'))
        row['accepted'] = (code in (0, 1) and row['tests_run'] > 0 and not row['modal_timeouts']
                           and not row['borrow_violations']
                           and not remaining and not row['winerror32']
                           and all(f['known'] for f in failures) and (code == 0 or bool(failures)))
        rows.append(row)
        write_json(folder / 'result.json', row)
        write_json(args.output / 'results.json', rows)
        print(f"{name}: exit={code} {elapsed:.2f}s pass={row['passed']}/{row['tests_run']} "
              f"w32={row['winerror32']} remaining={len(remaining)}", flush=True)
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except subprocess.CalledProcessError:
        commit = args.source_commit
    elapsed = round(time.monotonic() - started, 3)
    budget = 360 if fast_mode else None
    report = dict(commit=commit,
                  root=str(ROOT), guard=args.guard, borrow_guard=args.borrow_guard, platform='offscreen', vendor=args.vendor,
                  seconds=elapsed, budget_seconds=budget, files=rows,
                  accepted=all(row['accepted'] for row in rows) and (budget is None or elapsed <= budget))
    write_json(args.output / 'summary.json', report)
    return 0 if report['accepted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
