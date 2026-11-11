"""Lossless storage, projections, corruption, atomic migration and opening guards."""
import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from prompt_calculus_studio import journal_codec as codec, storage_layout as layout
from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.stage_store import StageStore
from journal_fixtures import bodies, legacy, snapshot


class CodecTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = StageStore(self.db)

    def insert(self, body, position=17):
        with self.store.transaction():
            codec.write(self.db, body, position)

    def test_all_kinds_legacy_frames_round_retry_unknown_and_positions_roundtrip(self):
        for operation in (lambda: self.store.update('missing'), lambda: self.store.patch('missing', status='waiting'),
                          lambda: self.store.replace_record({'id': 'missing'}, 0)):
            with self.assertRaisesRegex(ValueError, '^執行紀錄不存在。$'):operation()
        for i, body in enumerate(bodies()):
            self.insert(body, i * 7 + 3)
            self.assertTrue(codec.identical(body, self.store.read(body['id'])))
            self.assertEqual(self.db.execute('SELECT position FROM stage_header WHERE id=?', (body['id'],)).fetchone()[0], i * 7 + 3)
        self.assertNotIn('position', self.store.read('r1'))
        self.assertEqual(self.store.read('r2')['position'], 0)
        self.assertEqual(self.store.entry_attempts('w', ['r1']), [bodies()[2]])
        self.assertEqual(self.store.next_entry('owner'), bodies()[1])
        header = json.loads(self.db.execute("SELECT body FROM stage_header WHERE id='r0'").fetchone()[0])
        self.assertIsNone(header['stack'][0]['ctx']['saved']['scene'])
        self.assertIsNone(header['stack'][1])
        self.assertEqual(len(header['stack']), 3)
        self.assertEqual(header['future'], bodies()[0]['future'])
        self.assertEqual(codec._parts(codec.pointer(('', 'a/~', '字'))), ['', 'a/~', '字'])

    def test_shared_content_return_ownership_and_gc_after_snapshot_deletion(self):
        scene = {'items': [{'text': 'frozen'}]}
        batch = self.store.add('batch_snapshot', 'w', scene=scene)
        entry = self.store.add('entry', 'w', saved={'scene': scene}, status='waiting')
        other = self.store.add('entry', 'w', saved={'scene': scene}, status='waiting')
        run = self.store.add('run', 'w', frozen_scene=scene, status='complete')
        attempt = self.store.add('attempt', 'w', snapshot_state=scene)
        digest = hashlib.sha256(codec.canonical(scene)).hexdigest()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM stage_content_ref WHERE sha256=?', (digest,)).fetchone()[0], 5)
        self.store.delete_batch_snapshot(batch['id'])
        rows = self.store.rows('entry')
        rows[0]['saved']['scene']['items'][0]['text'] = 'edited'
        self.assertEqual(rows[1], other)
        self.store.patch(entry['id'], saved=rows[0]['saved'])
        for original in (other, run, attempt):
            self.assertEqual(self.store.read(original['id']), original)
        for original in (other, run, attempt):
            with self.store.transaction():
                codec.delete(self.db, original['id'])
        self.assertIsNone(self.db.execute('SELECT 1 FROM stage_content WHERE sha256=?', (digest,)).fetchone())
        self.assertEqual(self.store.read(entry['id'])['saved']['scene']['items'][0]['text'], 'edited')
        self.assertFalse(self.db.execute('SELECT 1 FROM stage_content c WHERE NOT EXISTS (SELECT 1 FROM stage_content_ref r WHERE r.sha256=c.sha256)').fetchall())

    def test_corrupt_missing_content_reference_and_collision_are_not_repaired(self):
        entry = self.store.add('entry', 'w', saved={'scene': {'a': 1}})
        before = snapshot(self.db)
        for sql in ('DELETE FROM stage_content', 'DELETE FROM stage_content_ref',
                    "UPDATE stage_content SET body=x'7b7d'", "UPDATE stage_content_ref SET pointer='/wrong'"):
            with self.subTest(sql=sql):
                self.db.execute('SAVEPOINT damage')
                self.db.execute(sql)
                damaged = snapshot(self.db)
                with self.assertRaises(codec.JournalCorrupt):
                    self.store.read(entry['id'])
                self.assertEqual(snapshot(self.db), damaged)
                self.db.execute('ROLLBACK TO damage'); self.db.execute('RELEASE damage')
                self.assertEqual(snapshot(self.db), before)
        self.db.execute("UPDATE stage_content SET body=x'7b7d'"); self.db.commit()
        damaged = snapshot(self.db)
        with self.assertRaises(codec.JournalCorrupt):
            self.store.add('entry', 'w', saved={'scene': {'a': 1}})
        self.assertEqual(snapshot(self.db), damaged)

    def test_summaries_use_projection_with_image_order_missing_null_and_empty(self):
        entry = bodies()[1]; self.insert(entry)
        self.assertEqual(list(self.store.read('r1')['saved']['values']), ['z', 'a'])
        before = self.store.entry_summaries('w', 'q')[0]
        self.store.patch('r1', error='receipt only')
        self.assertEqual(self.store.entry_summaries('w', 'q')[0], before)
        self.assertEqual(list(self.store.read('r1')['saved']['values']), ['z', 'a'])
        expected = [({'name': 'first'}, 1), ({'name': 'source'}, 1), ({'name': 'input'}, 0), (None, 0), (None, None), ({'name': 'batch'}, 1)]
        saved_variants = [entry['saved'], {}, {'inputs': {'a': {'type': 'image', 'value': {'name': 'input'}}}, 'batches': {'b': []}}, {'batches': {'b': []}}, {},
                          {'values': {'v': {'type': 'image', 'value': None}}, 'inputs': {'i': {'type': 'image', 'value': {'name': 'not-selected'}}}, 'batches': {'b': [{'name': 'batch'}]}}]
        for i, (saved, (image, count)) in enumerate(zip(saved_variants, expected)):
            self.store.patch('r1', saved=saved, source={'name': 'source'} if i == 1 else None)
            sql = []; self.db.set_trace_callback(sql.append)
            with patch.object(codec, 'records', side_effect=AssertionError('summary rebuilt content')):
                head = self.store.entry_summaries('w', 'q')[0]
                self.assertEqual((head['image'], head['image_count']), (image, count))
                self.store.schedule_groups('w', 'q'); self.store.entry_states('w', 'q')
                self.store.preferred_entry('w', 'q'); self.store.scheduler_runs('w', 'q')
            self.db.set_trace_callback(None)
            self.assertTrue(all('stage_content' not in s for s in sql))
        for present, config, expected in [(False, None, False), (True, None, False), (True, {}, True), (True, False, True), (True, '', True)]:
            stage = {'parameters': config} if present else {}
            item = self.store.add('attempt', 'w', stage='s', status='submitted', snapshot_state={'multi_output': {'stages': {'s': stage}}})
            with patch.object(codec, 'records', side_effect=AssertionError('snapshot decode')):
                self.assertEqual(next(r for r in self.store.attempt_heads() if r['id'] == item['id'])['has_parameters'], expected)

    def test_batch_segments_missing_null_empty_and_progress_patch_never_decode_scene(self):
        base = dict(kind='batch_snapshot', id='batch', workspace='w', owner='owner', scene={'long': 'x' * 100000})
        for fields, expected in [({}, True), ({'segments': None}, False), ({'segments': []}, False),
                                 ({'segments': [{'owner': 'owner', 'start': 2, 'total': 3}], 'expanded': 2}, True)]:
            self.insert(dict(base, **fields))
            with patch.object(codec, 'records', side_effect=AssertionError('scene decode')):
                self.assertEqual(self.store.pending_batches('owner'), expected)
                self.store.batch_heads('w', 'owner')
                self.store.patch('batch', expanded=4)
            self.assertEqual(self.store.read('batch')['scene'], base['scene'])

    def test_optional_malformed_summary_fields_do_not_block_lossless_full_records(self):
        for source in (False, 0, '', [], {}, None):
            body = dict(id='optional', kind='entry', workspace='w', owner='o', source=source, status='waiting', scheduler='q', saved={})
            self.insert(body)
            self.assertTrue(codec.identical(self.store.read('optional'), body))
            if type(source) in (bool, int) or source == '':
                with self.assertRaises((TypeError, json.JSONDecodeError)):self.store.entry_summaries('w', 'q')
        body = dict(id='bad-parameter', kind='attempt', workspace='w', owner='o', stage='s', status='submitted',
                    snapshot_state={'multi_output': {'stages': {'s': 'incomplete old field'}}})
        self.insert(body)
        self.assertEqual(self.store.read(body['id']), body)
        with self.assertRaises(sqlite3.OperationalError):self.store.attempt_heads()
        self.store.patch(body['id'], stage='unrelated')
        self.assertFalse(self.store.attempt_heads()[0]['has_parameters'])
        self.store.patch(body['id'], stage='s')
        with self.assertRaises(sqlite3.OperationalError):self.store.attempt_heads()
        self.store.patch(body['id'], snapshot_state={'multi_output': {'stages': {'s': {'parameters': None}}}})
        self.assertFalse(self.store.attempt_heads()[0]['has_parameters'])


