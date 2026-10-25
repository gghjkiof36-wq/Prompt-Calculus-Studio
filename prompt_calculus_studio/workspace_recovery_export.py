"""Restore a disk recovery record as an independent, exclusively created file."""
import base64
import os
from pathlib import Path
import re

from .document_projection import project_document
from .local_documents import assets, identity, io, paths, schema
from .markdown_source import MAX_BYTES, SourceLedger
from .state_loading import prepare_state
from .workspace_file_io import (available_targets, create_markdown, named_path,
                                PublishedMarkdownError, TargetNameCollision,
                                validate_markdown_location)


class RecoveryPublishedError(ValueError):
    """Do not repeat export: this destination was published or needs inspection."""
    def __init__(self, message, published_path, recovery_paths=(), *, expected_sha=''):
        super().__init__(message)
        self.published_path = Path(published_path)
        self.recovery_paths = tuple(recovery_paths)
        self.expected_sha = expected_sha


def _remember(recovery, original, target, expected_sha, value):
    from .workspace_recovery import key, state_digest
    recovery.published_receipts[(key(original), key(target))] = (expected_sha, state_digest(value))


def verify_recovered(recovery, original, target):
    from .workspace_recovery import key, state_digest
    target = Path(target)
    receipt = recovery.published_receipts.get((key(original), key(target)))
    if receipt is None:
        raise ValueError('沒有這份新檔的恢復回執；恢復副本已保留。')
    expected_sha, record_sha = receipt
    value = recovery.cache.read(key(original))
    if not isinstance(value, dict) or state_digest(value) != record_sha:
        raise ValueError('恢復副本已變更；未清除目前的恢復稿。')
    limit = MAX_BYTES if target.suffix.casefold() == '.md' else schema.MAX_BYTES
    actual = paths.read_bytes(paths.checked(target.parent, target.name), limit)[0]
    if schema.digest(actual) != expected_sha:
        raise ValueError('新檔已被其他內容替換或修改；恢復副本已保留。')
    return expected_sha


def _record(recovery, original):
    from .workspace_recovery import key
    value = recovery.cache.read(key(original))
    if not isinstance(value, dict):
        raise ValueError('恢復副本不存在或無法讀取；未建立新檔。')
    schema.check_json(value)
    stored = value.get('path')
    if not isinstance(stored, str) or not Path(stored).is_absolute() or key(stored) != key(original):
        raise ValueError('恢復副本的原位置不符；副本已保留。')
    expected = {'.md': 'md', **{suffix: kind for kind, suffix in schema.SUFFIXES.items()}}
    if value.get('kind') != expected.get(original.suffix.casefold()):
        raise ValueError('恢復副本的文件種類不符；副本已保留。')
    if not isinstance(value.get('sha'), str) or not re.fullmatch('[0-9a-f]{64}', value['sha']):
        raise ValueError('恢復副本的原件摘要無效；副本已保留。')
    return value


def _markdown(value):
    encoded = value.get('raw')
    if not isinstance(encoded, str) or len(encoded) > ((MAX_BYTES+2)//3)*4:
        raise ValueError('Markdown 恢復內容無效或超過 8 MiB；副本已保留。')
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_BYTES:
            raise ValueError('Markdown 超過 8 MiB。')
        SourceLedger(raw)  # Validate UTF-8 while retaining the original bytes.
    except (ValueError, UnicodeError) as error:
        raise ValueError('Markdown 恢復內容無法讀取；副本已保留。') from error
    return raw


def _native(recovery, value):
    if not all(isinstance(value.get(name), dict) for name in
               ('state', 'baseline', 'runtime_document', 'opening_settings')):
        raise ValueError('恢復副本缺少完整文件內容；副本已保留。')
    if 'identity_maps' not in value or (value['identity_maps'] is not None
                                      and not isinstance(value['identity_maps'], dict)):
        raise ValueError('恢復副本的身分映射無效；副本已保留。')
    runtime = schema.validate(value['runtime_document'])
    document = schema.validate(value.get('document', runtime))
    if runtime['kind'] != value['kind'] or document['kind'] != value['kind']:
        raise ValueError('恢復副本內的文件種類不符；副本已保留。')
    state = prepare_state(value['state'], multi=True)
    projected = project_document(document, runtime, state, value['opening_settings'], remap=False)
    candidate = identity.duplicate(projected).document
    # Editable IDs are deliberately remapped once. Old executions and their
    # journals remain owned by the original runtime; opening this file is fresh.
    source = value.get('data')
    if not isinstance(source, str) or not Path(source).is_absolute():
        raise ValueError('恢復副本的附件位置無效；副本已保留。')
    source = Path(source)
    if list(assets.references(candidate)):
        root = paths.root_path(recovery.workspace.runtimes.directory)
        if source.parent != root:
            raise ValueError('恢復附件不在此工作區的本機資料中；副本已保留。')
        source = paths.root_path(paths.checked(root, source.name))
    return candidate, source


def _relocated_markdown(recovery, raw, original, target):
    if target.parent == original.parent:
        return raw
    root = recovery.workspace.locations.root_for(original)
    if root is not None and Path(root).is_dir() and target.is_relative_to(root):
        from .markdown_relocation import copy_edits
        return copy_edits(raw, original, target, Path(root))[0]
    validate_markdown_location(raw.decode('utf-8-sig'), original, target)
    return raw


def recover_as(recovery, original, name, directory):
    from .workspace_recovery import key
    original = Path(original)
    if not original.is_absolute():
        raise ValueError('請使用原文件的完整位置。')
    original = Path(os.path.abspath(original))
    value = _record(recovery, original)  # Never use the process-local memory cache.
    parent = recovery.workspace.locations.directory(directory)
    requested = named_path(parent, name, original.suffix)
    if key(requested) == key(original):
        raise ValueError('請使用不同名稱或位置恢復為新檔；不會重建或覆寫原件。')
    markdown = value['kind'] == 'md'
    if markdown:
        raw = _markdown(value)
    else:
        candidate, source = _native(recovery, value)
        candidate = assets.prepare(candidate, source, parent)
    for target in available_targets(requested):
        # A numbered candidate can coincide with the former source too.
        if key(target) == key(original):
            continue
        if markdown:
            content = _relocated_markdown(recovery, raw, original, target)
            expected_sha = schema.digest(content)
            try:
                create_markdown(target, content)
            except TargetNameCollision:
                continue
            except PublishedMarkdownError as error:
                _remember(recovery, original, target, expected_sha, value)
                raise RecoveryPublishedError(str(error), target, expected_sha=expected_sha) from error
            _remember(recovery, original, target, expected_sha, value)
            try:
                actual = paths.read_bytes(paths.checked(target.parent, target.name), MAX_BYTES)[0]
                if actual != content:
                    raise ValueError('新檔內容在寫入後已變更。')
            except (OSError, ValueError) as error:
                raise RecoveryPublishedError('新檔已寫入，結果需要確認：'+str(error), target,
                                             expected_sha=expected_sha) from error
        else:
            expected_sha = schema.digest(schema.encode(candidate))
            result = io.create(parent, target.name, candidate)
            if result.status != 'saved':
                if result.name_collision:
                    continue
                message = result.error or '恢復尚未完成；副本已保留。'
                if result.published or target.exists():
                    _remember(recovery, original, target, expected_sha, value)
                    raise RecoveryPublishedError(message, target, result.recovery_paths, expected_sha=expected_sha)
                raise ValueError(message)
            _remember(recovery, original, target, expected_sha, value)
        return target  # The caller clears recovery only after successfully opening it.
