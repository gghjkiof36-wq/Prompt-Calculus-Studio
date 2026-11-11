"""Migrated legacy journals drive real offscreen runners with fake native receipts."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from _qt_cleanup import close_window
from prompt_calculus_studio import journal_codec as codec, workspace_migration as migration
from prompt_calculus_studio.window import Window
from stage_fixture import ChainExecutor
from test_stage_enqueue_progress import scenario
from migration_fixture import source_hashes


def legacy_copy(db, path):
    """Generate a layout-0 fixture using the public decoded-record contract."""
    with closing(sqlite3.connect(path)) as old:
        db.backup(old)
        rows = old.execute('SELECT id,kind,workspace,owner,position,body,content_paths,projection FROM stage_header ORDER BY position,id').fetchall()
        decoded = codec.records(old, [(r[0], r[5], r[6], r[7]) for r in rows])
        with old:
            old.execute('CREATE TABLE stage_journal(id TEXT PRIMARY KEY,kind TEXT,workspace TEXT,owner TEXT,position INTEGER,body TEXT NOT NULL)')
            for row, body in zip(rows, decoded):
                old.execute('INSERT INTO stage_journal VALUES(?,?,?,?,?,?)', (*row[:5], json.dumps(body, ensure_ascii=False)))
            for table in ('stage_content_ref', 'stage_header', 'stage_content', 'storage_layout'):
                old.execute('DROP TABLE ' + table)


class MigratedBehaviorTests(unittest.TestCase):
    def test_migrated_resume_cancel_retry_and_inflight_receipt(self):
        for action, count in (('resume', 10), ('cancel', 3), ('retry', 1), ('inflight', 1)):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as temporary, scenario(False) as (case, config):
                base = Path(temporary)
                source, destination = base / 'old', base / 'new'
                (source / 'files').mkdir(parents=True)
                (source / 'files/example.md').write_text('# Synthetic migration')
                (source / 'data').mkdir()
                if action in ('retry', 'inflight'):
                    case.runner.start(count)
                    QTest.qWait(50)
                    self.assertTrue(case.executor.pending, case.notices)
                    if action == 'retry':
                        case.executor.finish(error=True)
                else:
                    # Accept the batch through the real entry point, but leave
                    # dispatch for the new installation after migration.
                    with patch.object(case.runner, 'later'):
                        case.runner.start(count)
                    case.runner.pause(case.runner.runs()[0]['id'])
                case.w.persist()
                run_id = case.runner.runs()[0]['id']
                attempts = case.runner.store.rows('attempt')
                prompt_ids = [row.get('prompt_id') for row in attempts]
                legacy_copy(case.w.store.db, source / 'data/studio.sqlite3')
                for name in ('originals', 'thumbnails'):
                    folder = Path(case.tmp.name) / name
                    if folder.exists():
                        shutil.copytree(folder, source / 'data' / name)
                before = source_hashes(source)
                result = migration.migrate(source, destination, allow_inflight=action == 'inflight')
                self.assertEqual(result['status'], 'done', result)
                window = Window(Path(result['data']))
                executor = None
                try:
                    window.comfy.timer.stop(); window.comfy.enabled = False
                    notices = []
                    window.notice = notices.append; window.error = notices.append
                    executor = ChainExecutor(window, base / 'new-executor')
                    runner = window.comfy.input_flow.chain
                    self.assertEqual(runner.store.read(run_id)['status'], 'paused')
                    self.assertEqual([a.get('prompt_id') for a in runner.store.rows('attempt')], prompt_ids)
                    QTest.qWait(40)
                    self.assertEqual(executor.submissions, [])
                    if action == 'cancel':
                        runner.cancel(run_id)
                        QTest.qWait(40)
                        self.assertEqual(runner.store.read(run_id)['status'], 'cancelled')
                        self.assertEqual(executor.submissions, [])
                        print(json.dumps(dict(action=action, requested=count, submissions=0, status='cancelled')), flush=True)
                    elif action == 'inflight':
                        # The original fake server owns the accepted prompt.
                        # Deliver its actual result to the new generation
                        # observer, without creating another native operation.
                        case.executor.finish()
                        executor.history.update(case.executor.history)
                        executor.results.update(case.executor.results)
                        executor.tick()
                        self.assertEqual(executor.submissions, [])
                        self.assertEqual(runner.store.rows('attempt')[0]['prompt_id'], prompt_ids[0])
                        self.assertEqual(runner.store.rows('attempt')[0]['status'], 'complete', notices)
                        print(json.dumps(dict(action=action, preserved_prompt_ids=prompt_ids, resubmissions=0)), flush=True)
                    else:
                        if action == 'retry':
                            runner.retry(attempts[0]['id'])
                        else:
                            runner.resume(run_id)
                        for _ in range(count):
                            QTest.qWait(50)
                            self.assertTrue(executor.pending, notices)
                            executor.finish()
                        QTest.qWait(50)
                        entries = runner.store.rows('entry')
                        completed = sum(row['status']['status_str'] == 'success' for row in executor.history.values())
                        self.assertEqual((len(entries), len(executor.submissions), completed), (count, count, count))
                        self.assertEqual(runner.store.read(run_id)['status'], 'complete', notices)
                        if action == 'retry':
                            self.assertEqual(runner.store.read(attempts[0]['id'])['status'], 'retried')
                            self.assertEqual(runner.store.read(attempts[0]['id'])['prompt_id'], prompt_ids[0])
                        print(json.dumps(dict(action=action, entries=len(entries), submissions=len(executor.submissions), completed=completed)), flush=True)
                    self.assertEqual(source_hashes(source), before)
                finally:
                    close_window(window)
                    if executor:
                        executor.close()


if __name__ == '__main__':
    unittest.main()
