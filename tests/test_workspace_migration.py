from contextlib import closing
import errno
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from prompt_calculus_studio import migration_files as files, workspace_migration as migration, workspace_profile
from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.storage_layout import LayoutError, OLD_MESSAGE, inspect_layout
from migration_fixture import aliased_delivery, delivery, leave_hot_journal, realistic_delivery, source_hashes, write_json


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def success(self, source, target, **kwargs):
        report = migration.migrate(source, target, **kwargs)
        self.assertEqual(report['status'], 'done', report)
        return report

    def test_complete_inventory_wal_links_paths_and_source_preservation(self):
        source, target = delivery(self.base)
        before = source_hashes(source)
        report = self.success(source, target)
        root, data = workspace_profile.read(target)
        self.assertFalse((root.parent / 'INCOMPLETE').exists())
        self.assertEqual(source_hashes(source), before)
        self.assertTrue((root / 'nested/empty').is_dir())
        self.assertFalse((data / 'before-import-old.sqlite3').exists())
        self.assertEqual(report['counts']['databases'], 6)
        self.assertEqual(report['counts']['studio_databases'], 3)
        self.assertEqual(report['counts']['owners'], 2)
        self.assertEqual(report['counts']['work_resources_relink'], 1)
        self.assertEqual(report['counts']['hardlinks'], 2)
        for row in report['image_verification']:
            self.assertEqual(row['links'], 2)
            self.assertEqual(row['sha256'], before[row['file']])
        for row in report['databases']:
            self.assertEqual(row['expected'], row['after'])
            self.assertEqual(row['integrity_check'], 'ok')
            self.assertGreaterEqual(row['sizes']['before_vacuum'], row['sizes']['after_vacuum'])
            if row['kind'] == 'studio':
                with closing(sqlite3.connect(root.parent / row['file'])) as db:
                    self.assertTrue(inspect_layout(db))
                    self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='stage_journal'").fetchone())
        with closing(sqlite3.connect(data / 'studio.sqlite3')) as db:
            self.assertEqual(db.execute("SELECT body FROM cache WHERE key='wal'").fetchone()[0], 'committed in WAL')
            from prompt_calculus_studio.media_paths import original_path
            media = json.loads(db.execute('SELECT body FROM resources').fetchone()[0])
            self.assertEqual(original_path(data, media), data / 'originals/image.png')
            self.assertEqual(media['path'], str(source / 'data/originals/image.png'))
        with closing(sqlite3.connect(data / 'notebook.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT path FROM versions').fetchone()[0], str(root / 'first.md'))
        with closing(sqlite3.connect(data / 'work-resources/catalog.sqlite')) as db:
            self.assertEqual(db.execute('SELECT path FROM source').fetchone()[0], str(source / 'files'))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM document').fetchone()[0], 1)
        models = json.loads((data / 'md-models.json').read_text(encoding='utf-8'))
        self.assertEqual(models['models'], {str(root / 'first.md'): 'model-test'})
        self.assertEqual(json.loads((data / 'recent-files.json').read_text())[0], str(root / 'first.md'))
        self.assertGreater(report['total_seconds'], 0)
        print(json.dumps(dict(case='migration-inventory', counts=report['counts'], seconds=report['total_seconds'],
                              databases=[dict(file=r['file'], sizes=r['sizes']) for r in report['databases']])), flush=True)

    def test_realistic_sessions_skipped_but_hashed_without_root_studio(self):
        source, target = realistic_delivery(self.base)
        before = files.inventory(source)
        skipped = {name: info for name, info in before.items() if name.startswith('data/sessions/')}
        self.assertEqual(len(skipped), 13)
        self.assertEqual(sum(name.endswith('studio.sqlite3') for name in skipped), 3)
        self.assertEqual(sum(name.endswith('-journal') for name in skipped), 1)
        self.assertFalse((source / 'data/studio.sqlite3').exists())
        self.assertTrue(any(name.endswith('-wal') for name in before))
        # Files with similar names elsewhere must remain ordinary payload.
        (source / 'files/sessions/empty').mkdir(parents=True)
        (source / 'files/sessions/keep.txt').write_text('user file')
        (source / 'data/sessions-extra').mkdir()
        write_json(source / 'data/sessions-extra/keep.json', {'keep': True})
        before = files.inventory(source)
        checks = migration.check_layouts(source / 'data')
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(row['refused'] for row in checks))
        copied = []
        report = self.success(source, target, checkpoint=lambda phase, detail: copied.append(detail) if phase == 'copy' else None)
        self.assertFalse(any(name.startswith('data/sessions/') for name in copied))
        self.assertEqual(files.inventory(source), before)
        self.assertEqual(report['source_manifest'], before)
        root, data = workspace_profile.read(target)
        self.assertFalse((data / 'sessions').exists())
        self.assertFalse((data / 'studio.sqlite3').exists())
        self.assertTrue((root / 'sessions/empty').is_dir())
        self.assertEqual((root / 'sessions/keep.txt').read_text(), 'user file')
        self.assertTrue((data / 'sessions-extra/keep.json').exists())
        self.assertFalse(list(data.rglob('before-import-*.sqlite3')))
        self.assertEqual(report['counts']['skipped_sessions_files'], len(skipped))
        self.assertEqual(report['sizes']['skipped_sessions_bytes'], sum(info['size'] for info in skipped.values()))
        self.assertEqual(report['counts']['studio_databases'], 2)
        self.assertEqual(report['counts']['databases'], 5)
        self.assertEqual(report['counts']['inflight'], 0)  # Session hosts contain inflight jobs.
        self.assertTrue(all(row['expected'] == row['after'] and row['integrity_check'] == 'ok' for row in report['databases']))
        self.assertTrue(all(not row['refused'] for row in migration.check_layouts(data)))
        for name in before:
            if '/recovery/' in name or name.endswith(('.jpeg', '.log', 'workspace-ui.json', 'workspace-tree.json', 'workspace-connection.json')):
                self.assertEqual(files.digest(root.parent / name), before[name]['sha256'])
        print(json.dumps(dict(case='realistic-sessions', source_files=len(before),
            skipped_files=len(skipped), skipped_bytes=report['sizes']['skipped_sessions_bytes'],
            source_unchanged=True, root_studio=False, databases=report['counts']['databases'])), flush=True)

    def test_check_does_not_inspect_old_or_damaged_session_databases(self):
        source, _ = realistic_delivery(self.base)
        live = self.base / 'live'
        with closing(Storage(live)):
            pass
        shutil.copytree(source / 'data/sessions', live / 'sessions')
        damaged = live / 'sessions' / ('f' * 32) / 'app-services/studio.sqlite3'
        damaged.parent.mkdir(parents=True)
        damaged.write_bytes(b'not a database')
        before = files.inventory(live)
        checks = migration.check_layouts(live)
        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0]['refused'])
        self.assertEqual(files.inventory(live), before)

    def test_skipped_session_change_still_aborts_before_commit(self):
        source, target = realistic_delivery(self.base)
        preview = next((source / 'data/sessions').rglob('*.image'))
        before = files.inventory(source)
        def change(phase, detail):
            if phase == 'before_commit':
                info = preview.stat()
                raw = preview.read_bytes()
                preview.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
                os.utime(preview, ns=(info.st_atime_ns, info.st_mtime_ns))
        result = migration.migrate(source, target, checkpoint=change)
        self.assertEqual(result['reason'], files.CLOSE_OLD)
        self.assertEqual(result['counts']['skipped_sessions_files'], 13)
        self.assertFalse((target / workspace_profile.PROFILE).exists())
        self.assertFalse(list(target.glob('migrating-*')))
        changed = files.inventory(source)
        relative = preview.relative_to(source).as_posix()
        self.assertNotEqual(changed[relative]['sha256'], before[relative]['sha256'])
        self.assertEqual(changed[relative]['mtime_ns'], before[relative]['mtime_ns'])
        self.assertEqual(changed[relative]['size'], before[relative]['size'])

    def test_local_ai_typed_paths_and_registered_model_identity_survive(self):
        from prompt_calculus_studio.local_ai.registry import ModelRegistry, MODEL_PARAMETERS, validate_model
        source, target = realistic_delivery(self.base)
        folder = source / 'files/models'
        folder.mkdir()
        model = folder / 'synthetic.gguf'
        model.write_bytes(b'GGUF' + (3).to_bytes(4, 'little') + b'synthetic only')
        os.utime(model, ns=(1_700_000_000_123456700, 1_700_000_000_123456700))
        external = self.base / 'external.gguf'
        external.write_bytes(model.read_bytes())
        engine = source / 'files/llama-server.exe'
        engine.write_bytes(b'synthetic only; never executed')
        registry = ModelRegistry(source / 'data')
        record = registry.register(model)
        outside = registry.register(external)
        preset = dict(parameters={key: registry.settings[key] for key in MODEL_PARAMETERS}, system_prompt='synthetic preset')
        registry.update(model_folder=str(folder), settings={**registry.settings, 'runtime_path': str(engine)},
                        selected_model=record['id'], model_presets={record['id']: preset})
        before = files.inventory(source)
        report = self.success(source, target)
        moved = ModelRegistry(Path(report['data']))
        self.assertFalse(moved.error)
        self.assertEqual(moved.selected_model, record['id'])
        self.assertEqual(moved.model_presets, registry.model_presets)
        self.assertEqual(moved.model_folder, str(Path(report['root']) / 'models'))
        self.assertEqual(moved.settings['runtime_path'], str(Path(report['root']) / engine.name))
        self.assertEqual(moved.models[0], {**record, 'path': str(Path(report['root']) / 'models/synthetic.gguf')})
        self.assertEqual(moved.models[1], outside)
        self.assertEqual(validate_model(moved.models[0])['mtime_ns'], record['mtime_ns'])
        self.assertEqual(files.inventory(source), before)

    def test_unlisted_registry_paths_stop_without_content(self):
        source, target = realistic_delivery(self.base)
        for case in ('registry-prompt', 'registry-type', 'registry-session'):
            with self.subTest(case=case):
                secret = 'private synthetic content ' + str(source / 'files/first.md')
                path = source / 'data/local_ai/registry.json'
                value = {'version': 1, 'models': [], 'settings': {}, 'model_presets': {'synthetic': {'system_prompt': secret}}}
                pointer = '#/model_presets/synthetic/system_prompt'
                if case == 'registry-type':
                    value['model_folder'] = ['invalid']
                    pointer = ''
                elif case == 'registry-session':
                    value['model_folder'] = str(source / 'data/sessions')
                    pointer = ''
                previous = path.read_bytes() if path.exists() else None
                write_json(path, value)
                before = files.inventory(source)
                result = migration.migrate(source, target)
                self.assertEqual(result['status'], 'failed')
                self.assertIn(path.relative_to(source).as_posix() + pointer, result['locations'])
                self.assertNotIn('private synthetic content', json.dumps(result))
                self.assertFalse((target / workspace_profile.PROFILE).exists())
                self.assertFalse(list(target.glob('migrating-*')))
                self.assertEqual(files.inventory(source), before)
                if previous is None:
                    path.unlink()
                else:
                    path.write_bytes(previous)

    def test_source_alias_relocates_typed_fields_without_accessing_alias(self):
        from prompt_calculus_studio.local_ai.registry import ModelRegistry, validate_model
        source, target, alias = aliased_delivery(self.base)
        before = files.inventory(source)
        denied = migration.migrate(source, target)
        self.assertEqual(denied['status'], 'failed')
        self.assertIn('data/execution-index.sqlite3:owners/body', denied['locations'])
        self.assertFalse((target / workspace_profile.PROFILE).exists())
        self.assertFalse(list(target.glob('migrating-*')))
        self.assertEqual(files.inventory(source), before)

        def guarded(method):
            def invoke(path, *args, **kwargs):
                self.assertFalse(str(path).replace('\\', '/').casefold().startswith(str(alias).replace('\\', '/').casefold()))
                return method(path, *args, **kwargs)
            return invoke
        copied = []
        with patch.object(Path, 'open', guarded(Path.open)), patch.object(Path, 'stat', guarded(Path.stat)), patch.object(Path, 'resolve', guarded(Path.resolve)):
            report = self.success(source, target, source_aliases=[str(alias)],
                checkpoint=lambda phase, detail: copied.append(detail) if phase == 'copy' else None)
        root, data = workspace_profile.read(target)
        self.assertEqual(report['source_aliases'], [dict(path=str(alias), hits=23)])
        self.assertEqual(report['counts']['skipped_recovery_files'], 3)
        self.assertTrue(all(name not in copied and not (root.parent / name).exists() for name in report['skipped_recovery']))
        self.assertNotIn('synthetic private recovery content', json.dumps(report))
        self.assertEqual(files.inventory(source), before)  # Includes session hot journal and skipped recovery.
        self.assertFalse((data / 'sessions').exists())
        self.assertTrue(all(row['expected'] == row['after'] for row in report['databases']))
        with closing(sqlite3.connect(data / 'execution-index.sqlite3')) as db:
            owners = [json.loads(row[0]) for row in db.execute('SELECT body FROM owners')]
        for owner in owners:
            self.assertTrue(Path(owner['path']).is_relative_to(root))
            self.assertEqual(owner['receipt']['path'], owner['path'])
        self.assertEqual(owners[0]['document']['frozen_path'], str(alias / 'files/frozen.md'))
        with closing(sqlite3.connect(data / 'notebook.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT path FROM versions').fetchone()[0], str(root / 'first.md'))
        self.assertEqual(json.loads((data / 'recent-files.json').read_text())[0], str(root / 'first.md'))
        self.assertEqual(json.loads((data / 'workspace-layout.json').read_text())['panes'][0]['views'][0]['path'], str(root / 'first.md'))
        self.assertEqual(json.loads((data / 'md-models.json').read_text())['models'], {str(root / 'first.md'): 'model-test'})
        tree = json.loads((data / 'workspace-tree.json').read_text())
        self.assertEqual(tree, dict(version=1, selected=str(root / 'first.md'),
            expanded=[str(root), str(root / 'nested'), str(self.base / 'former-delivery-sibling/files'), str(self.base / 'external')],
            scroll=37, extra={'preserve': True}))
        registry = ModelRegistry(data)
        self.assertEqual(registry.models[0]['path'], str(root / 'models/synthetic.gguf'))
        self.assertEqual(registry.model_folder, str(root / 'models'))
        self.assertEqual(registry.settings['runtime_path'], str(root / 'llama-server.exe'))
        self.assertEqual(validate_model(registry.models[0])['mtime_ns'], registry.models[0]['mtime_ns'])
        # No old prefix remains in non-frozen JSON. A sibling is allowed.
        from prompt_calculus_studio.migration_data import Relocation
        mapping = Relocation(source, root.parent, [str(alias)])
        for path in data.rglob('*.json'):
            self.assertEqual(mapping.scan(json.loads(path.read_text(encoding='utf-8')), path.name), [])

    def test_alias_boundaries_slashes_case_multiple_and_tree_types(self):
        from prompt_calculus_studio.migration_data import Relocation
        stage = self.base / 'staging'
        aliases = ['C:\\Absent\\Previous', '//absent-server/share/old']
        relocation = Relocation(self.base / 'source', stage, aliases)
        self.assertEqual(relocation.path('c:/ABSENT/previous/FILES/nested/a.md'), str(stage / 'files/nested/a.md'))
        self.assertEqual(relocation.path('\\\\ABSENT-SERVER\\SHARE\\OLD\\data\\config.json'), str(stage / 'data/config.json'))
        for path in ('C:/Absent/Previous-other/files/a.md', 'C:/Absent/Previous copy/files/a.md', '//absent-server/share/older/files/a.md', 'C:/Absent/Previous/files-other/a.md'):
            self.assertEqual(relocation.path(path), path)
        self.assertFalse(relocation.old('C:/Absent/Previous-other/files/a.md'))
        self.assertFalse(relocation.old('C:/Absent/Previous copy/files/a.md'))
        self.assertTrue(relocation.old('document:c:/ABSENT/previous/files/a.md'))
        value = dict(version=1, expanded=['c:/absent/previous/files'], selected=None, scroll=9, extra=True)
        self.assertEqual(relocation.config('data/workspace-tree.json', value), {**value, 'expanded': [str(stage / 'files')]})
        value['selected'] = 'C:/Absent/Previous/data/something'
        self.assertEqual(relocation.config('data/workspace-tree.json', value)['selected'], value['selected'])
        for invalid in ({**value, 'selected': []}, {**value, 'expanded': [1]}, {**value, 'version': 2}):
            with self.assertRaises(files.MigrationFailure):
                relocation.config('data/workspace-tree.json', invalid)
        for invalid in ('relative/path', 'C:drive-relative', ''):
            with self.assertRaises(files.MigrationFailure):
                Relocation(self.base / 'source', stage, [invalid])

    def test_recovery_old_locations_are_skipped_at_both_levels_only(self):
        from prompt_calculus_studio.draft_cache import DraftCache
        source, target = realistic_delivery(self.base)
        alias = self.base / 'former'
        skipped, kept = [], []
        for directory in (source / 'data', next((source / 'data/documents').iterdir())):
            cache = DraftCache(directory)
            for key, value in (
                ('document:' + str(alias / 'files/first.md').casefold(), {'raw': 'synthetic-private'}),
                ('path-only', {'path': str(source / 'files/first.md'), 'raw': 'synthetic-private'}),
                ('data-only', {'data': str(alias / 'data/sessions' / ('a' * 32)), 'raw': 'synthetic-private'}),
                ('nested-only', {'state': {'draft': 'synthetic-private ' + str(alias / 'files/first.md')}}),
            ):
                cache.write(key, value)
                skipped.append((directory / 'recovery' / cache.name(key)).relative_to(source).as_posix())
            cache.write('external-only', {'path': str(self.base / 'former-sibling/files/a.md')})
            kept.append((directory / 'recovery' / cache.name('external-only')).relative_to(source).as_posix())
        # Same-named directories elsewhere do not receive the recovery exception.
        unknown = source / 'data/recovery-extra/snapshot.json'
        write_json(unknown, {'path': str(alias / 'files/first.md')})
        denied = migration.migrate(source, target, source_aliases=[str(alias)])
        self.assertEqual(denied['status'], 'failed')
        self.assertEqual(denied['locations'], ['data/recovery-extra/snapshot.json#/path'])
        unknown.unlink()
        before = files.inventory(source)
        copied = []
        report = self.success(source, target, source_aliases=[str(alias)],
            checkpoint=lambda phase, detail: copied.append(detail) if phase == 'copy' else None)
        self.assertEqual(report['counts']['skipped_recovery_files'], 8)
        self.assertEqual(report['skipped_recovery'], sorted(skipped))
        self.assertEqual(report['source_aliases'], [dict(path=str(alias), hits=6)])
        self.assertNotIn('synthetic-private', json.dumps(report))
        for name in skipped:
            self.assertNotIn(name, copied)
            self.assertFalse((Path(report['root']).parent / name).exists())
        for name in kept:
            self.assertEqual(files.digest(Path(report['root']).parent / name), before[name]['sha256'])
        self.assertEqual(files.inventory(source), before)

    def test_alias_unknown_json_and_sql_locations_still_stop(self):
        source, target, alias = aliased_delivery(self.base)
        settings = source / 'data/settings.json'
        write_json(settings, {'secret': 'private synthetic ' + str(alias / 'data/unlisted')})
        before = files.inventory(source)
        result = migration.migrate(source, target, source_aliases=[str(alias)])
        self.assertEqual(result['locations'], ['data/settings.json#/secret'])
        self.assertNotIn('private synthetic', json.dumps(result))
        self.assertEqual(files.inventory(source), before)
        write_json(settings, {'keep': True})
        with closing(sqlite3.connect(source / 'data/notebook.sqlite3')) as db, db:
            db.execute('UPDATE settings SET value=?', (json.dumps({'secret': str(alias / 'files/a.md')}),))
        before = files.inventory(source)
        result = migration.migrate(source, target, source_aliases=[str(alias)])
        self.assertEqual(result['locations'], ['data/notebook.sqlite3:settings/value#/secret'])
        self.assertEqual(files.inventory(source), before)
        self.assertFalse((target / workspace_profile.PROFILE).exists())
        self.assertFalse(list(target.glob('migrating-*')))

    def test_four_failure_phases_cleanup_and_retry(self):
        for phase in ('copy', 'transform', 'verify', 'before_commit'):
            with self.subTest(phase=phase):
                base = self.base / phase
                source, target = delivery(base)
                before = source_hashes(source)
                def fail(current, detail):
                    if current == phase:
                        raise RuntimeError('synthetic failure')
                report = migration.migrate(source, target, checkpoint=fail)
                self.assertEqual(report['status'], 'failed')
                self.assertFalse((target / workspace_profile.PROFILE).exists())
                self.assertFalse(list(target.glob('migrating-*')))
                self.assertEqual(source_hashes(source), before)
                self.success(source, target)
                self.assertEqual(source_hashes(source), before)

    def test_cleanup_orphan_unmarked_referenced_and_broken_profile(self):
        target = self.base / 'new'
        target.mkdir()
        orphan = target / 'migrating-orphan'
        orphan.mkdir(); (orphan / 'INCOMPLETE').write_text('{}')
        unmarked = target / 'migrating-keep'
        unmarked.mkdir(); (unmarked / 'keep.txt').write_text('keep')
        self.assertEqual(migration.cleanup(target)['removed'], [orphan.name])
        self.assertTrue(unmarked.exists())
        kept = target / 'migrating-committed'
        (kept / 'files').mkdir(parents=True); (kept / 'data').mkdir()
        (kept / 'INCOMPLETE').write_text('{}')
        workspace_profile.remember(target, kept / 'files', kept / 'data')
        self.assertEqual(migration.cleanup(target)['finalized'], [kept.name])
        self.assertTrue(kept.exists()); self.assertFalse((kept / 'INCOMPLETE').exists())
        orphan.mkdir(); (orphan / 'INCOMPLETE').write_text('{}')
        (target / workspace_profile.PROFILE).write_text('corrupt')
        with self.assertRaises(ValueError):
            migration.cleanup(target)
        self.assertTrue(orphan.exists())

    def test_commit_marker_failure_and_post_replace_exception_keep_data(self):
        for phase in ('committed', 'remember'):
            with self.subTest(phase=phase):
                source, target = delivery(self.base / phase)
                original = workspace_profile.remember
                def remember(*args):
                    original(*args)
                    raise OSError('post replace')
                def stop(current, detail):
                    if current == 'committed':
                        raise RuntimeError('post commit')
                if phase == 'remember':
                    with patch.object(workspace_profile, 'remember', side_effect=remember):
                        report = self.success(source, target)
                else:
                    report = self.success(source, target, checkpoint=stop)
                root, data = workspace_profile.read(target)
                self.assertTrue(report['cleanup_pending'])
                self.assertTrue((root.parent / 'INCOMPLETE').exists())
                migration.cleanup(target)
                self.assertTrue(data.exists())
                self.assertFalse((root.parent / 'INCOMPLETE').exists())

    def test_inflight_default_refusal_and_explicit_import(self):
        source, target = delivery(self.base, inflight=True)
        before = source_hashes(source)
        report = migration.migrate(source, target)
        self.assertEqual(report['reason'], migration.INFLIGHT_MESSAGE)
        self.assertEqual(report['counts']['inflight'], 3)
        self.assertEqual({r['id'] for r in report['inflight']}, {'r2', 'r3', 'g1'})
        self.assertFalse((target / workspace_profile.PROFILE).exists())
        accepted = self.success(source, target, allow_inflight=True)
        self.assertEqual(accepted['counts']['inflight'], 3)
        from prompt_calculus_studio.stage_store import StageStore
        store = Storage(Path(accepted['data']))
        try:
            journal = StageStore(store.db)
            self.assertEqual(journal.read('r0')['status'], 'paused')
            self.assertEqual(journal.read('r2')['prompt_id'], 'preserved-prompt-id')
        finally:
            store.close()
        self.assertEqual(source_hashes(source), before)

    def test_unknown_paths_types_sqlite_and_columns_fail_without_content(self):
        for case in ('json', 'json-key', 'sqlite', 'extension', 'column', 'sql-path'):
            with self.subTest(case=case):
                source, target = delivery(self.base / case, wal=False)
                if case == 'json':
                    write_json(source / 'data/extra.json', {'secret': {'nested': 'private prompt ' + str(source / 'files/first.md')}})
                elif case == 'json-key':
                    write_json(source / 'data/extra.json', {str(source / 'files/first.md'): 'private prompt'})
                elif case == 'sqlite':
                    with closing(sqlite3.connect(source / 'data/unknown.bin')) as db:
                        db.execute('CREATE TABLE secret(value TEXT)'); db.commit()
                elif case == 'extension':
                    (source / 'data/unknown.blob').write_bytes(b'private prompt')
                else:
                    with closing(sqlite3.connect(source / 'data/notebook.sqlite3')) as db, db:
                        if case == 'column':
                            db.execute('ALTER TABLE settings ADD COLUMN secret TEXT')
                        else:
                            db.execute('UPDATE settings SET value=?', ('private prompt ' + str(source / 'data'),))
                before = source_hashes(source)
                result = migration.migrate(source, target)
                self.assertEqual(result['status'], 'failed')
                self.assertTrue(result['locations'], result)
                self.assertNotIn('private prompt', json.dumps(result))
                self.assertEqual(source_hashes(source), before)
                self.assertFalse((target / workspace_profile.PROFILE).exists())
                self.assertFalse(list(target.glob('migrating-*')))

    def test_live_lock_stale_lock_and_source_change(self):
        source, target = delivery(self.base)
        lock = source / 'data/workspace.lock'
        lock.write_text(f'{os.getpid()}\npython\n{socket.gethostname()}\n', encoding='utf-8')
        before = source_hashes(source)
        result = migration.migrate(source, target)
        self.assertEqual(result['reason'], files.CLOSE_OLD)
        self.assertEqual(source_hashes(source), before)
        lock.write_text(f'999999999\npython\n{socket.gethostname()}\n', encoding='utf-8')
        def changed(phase, detail):
            if phase == 'before_commit':
                (source / 'files/first.md').write_text('externally modified')
        result = migration.migrate(source, target, checkpoint=changed)
        self.assertEqual(result['reason'], files.CLOSE_OLD)
        self.assertFalse(list(target.glob('migrating-*')))
        self.success(source, target)

    def test_space_fallback_and_enospc(self):
        source, target = delivery(self.base)
        before = source_hashes(source)
        with patch.object(files, 'require_space', side_effect=files.MigrationFailure('目的地空間不足。')):
            self.assertEqual(migration.migrate(source, target)['status'], 'failed')
        self.assertFalse(list(target.glob('migrating-*')))
        with patch.object(files, 'copy_file', side_effect=OSError(errno.ENOSPC, 'full')):
            result = migration.migrate(source, target)
        self.assertEqual(result['reason'], '目的地空間不足，搬遷未提交。')
        self.assertFalse(list(target.glob('migrating-*')))
        with patch.object(os, 'link', side_effect=OSError(errno.EXDEV, 'other volume')), patch.object(files, 'require_space', wraps=files.require_space) as space:
            report = self.success(source, target)
            self.assertEqual(space.call_count, 3)
        self.assertEqual(report['counts']['hardlinks'], 0)
        self.assertEqual(source_hashes(source), before)

    def test_destination_lock_prevents_migration_and_cleanup(self):
        source, target = delivery(self.base)
        target.mkdir()
        orphan = target / 'migrating-in-use'
        orphan.mkdir(); (orphan / 'INCOMPLETE').write_text('{}')
        with files.destination_lock(target):
            self.assertEqual(migration.migrate(source, target)['status'], 'failed')
            with self.assertRaises(files.MigrationFailure):
                migration.cleanup(target)
            self.assertTrue(orphan.exists())
        self.success(source, target)
        self.assertFalse(orphan.exists())

    def test_profile_conflict_and_nested_destination(self):
        source, target = delivery(self.base)
        self.assertEqual(migration.migrate(source, source)['status'], 'failed')
        self.assertEqual(migration.migrate(source, source / 'new')['status'], 'failed')
        self.assertEqual(migration.migrate(source, self.base)['status'], 'failed')
        def conflict(phase, detail):
            if phase == 'before_commit':
                workspace_profile.remember(target, source / 'files', source / 'data')
        result = migration.migrate(source, target, checkpoint=conflict)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(workspace_profile.read(target), (source / 'files', source / 'data'))
        self.assertFalse(list(target.glob('migrating-*')))

    def test_readonly_check_and_old_open_guard_then_extension_identity(self):
        from test_comfy_integration import Service
        source, target = delivery(self.base)
        before = source_hashes(source)
        checks = migration.check_layouts(source / 'data')
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(row['refused'] and row['reason'] == OLD_MESSAGE for row in checks))
        self.assertEqual(source_hashes(source), before)
        # Guard a closed old DB: opening the WAL source itself is forbidden.
        old = next((source / 'data/documents').iterdir())
        with self.assertRaises(LayoutError):
            Storage(old)
        self.assertEqual(source_hashes(source), before)
        service = object.__new__(Service)
        old_library = service.read_library(old / 'studio.sqlite3')
        report = self.success(source, target)
        new_path = Path(report['data']) / 'documents' / old.name / 'studio.sqlite3'
        new_library = service.read_library(new_path)
        self.assertEqual(old_library['library_revision'], new_library['library_revision'])
        self.assertNotEqual(old_library['library_id'], new_library['library_id'])
        with closing(Storage(new_path.parent)) as store:
            self.assertTrue(inspect_layout(store.db))
        checks = migration.check_layouts(Path(report['data']))
        self.assertTrue(all(not row['refused'] and row['storage_layout']['journal'] == 1 for row in checks))
        self.assertEqual(source_hashes(source), before)

    def test_cli_both_entrypoints_no_qt_and_mutually_exclusive_flags(self):
        source, target, alias = aliased_delivery(self.base)
        second_alias = self.base / 'another-former-delivery'
        with closing(sqlite3.connect(source / 'data/notebook.sqlite3')) as db, db:
            db.execute('UPDATE versions SET path=?', (str(second_alias / 'files/first.md').replace('\\', '/').upper(),))
        before = files.inventory(source)
        alias_flags = ['--migrate-source-alias', str(alias), '--migrate-source-alias', str(second_alias)]
        project = Path(__file__).resolve().parents[1]
        script = """import runpy,sys
class NoQt:
 def find_spec(self,fullname,*args):
  if fullname.startswith(('PySide','PyQt')): raise AssertionError('Qt imported')
sys.meta_path.insert(0,NoQt())
sys.argv=sys.argv[1:]
runpy.run_path(sys.argv[0],run_name='__main__')
"""
        for entry in ('run.py', 'run_workspace.py'):
            missing = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script, str(project / entry), *alias_flags], cwd=project, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(missing.returncode, 2, missing.stderr)
            self.assertEqual(len(missing.stdout.splitlines()), 1)
            self.assertEqual(json.loads(missing.stdout)['status'], 'failed')
            for extra in (['--check'], ['--root', str(source)], ['--data', str(source)], ['--choose-workspace'], ['--smoke-test'], ['--v08-alpha'], ['--v0.8.10-pre-alpha.2']):
                result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script, str(project / entry), '--migrate-from', str(source), '--migrate-into', str(target), *alias_flags, *extra], cwd=project, capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(len(result.stdout.splitlines()), 1)
                self.assertEqual(json.loads(result.stdout)['status'], 'failed')
            destination = target / entry.replace('.', '-')
            result = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', script, str(project / entry), '--migrate-from', str(source), '--migrate-into', str(destination), *alias_flags], cwd=project, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(len(result.stdout.splitlines()), 1)
            report = json.loads(result.stdout)
            self.assertEqual(report['status'], 'done')
            self.assertEqual(report['counts']['skipped_sessions_files'], 13)
            self.assertGreater(report['sizes']['skipped_sessions_bytes'], 0)
            self.assertEqual(report['source_aliases'], [dict(path=str(alias), hits=22), dict(path=str(second_alias), hits=1)])
            self.assertEqual(report['counts']['skipped_recovery_files'], 3)
            self.assertFalse((Path(report['data']) / 'sessions').exists())
        self.assertEqual(files.inventory(source), before)

    def test_hot_rollback_journal_recovers_only_the_copy(self):
        source, target = delivery(self.base, wal=False)
        path = source / 'data/studio.sqlite3'
        leave_hot_journal(path)
        self.assertGreater(Path(str(path) + '-journal').stat().st_size, 512)
        before = source_hashes(source)
        report = self.success(source, target)
        with closing(sqlite3.connect(Path(report['data']) / 'studio.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT body FROM cache').fetchone()[0], 'before crash')
        self.assertEqual(source_hashes(source), before)

    def test_each_inflight_state_including_input_without_body_kind(self):
        source, target = delivery(self.base, wal=False)
        path = source / 'data/studio.sqlite3'
        for state in ('submitted', 'collecting', 'preparing', 'unconfirmed', 'cancel_requested'):
            with self.subTest(state=state), closing(sqlite3.connect(path)) as db:
                value = json.loads(db.execute("SELECT body FROM stage_journal WHERE id='r2'").fetchone()[0])
                value['status'] = state
                db.execute("UPDATE stage_journal SET body=? WHERE id='r2'", (json.dumps(value),)); db.commit()
                self.assertEqual(migration.migrate(source, target)['reason'], migration.INFLIGHT_MESSAGE)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE stage_journal SET body=json_set(body,'$.status','complete') WHERE id='r2'")
            db.execute("UPDATE stage_journal SET body=json_remove(json_set(body,'$.ended',0),'$.kind') WHERE id='r3'")
        report = migration.migrate(source, target)
        self.assertEqual(report['reason'], migration.INFLIGHT_MESSAGE)
        self.assertEqual([r['id'] for r in report['inflight']], ['r3'])

    def test_json_sql_paths_and_component_boundaries(self):
        source, target = delivery(self.base, wal=False)
        with closing(sqlite3.connect(source / 'data/notebook.sqlite3')) as db, db:
            db.execute('UPDATE settings SET value=?', (json.dumps({'nested': [str(source / 'files/first.md')]}),))
        result = migration.migrate(source, target)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('data/notebook.sqlite3:settings/value#/nested/0', result['locations'])
        with closing(sqlite3.connect(source / 'data/notebook.sqlite3')) as db, db:
            db.execute('UPDATE settings SET value=?', (json.dumps({'nested': [str(source) + '-sibling/file.md']}),))
        self.success(source, target)

    def test_junctions_rejected_and_cleanup_never_follows_them(self):
        source, target = delivery(self.base)
        outside = self.base / 'outside'
        outside.mkdir(); (outside / 'INCOMPLETE').write_text('{}'); (outside / 'keep.txt').write_text('keep')
        def junction(link, actual):
            if os.name == 'nt':
                import _winapi
                _winapi.CreateJunction(str(actual), str(link))
            else:
                link.symlink_to(actual, target_is_directory=True)
        linked = source / 'files/linked'
        junction(linked, outside)
        try:
            self.assertEqual(migration.migrate(source, target)['status'], 'failed')
        finally:
            linked.rmdir() if os.name == 'nt' else linked.unlink()
        target.mkdir(exist_ok=True)
        linked = target / 'migrating-linked'
        junction(linked, outside)
        try:
            migration.cleanup(target)
            self.assertTrue((outside / 'keep.txt').exists())
            self.assertTrue((outside / 'INCOMPLETE').exists())
            self.assertTrue(linked.exists())
        finally:
            linked.rmdir() if os.name == 'nt' else linked.unlink()

    def test_startup_cleanup_and_check_do_not_open_a_window(self):
        import run_workspace
        source, target = delivery(self.base)
        self.success(source, target)
        root, data = workspace_profile.read(target)
        marker = root.parent / 'INCOMPLETE'
        marker.write_text('{}')
        orphan = target / 'migrating-orphan'
        orphan.mkdir(); (orphan / 'INCOMPLETE').write_text('{}')
        isolated_env = {k: v for k, v in os.environ.items() if k != 'PROMPT_STUDIO_DATA'}
        with patch.object(run_workspace, '__file__', str(target / 'run_workspace.py')), patch.dict(os.environ, isolated_env, clear=True):
            with patch.object(sys, 'argv', ['run_workspace.py', '--check']), patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(run_workspace.main(), 0)
                checks = json.loads(output.getvalue())['databases']
                self.assertEqual(len(checks), 3)
                self.assertTrue(all(not d['refused'] for d in checks))
            self.assertTrue(marker.exists()); self.assertTrue(orphan.exists())
            with patch.object(sys, 'argv', ['run_workspace.py']), patch.object(workspace_profile, 'locations', side_effect=RuntimeError('stop before UI')):
                with self.assertRaisesRegex(RuntimeError, 'stop before UI'):
                    run_workspace.main()
            self.assertFalse(marker.exists()); self.assertFalse(orphan.exists())

    def test_other_process_lock_and_no_source_sqlite_connections(self):
        source, target = delivery(self.base)
        target.mkdir()
        script = """import sys
from pathlib import Path
from prompt_calculus_studio.migration_files import destination_lock
with destination_lock(Path(sys.argv[1])):
 print('ready',flush=True)
 sys.stdin.readline()
"""
        process = subprocess.Popen([sys.executable, '-B', '-c', script, str(target)],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            self.assertEqual(migration.migrate(source, target)['status'], 'failed')
        finally:
            process.communicate('\n', timeout=10)
        self.assertEqual(process.returncode, 0)
        connect = sqlite3.connect
        opened = []
        def guard(path, *args, **kwargs):
            self.assertNotIn(str(source).casefold(), str(path).casefold())
            opened.append(str(path))
            return connect(path, *args, **kwargs)
        before = source_hashes(source)
        with patch.object(sqlite3, 'connect', side_effect=guard):
            checks = migration.check_layouts(source / 'data')
            self.assertEqual(len(checks), 3)
            self.success(source, target)
        self.assertTrue(opened)
        self.assertEqual(source_hashes(source), before)

    def test_profile_replace_failure_removes_only_current_staging(self):
        source, target = delivery(self.base)
        target.mkdir()
        keep = target / 'migrating-unmarked'
        keep.mkdir(); (keep / 'keep.txt').write_text('keep')
        before = source_hashes(source)
        with patch.object(os, 'replace', side_effect=OSError('replace failure')):
            result = migration.migrate(source, target)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(list(target.glob('migrating-*')), [keep])
        self.assertFalse(list(target.glob('.pcs-workspace-*')))
        self.assertFalse((target / workspace_profile.PROFILE).exists())
        self.assertEqual(source_hashes(source), before)
        self.success(source, target)


if __name__ == '__main__':
    unittest.main()
