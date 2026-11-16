"""S5B copies the pre-channel journal layout; the original stays byte-identical."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from migration_fixture import delivery,source_hashes
from prompt_calculus_studio import storage_layout as layout,workspace_migration as migration,workspace_profile
from prompt_calculus_studio.comfy_event_inbox import EventInbox
from prompt_calculus_studio.core import Storage


class EventMigrationTests(unittest.TestCase):
    def test_copy_then_upgrade_journal_one_and_sidecar_reopen_twice(self):
        with tempfile.TemporaryDirectory() as area:
            source,target=delivery(Path(area),wal=False)
            for path in source.rglob('studio.sqlite3'):
                with closing(sqlite3.connect(path)) as db:layout.transform_journal(db)
            inbox=EventInbox(source/'data');inbox.close()
            before=source_hashes(source)
            with self.assertRaises(layout.LayoutError):Storage(source/'data')
            self.assertEqual(source_hashes(source),before)
            report=migration.migrate(source,target)
            self.assertEqual(report['status'],'done',report)
            self.assertEqual(source_hashes(source),before)
            _,data=workspace_profile.read(target)
            for _ in range(2):
                storage=Storage(data)
                self.assertEqual(dict(storage.db.execute('SELECT key,version FROM storage_layout')),dict(journal=1,event_apply=layout.SUPPORTED['event_apply']))
                self.assertEqual(storage.db.execute('SELECT COUNT(*) FROM channel_applied_events').fetchone()[0],0)
                storage.close()
                inbox=EventInbox(data);inbox.close()
            self.assertEqual(source_hashes(source),before)

    def test_normal_open_rejects_incomplete_event_table_without_ddl(self):
        with tempfile.TemporaryDirectory() as area:
            storage=Storage(area);storage.close()
            with closing(sqlite3.connect(Path(area)/'studio.sqlite3')) as db,db:db.execute('DROP TABLE channel_applied_events')
            before=(Path(area)/'studio.sqlite3').read_bytes()
            with self.assertRaises(layout.LayoutError):Storage(area)
            self.assertEqual((Path(area)/'studio.sqlite3').read_bytes(),before)


if __name__=='__main__':unittest.main()
