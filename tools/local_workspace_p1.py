"""Build inspectable P1 documents and recovery evidence from synthetic data only."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import local_workspace_p0 as p0  # also installs the explicit Qt import blocker
from prompt_calculus_studio import core, prompt_materials, prompt_sources
from prompt_calculus_studio.local_documents import assets, category, workflow, schema, io, index, identity, windows


def run(out):
    out = out.absolute()
    if out.exists():
        raise ValueError('Use a new output directory; earlier evidence is retained.')
    out.mkdir(parents=True)
    source = out / 'synthetic-source'
    first = out / 'first-root'
    moved = out / 'moved-root'
    source.mkdir()
    first.mkdir()
    (source / 'assets').mkdir()
    raw = p0.png()
    (source / 'assets/p0-swatch.png').write_bytes(raw)
    image = source / 'originals/generation/p1-swatch.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(raw)
    state, ids = p0.fixture()
    state['settings']['model_root'] = 'SYNTHETIC_PRIVATE_ROOT'
    state['generation']['source'] = dict(relative='originals/generation/p1-swatch.png',
        sha256=hashlib.sha256(raw).hexdigest(), width=1, height=1, name='p1-swatch.png')
    before = copy.deepcopy(state)
    p0.write_json(source / 'state.json', state)
    documents = {
        '場景.pcs-category': category.from_state(state, ids['category_a']),
        '街景.pcs-workflow': workflow.from_state(state, ids['workspace_a']),
    }
    for name, document in documents.items():
        document = assets.prepare(document, source, first)
        result = io.create(first, name, document)
        assert result.status == 'saved', result
        documents[name] = document
    (first / '筆記.md').write_bytes(b'\xef\xbb\xbf' + '# 合成筆記\r\n'.encode('utf-8'))
    (first / '空白.md').write_bytes(b'')
    snapshot = index.rebuild(first)
    assert not snapshot['errors']
    cache = index.write_cache(first, snapshot)
    cache.unlink()
    assert first.resolve().is_relative_to(out.resolve()) and moved.parent.resolve() == out.resolve()
    first.rename(moved)
    assert not first.exists()
    with patch.object(core, 'Storage', side_effect=AssertionError('No original DB allowed')), \
            patch.object(assets, 'prepare', side_effect=AssertionError('No access to original asset root')):
        for name, document in documents.items():
            loaded = io.load(moved, name)
            assert loaded.document == document and not loaded.issues
            for reference, _, _ in assets.references(document):
                assert assets.resolve(document, moved, reference).read_bytes() == raw
        rebuilt = index.rebuild(moved)
    assert len(rebuilt['entries']) == 4 and not rebuilt['errors']
    assert state == before
    saved_state = workflow.to_state(documents['街景.pcs-workflow'])
    reader = prompt_sources.source(saved_state, ids['reader'])['root']
    assert prompt_materials.position(reader)['repeat'] == 1
    payload = schema.encode(documents['街景.pcs-workflow'])
    for sentinel in (b'SYNTHETIC_UNUSED', b'SYNTHETIC_WORKFLOW_B', b'SYNTHETIC_PRIVATE_ROOT'):
        assert sentinel not in payload

    # A real native replace-time competitor, outside the immutable final fixture root.
    recovery_root = out / 'recovery-case'
    recovery_root.mkdir()
    original = assets.prepare(documents['場景.pcs-category'], moved, recovery_root)
    saved = io.create(recovery_root, '競態.pcs-category', original)
    assert saved.status == 'saved', saved
    candidate = copy.deepcopy(saved.document)
    candidate['payload']['items'][0]['notes'] = 'P1 candidate preserved'
    external = copy.deepcopy(saved.document)
    external['payload']['items'][0]['notes'] = 'P1 external edit preserved'
    external_raw = schema.encode(external)
    staged = recovery_root / 'external.tmp'
    staged.write_bytes(external_raw)
    def race(phase):
        if phase == 'before-replace':
            windows.replace(recovery_root / '競態.pcs-category', staged, recovery_root / 'competitor.previous')
    with patch.object(io, '_checkpoint', side_effect=race):
        result = io.save(recovery_root, '競態.pcs-category', candidate, saved.receipt)
    assert result.status == 'conflict' and result.dirty, result
    recovered = {Path(p).suffix: Path(p) for p in result.recovery_paths}
    assert recovered['.external'].read_bytes() == external_raw
    assert schema.decode(recovered['.candidate'].read_bytes())['payload']['items'][0]['notes'] == 'P1 candidate preserved'
    duplicate = identity.duplicate(documents['街景.pcs-workflow'])
    assert duplicate.document['document_id'] != documents['街景.pcs-workflow']['document_id']
    report = dict(
        purpose='P1 Qt-free document core; no UI, live task, migration or release acceptance',
        qt_import_blocked=True, qt_modules_loaded=[k for k in sys.modules if k.startswith(('PySide6', 'shiboken6'))],
        original_user_databases_opened=False, original_state_unchanged=state == before,
        moved_root='moved-root', original_document_root_absent=not first.exists(),
        index_rebuilt_entries=len(rebuilt['entries']), unused_sentinels_excluded=True,
        reader_repeat=prompt_materials.position(reader)['repeat'],
        content_files={name: schema.digest((moved / name).read_bytes()) for name in documents},
        asset_count=sum(len(d['assets']) for d in documents.values()),
        race_status=result.status, candidate_dirty=result.dirty,
        recovery_files={role: str(path.relative_to(out)) for role, path in recovered.items()},
        recovery_hashes={role: schema.digest(path.read_bytes()) for role, path in recovered.items()},
        duplicate_document_id=duplicate.document['document_id'])
    assert not report['qt_modules_loaded']
    p0.write_json(out / 'REPORT.json', report)
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out)