class MigrationTests(unittest.TestCase):
    def test_standard_json_with_incomplete_summary_fields_still_migrates_losslessly(self):
        with closing(sqlite3.connect(':memory:')) as db:
            record = dict(id='entry',kind='entry',workspace='w',owner='o',source=False,saved={'values': {'z': 'old placeholder'}},status='waiting')
            legacy(db, [record])
            self.assertEqual(layout.transform_journal(db)['records'], 1)
            self.assertTrue(codec.identical(StageStore(db).read('entry'), record))

    def test_single_copy_lossless_verified_counts_registry_and_untouched_tables(self):
        with closing(sqlite3.connect(':memory:')) as db:
            original = legacy(db)
            retained = {t: db.execute('SELECT * FROM ' + t).fetchall() for t in ('document', 'content_history')}
            report = layout.REGISTRY[0].transform(db)
            self.assertEqual((layout.REGISTRY[0].key, layout.REGISTRY[0].from_version, layout.REGISTRY[0].to_version), ('journal', 0, 1))
            self.assertEqual(report['records'], 8)
            self.assertEqual(report['positions'], [['r'+str(i), i*7+3] for i in range(8)])
            restored = codec.records(db, db.execute('SELECT id,body,content_paths,projection FROM stage_header ORDER BY position'))
            self.assertTrue(codec.identical(original, restored))
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
            self.assertEqual(db.execute('SELECT * FROM storage_layout').fetchall(), [('journal', 1)])
            self.assertNotIn('stage_journal', {r[0] for r in db.execute("SELECT name FROM sqlite_master")})
            self.assertEqual(retained, {t: db.execute('SELECT * FROM ' + t).fetchall() for t in retained})

    def test_failures_at_record_verify_and_after_drop_restore_entire_database(self):
        for phase in ('record', 'verified', 'marked'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'studio.sqlite3'
                db = sqlite3.connect(path)
                try:
                    legacy(db)
                    before = path.read_bytes(); dump = list(db.iterdump())
                    def fail(where, value):
                        if where == phase:raise RuntimeError('injected')
                    with self.assertRaisesRegex(RuntimeError, 'injected'):
                        layout.transform_journal(db, checkpoint=fail)
                    self.assertEqual(list(db.iterdump()), dump)
                    self.assertEqual(path.read_bytes(), before)
                finally:db.close()

    def test_nonstandard_duplicate_and_lossy_values_report_all_ids_and_keep_original(self):
        db = sqlite3.connect(':memory:'); self.addCleanup(db.close)
        legacy(db)
        for ident, raw in [('r0', '{"id":"r0","id":"r0"}'), ('r1', '{"id":"r1","x":NaN}'), ('r2', '{"id":"r2","x":1e400}'), ('r3', '{"id":"r3","x":"\\ud800"}'),
                           ('r4', '{"id":"r4","x":' + '[' * 1500 + '0' + ']' * 1500 + '}')]:
            db.execute('UPDATE stage_journal SET body=? WHERE id=?', (raw, ident))
        db.commit(); before = list(db.iterdump())
        with self.assertRaises(layout.MigrationError) as caught:layout.transform_journal(db)
        self.assertEqual(caught.exception.record_ids, ['r0', 'r1', 'r2', 'r3', 'r4'])
        self.assertEqual(list(db.iterdump()), before)


class OpenGuardTests(unittest.TestCase):
    def test_all_rejected_layouts_are_byte_identical_before_any_ddl_or_pause(self):
        variants = ('old', 'new_journal', 'new_local_state', 'missing_table', 'wrong_columns', 'old_table_with_marker', 'missing_keys')
        for variant in variants:
            for entrance in ('Storage', 'StageStore'):
                with self.subTest(variant=variant, entrance=entrance), tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / 'studio.sqlite3'
                    db = sqlite3.connect(path)
                    if variant == 'old':
                        legacy(db)
                        db.execute("UPDATE stage_journal SET body=json_set(body,'$.status','running') WHERE kind='run'")
                        db.commit()
                    else:
                        layout.initialize(db)
                        codec.write(db, dict(id='running',kind='run',workspace='w',owner='',status='running',frozen_scene={'original': 'scene'}), 1)
                        if variant == 'new_journal':db.execute("UPDATE storage_layout SET version=2")
                        elif variant == 'new_local_state':db.execute("INSERT INTO storage_layout VALUES ('local_state',1)")
                        elif variant == 'missing_table':db.execute('DROP TABLE stage_content_ref')
                        elif variant == 'wrong_columns':db.execute('ALTER TABLE stage_header ADD COLUMN unknown TEXT')
                        elif variant == 'missing_keys':
                            db.execute('DROP TABLE stage_content_ref')
                            db.execute('CREATE TABLE stage_content_ref(record_id TEXT,pointer TEXT,sha256 TEXT)')
                        else:db.execute('CREATE TABLE stage_journal(body TEXT)')
                        db.commit()
                    db.close(); before = hashlib.sha256(path.read_bytes()).hexdigest()
                    db = sqlite3.connect(path); sql = []; db.set_trace_callback(sql.append)
                    try:
                        with self.assertRaises(layout.LayoutError) as caught:
                            Storage(folder) if entrance == 'Storage' else StageStore(db)
                        self.assertEqual(str(caught.exception), layout.NEW_MESSAGE if variant.startswith('new_') else layout.OLD_MESSAGE)
                        self.assertFalse(any(s.lstrip().upper().startswith(('CREATE', 'UPDATE', 'INSERT', 'DELETE')) for s in sql))
                    finally:db.close()
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_empty_initialization_backup_and_initialization_failure_are_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Storage(folder)
            try:
                self.assertTrue(layout.inspect_layout(store.db))
                self.assertEqual(store.db.execute('SELECT * FROM storage_layout').fetchall(), [('journal', 1)])
                backup = store.backup()
                with closing(sqlite3.connect(backup)) as db:self.assertTrue(layout.inspect_layout(db))
            finally:store.close()
        db = sqlite3.connect(':memory:'); self.addCleanup(db.close)
        with patch.object(layout, 'mark_journal', side_effect=RuntimeError('init failure')):
            with self.assertRaises(RuntimeError):StageStore(db)
        self.assertEqual(db.execute('SELECT name FROM sqlite_master').fetchall(), [])


if __name__ == '__main__':unittest.main()
