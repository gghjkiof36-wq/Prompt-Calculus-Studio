"""Immutable attachments with explicit logical-reference to physical-path mapping.

Legacy image sources retain originals/generation/... (required by the shared
validator). A manifest resolves that logical name to assets/<document-id>/...
without changing a material definition or invalidating its saved cursor.
"""
import copy
import mimetypes
import os
from pathlib import PurePosixPath, PureWindowsPath
import re
import uuid

from .schema import digest, fail
from .paths import checked, read_bytes, relative_parts

MAX_ASSET_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


def references(document):
    """Yield only known content attachment fields, never arbitrary graph strings."""
    if document['kind'] == 'chat':
        return
    from ..composition import walk
    from .policy import material_roots
    payload = document['payload']
    state = payload['state'] if document['kind'] == 'workflow' else {'items': payload['items']}
    for index, item in enumerate(state.get('items', [])):
        if item.get('preview'):
            yield item['preview'], None, f'items/{index}/preview'
    for index, root in enumerate(material_roots(state)):
        for part in walk(root):
            if part.get('preview'):
                yield part['preview'], None, f'roots/{index}/{part["id"]}/preview'
    sources = [('generation/source', state.get('generation', {}).get('source'))]
    for key, image in state.get('canvas_functions', {}).get('images', {}).items():
        sources.append((f'canvas_functions/images/{key}/source', image.get('source')))
    for key, canvas in state.get('multi_output', {}).get('canvases', {}).items():
        for layer in (canvas.get('image') or {}).get('layers', []):
            if layer.get('type') == 'image':
                sources.append((f'multi_output/canvases/{key}/layers/{layer["id"]}/source', layer.get('source')))
    for location, source in sources:
        if source:
            yield source['relative'], source.get('sha256'), location


def validate_manifest(document):
    manifest = document.get('assets')
    if not isinstance(manifest, list) or len(manifest) > 10000:
        fail('invalid', '附件清單無效。')
    seen = set()
    paths = {}
    for entry in manifest:
        if not isinstance(entry, dict) or set(entry) != {'reference', 'path', 'sha256', 'size', 'media_type'}:
            fail('invalid', '附件紀錄無效。')
        relative_parts(entry['reference'])
        relative_parts(entry['path'])
        if not entry['path'].startswith('assets/') or not re.fullmatch('[0-9a-f]{64}', str(entry['sha256'])):
            fail('invalid', '附件路徑或摘要無效。')
        if type(entry['size']) is not int or not 0 <= entry['size'] <= MAX_ASSET_BYTES or not isinstance(entry['media_type'], str):
            fail('invalid', '附件大小或類型無效。')
        if entry['reference'] in seen:
            fail('invalid', '附件引用重複。')
        seen.add(entry['reference'])
        previous = paths.setdefault(entry['path'].casefold(), entry['sha256'])
        if previous != entry['sha256']:
            fail('invalid', '同一附件路徑有不同內容。')
    used = {ref for ref, _, _ in references(document)}
    if seen - used:
        fail('invalid', '附件清單含未使用內容。')


def external_dependencies(document):
    result = []
    if document['kind'] != 'workflow':
        return result
    for profile in document['payload']['state'].get('generation', {}).get('profiles', []):
        result.append(dict(kind='service-workflow', profile=profile['id'], available='unverified'))
        for node, record in profile['graph'].items():
            for field, value in record['inputs'].items():
                if isinstance(value, str) and (field.endswith('_name') or field in ('image', 'video', 'audio', 'path', 'filename')):
                    result.append(dict(kind='external-resource', profile=profile['id'], node=node,
                                       field=field, value=value, available='unverified'))
    return result


def unsupported_paths(document):
    result = []
    known = {ref for ref, _, _ in references(document)}

    def walk(value, location='payload'):
        if isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f'{location}/{i}')
        elif isinstance(value, dict):
            for key, child in value.items():
                at = f'{location}/{key}'
                if isinstance(child, str) and child and (
                        key in ('path', 'filename', 'directory', 'image') or key.endswith('_path')):
                    if (PureWindowsPath(child).is_absolute() or PurePosixPath(child).is_absolute()) and child not in known:
                        result.append(dict(code='unsupported-path', location=at))
                walk(child, at)
    walk(document['payload'])
    return result


