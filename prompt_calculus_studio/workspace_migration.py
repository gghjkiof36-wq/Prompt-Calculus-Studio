"""Headless, copy-first migration. Publication is workspace_profile.remember only."""
from contextlib import closing
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid

from . import migration_data as data
from . import migration_files as files
from . import storage_layout, workspace_profile


INFLIGHT_MESSAGE = '舊版還有執行中的工作，請先在舊版完成或取消。'


def read_profile(base):
    files.physical(base / workspace_profile.PROFILE)
    return workspace_profile.read(base)


def _cleanup_locked(base):
    profile = read_profile(base)  # A broken profile is a stop, never an orphan hint.
    removed, finalized = [], []
    for folder in sorted(base.glob('migrating-*')):
        if files.reparse(folder) or not folder.is_dir():
            continue
        marker = folder / 'INCOMPLETE'
        if not marker.is_file() or files.reparse(marker):
            continue
        referenced = profile and any(path == folder or folder in path.parents for path in profile)
        if referenced:
            marker.unlink()
            finalized.append(folder.name)
        else:
            files.remove_staging(base, folder)
            removed.append(folder.name)
    return dict(removed=removed, finalized=finalized)


def cleanup(base):
    """Startup cleanup; diagnostics never call it. Hold the same lock as migrate."""
    base = files.physical(base)
    read_profile(base)
    if not base.exists() or not any(base.glob('migrating-*/INCOMPLETE')):
        return dict(removed=[], finalized=[])
    with files.destination_lock(base):
        return _cleanup_locked(base)


def integrity(db, relative):
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise files.MigrationFailure('資料庫完整性檢查失敗。', locations=[relative])


def recover(db, relative):
    # SQLite sees only cloned files, including WAL, SHM and hot rollback journal.
    integrity(db, relative)
    db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    db.execute('PRAGMA journal_mode=DELETE')
    integrity(db, relative)


def estimate(manifest, selected):
    parts = dict(files=0, data=0, images=0, databases=0)
    for relative, kind in selected.items():
        bucket = 'databases' if kind in ('database', 'sidecar') else 'images' if kind == 'image' else 'files' if relative.startswith('files/') else 'data'
        parts[bucket] += manifest[relative]['size']
    # Retain all copies, new tables, rollback/WAL, and VACUUM's temporary file.
    # Images are budgeted as full copies even if every hard link succeeds.
    parts.update(database_peak_multiplier=6, reserve=16 * 1024 * 1024)
    parts['required'] = parts['files'] + parts['data'] + parts['images'] + parts['databases'] * parts['database_peak_multiplier'] + parts['reserve']
    return parts


def _profile_references(base, staging):
    profile = read_profile(base)
    return bool(profile and any(path == staging or staging in path.parents for path in profile))


