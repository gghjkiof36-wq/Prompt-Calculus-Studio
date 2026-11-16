from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from migration_fixture import delivery, source_hashes
from prompt_calculus_studio import storage_layout as layout, workspace_migration as migration, workspace_profile
from prompt_calculus_studio.core import Storage


class CandidateMigrationTests(unittest.TestCase):
    def migrate(self, journal, events):
        with tempfile.TemporaryDirectory() as area:
            source,target=delivery(Path(area),wal=False)
            for path in source.rglob('studio.sqlite3'):
                with closing(sqlite3.connect(path)) as db:
                    if journal:layout.transform_journal(db)
                    if events:layout.transform_events(db)
                    expected=[('journal',1)] if not journal else []
                    expected += [('event_apply',1)] if not events else []
                    expected += [('event_apply',2)]
                    self.assertEqual([(s.key,s.to_version) for s in layout.pending_steps(db)],expected)
            before=source_hashes(source)
            report=migration.migrate(source,target)
            self.assertEqual(report['status'],'done',report)
            self.assertEqual(source_hashes(source),before)
            _,data=workspace_profile.read(target)
            for row in report['databases']:
                if row['kind']=='studio':
                    self.assertEqual([(r['key'],r['version']) for r in row['transforms']],expected)
                    self.assertEqual(row['layout']['event_apply'],2)
            for _ in range(2):
                storage=Storage(data)
                self.assertEqual(layout.verify_candidates(storage.db),dict(candidates=0))
                self.assertEqual(layout.pending_steps(storage.db),[])
                storage.close()
            self.assertEqual(source_hashes(source),before)

    def test_journal_zero_events_zero_to_one_to_two(self):self.migrate(0,0)
    def test_journal_one_events_zero_to_one_to_two(self):self.migrate(1,0)
    def test_event_apply_one_to_two(self):self.migrate(1,1)

    def test_shape_guard_never_repairs_a_missing_candidate_table_or_index(self):
        for statement in ('DROP TABLE channel_result_candidates','DROP TABLE channel_result_retention','DROP INDEX channel_candidate_order',
                'DROP INDEX channel_candidate_operation','DROP INDEX channel_candidate_asset','DROP INDEX channel_candidate_deriving',
                "DROP INDEX channel_candidate_deriving; CREATE INDEX channel_candidate_deriving ON channel_result_candidates(operation) WHERE derived_pending=0",
                "DROP INDEX channel_candidate_order; CREATE UNIQUE INDEX channel_candidate_order ON channel_result_candidates(manifest,source,ordinal) WHERE state='ready'"):
            with self.subTest(statement=statement),tempfile.TemporaryDirectory() as area:
                storage=Storage(area);storage.close();path=Path(area)/'studio.sqlite3'
                with closing(sqlite3.connect(path)) as db,db:db.executescript(statement)
                before=path.read_bytes()
                with self.assertRaises(layout.LayoutError):Storage(area)
                self.assertEqual(path.read_bytes(),before)

    def test_failure_rolls_back_v2_and_preserves_v1(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        layout.transform_journal(db);layout.transform_events(db)
        before=list(db.iterdump())
        with self.assertRaises(OSError):
            layout.transform_candidates(db,checkpoint=lambda *_:(_ for _ in ()).throw(OSError('power loss')))
        self.assertEqual(list(db.iterdump()),before)
        layout.transform_candidates(db);self.assertTrue(layout.inspect_layout(db))


if __name__=='__main__':unittest.main()