def inspect(document, directory):
    """Return actionable issues; opening never fetches or substitutes a resource."""
    manifest = {a['reference']: a for a in document['assets']}
    issues = unsupported_paths(document)
    for reference, expected, location in references(document):
        entry = manifest.get(reference)
        try:
            relative_parts(reference)
            if entry is None:
                fail('missing-asset', '附件尚未納入文件。')
            path = checked(directory, entry['path'])
            raw, _ = read_bytes(path, MAX_ASSET_BYTES)
            actual = digest(raw)
            if actual != entry['sha256'] or len(raw) != entry['size'] or expected and expected != actual:
                fail('asset-hash', '附件摘要不同。')
        except (ValueError, OSError) as error:
            issues.append(dict(code=getattr(error, 'code', 'missing-asset'), reference=reference,
                               location=location, message=str(error)))
    return issues


def prepare(document, source_root, destination_directory):
    """Copy known attachments from an explicitly supplied root, never from a DB.

    Validation and reads finish before writing immutable, hash-named attachments.
    Failure may leave reusable immutable assets, but never replaces a document.
    """
    from .schema import validate
    candidate = copy.deepcopy(validate(document))
    issues = unsupported_paths(candidate)
    old = {a['reference']: a for a in candidate['assets']}
    manifest = {}
    content = {}
    total = 0
    for reference, expected, location in references(candidate):
        try:
            relative_parts(reference)
            physical = old.get(reference, {}).get('path', reference)
            raw, _ = read_bytes(checked(source_root, physical), MAX_ASSET_BYTES)
            hashed = digest(raw)
            if expected and expected != hashed or reference in old and old[reference]['sha256'] != hashed:
                fail('asset-hash', '來源附件摘要不同。')
            suffix = PurePosixPath(reference).suffix.lower()
            if not re.fullmatch(r'\.[a-z0-9]{1,10}', suffix):
                suffix = '.bin'
            path = f'assets/{candidate["document_id"]}/{hashed}{suffix}'
            if path not in content:
                total += len(raw)
            if total > MAX_TOTAL_BYTES:
                fail('limit', '一次可攜附件超過 256 MiB；未保存文件。')
            manifest[reference] = dict(reference=reference, path=path, sha256=hashed, size=len(raw),
                                       media_type=mimetypes.guess_type(reference)[0] or 'application/octet-stream')
            content[path] = raw
        except (ValueError, OSError) as error:
            if getattr(error, 'code', '') == 'limit':
                raise
            issues.append(dict(code=getattr(error, 'code', 'missing-asset'), location=location, message=str(error)))
    if issues:
        fail('assets', '附件尚未完整可攜；未保存文件。', issues=issues)
    candidate['assets'] = list(manifest.values())
    validate(candidate)
    from .directory_pin import pinned_target
    for relative, raw in content.items():
        with pinned_target(destination_directory, relative, create_parents=True) as path:
            temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                with temporary.open('xb') as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    existing, _ = read_bytes(checked(destination_directory, relative), MAX_ASSET_BYTES)
                    if existing != raw:
                        fail('asset-hash', '附件目的地已有不同內容，未覆蓋。')
            finally:
                temporary.unlink(missing_ok=True)
    return candidate


def resolve(document, directory, reference):
    entry = next((a for a in document['assets'] if a['reference'] == reference), None)
    if entry is None:
        fail('missing-asset', '附件未包含於文件。')
    path = checked(directory, entry['path'])
    raw, _ = read_bytes(path, MAX_ASSET_BYTES)
    if len(raw) != entry['size'] or digest(raw) != entry['sha256']:
        fail('asset-hash', '附件內容與保存時不同。')
    return path