def migrate(source, destination, *, allow_inflight=False, source_aliases=(), checkpoint=None):
    """Return a content-free receipt. checkpoint is an internal test/progress hook.

    Source SQLite connections are forbidden here. All database work, including
    the pre-conversion inventory, happens after the entire file group is cloned.
    """
    start = time.monotonic()
    report = dict(status='failed', destination=str(Path(destination).absolute()),
                  id=uuid.uuid4().hex, counts={}, databases=[], locations=[], inflight=[],
                  total_seconds=0, sizes={}, source_aliases=[], skipped_recovery=[], reason='')
    staging = None
    committed = False
    def event(phase, detail=None):
        if checkpoint:
            checkpoint(phase, detail)
    try:
        source, destination = files.physical(source), files.physical(destination)
        report['destination'] = str(destination)
        if source == destination or source in destination.parents or destination in source.parents:
            raise files.MigrationFailure('來源與目的地不能相同或互相包含。')
        if not all(files.physical(source / folder).is_dir() for folder in ('files', 'data')):
            raise files.MigrationFailure('舊交付資料夾必須有實體 files 和 data。')
        staging = destination / ('migrating-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + report['id'])
        relocation = data.Relocation(source, staging, tuple(source_aliases))
        report['source_aliases'] = relocation.aliases
        read_profile(destination)
        destination.mkdir(parents=True, exist_ok=True)
        with files.destination_lock(destination):
            report['cleanup'] = _cleanup_locked(destination)
            if (destination / workspace_profile.PROFILE).exists():
                raise files.MigrationFailure('目的地已經有 PCS-WORKSPACE.json，請使用另一個新交付資料夾。')
            files.check_source_locks(source)
            before = files.inventory(source)
            skipped = [info for relative, info in before.items() if data.session_data(relative)]
            report['source_manifest'] = before
            report['counts'] = dict(source_files=len(before), skipped_sessions_files=len(skipped))
            report['sizes']['skipped_sessions_bytes'] = sum(info['size'] for info in skipped)
            selected, databases = data.classify(source, before)
            report['skipped_recovery'] = data.skip_recovery(source, selected, relocation)
            report['counts']['skipped_recovery_files'] = len(report['skipped_recovery'])
            space = estimate(before, selected)
            space['free_before'] = files.require_space(destination, space['required'])
            report['sizes'].update(space)
            report['counts'].update(copied=0, hardlinks=0,
                                    databases=len(databases), studio_databases=sum(k == 'studio' for k in databases.values()),
                                    inflight=0, work_resources_relink=0, owners=0)
            staging.mkdir()
            # Marker precedes all materialized data. A crash here leaves at most
            # an empty unmarked directory, which cleanup intentionally preserves.
            try:
                with (staging / 'INCOMPLETE').open('x', encoding='utf-8') as marker:
                    json.dump(dict(source=str(source), id=report['id']), marker, ensure_ascii=False)
                    marker.flush()
                    os.fsync(marker.fileno())
                for folder in ('files', 'data'):
                    (staging / folder).mkdir()
                # Preserve empty subdirectories too, without walking root logs,
                # program binaries, historical packages or per-launch sessions.
                for folder in ('files', 'data'):
                    for parent, directories, _ in os.walk(source / folder):
                        directories[:] = sorted(name for name in directories
                            if not data.excluded((Path(parent) / name).relative_to(source)))
                        for name in directories:
                            directory = files.physical(Path(parent) / name)
                            (staging / directory.relative_to(source)).mkdir(parents=True, exist_ok=True)
                remaining = space['required']
                for relative, kind in selected.items():
                    method = files.copy_file(source / relative, staging / relative,
                                             hardlink=kind == 'image', reserve=max(0, remaining - before[relative]['size']))
                    report['counts']['hardlinks' if method == 'hardlink' else 'copied'] += 1
                    remaining -= before[relative]['size']
                    if files.digest(staging / relative) != before[relative]['sha256']:
                        raise files.MigrationFailure(files.CLOSE_OLD)
                    event('copy', relative)
                files.unchanged(source, before)
                expected_files = data.configs(staging, selected, relocation)
                plans = []
                # Audit every copied DB before transforming any of them.
                for relative, kind in databases.items():
                    path = staging / relative
                    with closing(sqlite3.connect(path)) as db:
                        recover(db, relative)
                        schema, inflight, relink = data.audit_database(db, kind, relative, relocation)
                        original = data.logical_inventory(db, kind, schema)
                        expected = data.logical_inventory(db, kind, schema, relocation=relocation)
                    receipt = dict(file=relative, kind=kind, before=original, expected=expected,
                                   sizes=dict(source=before[relative]['size'], recovered=path.stat().st_size))
                    report['databases'].append(receipt)
                    report['inflight'].extend(inflight)
                    report['counts']['work_resources_relink'] += relink
                    plans.append((relative, kind, schema, receipt))
                report['counts']['inflight'] = len(report['inflight'])
                if report['inflight'] and not allow_inflight:
                    raise files.MigrationFailure(INFLIGHT_MESSAGE, inflight=report['inflight'])
                for relative, kind, schema, receipt in plans:
                    path = staging / relative
                    tick = time.monotonic()
                    with closing(sqlite3.connect(path)) as db:
                        receipt['sizes']['before_transform'] = path.stat().st_size
                        if kind == 'studio':
                            receipt['transforms'] = []
                            # pending_steps includes the complete virtual chain,
                            # including event_apply 0 -> 1 -> 2 after journal 0.
                            for step in storage_layout.pending_steps(db):
                                step.inspect(db)
                                converted = step.transform(db, checkpoint=lambda phase, detail: event('transform', dict(file=relative, phase=phase)))
                                receipt['transforms'].append(dict(key=step.key, version=step.to_version, inventory=converted))
                            storage_layout.inspect_layout(db)
                            receipt['layout'] = dict(db.execute('SELECT key,version FROM storage_layout'))
                        data.rewrite_database(db, kind, schema, relocation)
                        receipt['sizes']['after_transform'] = path.stat().st_size
                        event('verify', relative)
                        actual = data.logical_inventory(db, kind, schema, new_journal=kind == 'studio')
                        if actual != receipt['expected']:
                            raise files.MigrationFailure('搬遷後的邏輯清點不符。', locations=[relative])
                        if kind == 'execution-index':
                            report['counts']['owners'] += data.verify_owners(db, staging)
                        integrity(db, relative)
                        receipt['sizes']['before_vacuum'] = path.stat().st_size
                        db.execute('VACUUM')
                        integrity(db, relative)
                        if data.logical_inventory(db, kind, schema, new_journal=kind == 'studio') != actual:
                            raise files.MigrationFailure('整理資料庫後的邏輯清點不符。', locations=[relative])
                        receipt.update(after=actual, integrity_check='ok')
                    receipt['sizes']['after_vacuum'] = path.stat().st_size
                    receipt['seconds'] = time.monotonic() - tick
                for relative, kind in selected.items():
                    if kind in ('database', 'sidecar'):
                        continue
                    expected = expected_files.get(relative, before[relative]['sha256'])
                    if files.digest(staging / relative) != expected:
                        raise files.MigrationFailure('搬遷後的檔案內容不符。', locations=[relative])
                report['image_verification'] = [dict(file=r, sha256=files.digest(staging / r),
                    links=(staging / r).stat().st_nlink) for r, k in selected.items() if k == 'image']
                event('before_commit', staging)
                files.check_source_locks(source)
                files.unchanged(source, before, hashes=True)
                if (destination / workspace_profile.PROFILE).exists():
                    raise files.MigrationFailure('目的地設定檔已出現，搬遷未提交。')
                workspace_profile.remember(destination, staging / 'files', staging / 'data')
                committed = True
                report.update(status='done', root=str(staging / 'files'), data=str(staging / 'data'),
                              reason='搬遷完成。', source_unchanged=True)
                try:
                    event('committed', staging)
                    (staging / 'INCOMPLETE').unlink()
                except Exception:
                    report['cleanup_pending'] = True
            except BaseException:
                # remember may have published before a post-replace exception.
                # Read the profile under the lock before deciding to discard.
                if not committed and _profile_references(destination, staging):
                    committed = True
                    report.update(status='done', root=str(staging / 'files'), data=str(staging / 'data'),
                                  reason='搬遷已提交，暫存標記將於下次啟動清除。', cleanup_pending=True)
                if not committed:
                    if (staging / 'INCOMPLETE').is_file():
                        files.remove_staging(destination, staging)
                    else:
                        staging.rmdir()  # Only our freshly created empty directory.
                    files.unchanged(source, before)
                    raise
            report['sizes']['destination_bytes'] = sum(p.stat().st_size for p in staging.rglob('*') if p.is_file())
    except Exception as exc:
        report['status'] = 'done' if committed else 'failed'
        if committed:
            report.update(reason='搬遷已提交，暫存標記將於下次啟動清除。', cleanup_pending=True)
        elif isinstance(exc, files.MigrationFailure):
            report.update(reason=str(exc), locations=exc.locations)
            if exc.inflight:
                report['inflight'] = exc.inflight
        elif isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            report['reason'] = '目的地空間不足，搬遷未提交。'
        elif isinstance(exc, storage_layout.LayoutError):
            report['reason'] = str(exc)
        else:
            # SQLite/JSON exceptions can include source content. Report only
            # type and known structural locations, never a prompt or SQL value.
            report.update(reason='搬遷未完成，請檢查資料與目的地後重試。', error_type=type(exc).__name__)
    finally:
        report['total_seconds'] = time.monotonic() - start
    return report


def check_layouts(directory):
    """Read file snapshots, then inspect read-only connections. Never touch source SHM.

    Even SQLite mode=ro may write a source WAL's SHM. Restore byte copies in a
    disposable directory first, then open those snapshots with mode=ro.
    """
    directory = files.physical(directory)
    reports = []
    if not directory.is_dir():
        return reports
    paths = []
    for parent, directories, names in os.walk(directory):
        # Session hosts are never reopened on the next launch. Prune traversal,
        # not merely the refused result, so no session DB/sidecar is inspected.
        directories[:] = sorted(name for name in directories
            if not data.session_data(Path('data') / (Path(parent) / name).relative_to(directory)))
        paths.extend(Path(parent) / name for name in names if name.casefold() == 'studio.sqlite3')
    for path in sorted(paths):
        files.physical(path)
        result = dict(file=str(path), storage_layout={}, refused=False, reason='', inspection='readonly-copy')
        try:
            with tempfile.TemporaryDirectory(prefix='pcs-layout-check-') as scratch:
                target = Path(scratch) / path.name
                group = [path] + [Path(str(path) + suffix) for suffix in files.SIDECARS if Path(str(path) + suffix).exists()]
                before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns, files.digest(p)) for p in group}
                for item in group:
                    files.copy_file(item, target.parent / item.name)
                with closing(sqlite3.connect(target)) as db:
                    recover(db, path.name)
                with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)) as db:
                    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'storage_layout' in names:
                        result['storage_layout'] = dict(db.execute('SELECT key,version FROM storage_layout'))
                    else:
                        result['storage_layout'] = {'journal': 0}
                    try:
                        storage_layout.inspect_layout(db)
                    except storage_layout.LayoutError as exc:
                        result.update(refused=True, reason=str(exc))
                if before != {p.name: (p.stat().st_size, p.stat().st_mtime_ns, files.digest(p)) for p in group}:
                    raise files.MigrationFailure(files.CLOSE_OLD)
        except Exception as exc:
            result.update(refused=True, reason=str(exc) if isinstance(exc, files.MigrationFailure) else '資料庫無法唯讀檢查。')
        reports.append(result)
    return reports


def cli(argv, base):
    """Migration arguments are parsed before the UI or any historical route."""
    import argparse
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise files.MigrationFailure('搬遷旗標無效，不能與開啟資料或診斷旗標混用。')
    parser = Parser(add_help=False, allow_abbrev=False)
    parser.add_argument('--migrate-from', type=Path, required=True)
    parser.add_argument('--migrate-into', type=Path, default=base)
    parser.add_argument('--migrate-allow-inflight', action='store_true')
    parser.add_argument('--migrate-source-alias', action='append', default=[])
    try:
        args = parser.parse_args(argv)
    except files.MigrationFailure as exc:
        report = dict(status='failed', destination=str(base), counts={}, sizes={}, total_seconds=0, reason=str(exc))
        code = 2
    else:
        report = migrate(args.migrate_from, args.migrate_into, allow_inflight=args.migrate_allow_inflight,
                         source_aliases=args.migrate_source_alias)
        code = 0 if report['status'] == 'done' else 1
    print(json.dumps(report, ensure_ascii=False, separators=(',', ':')))
    return code
