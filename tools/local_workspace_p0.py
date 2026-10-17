"""Reproduce the pre-file-model inventory using synthetic data only.

This is a P0 diagnostic, not a portable-document codec or a product entry point.
The required --out directory must be new. No user database is opened.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
from pathlib import Path
import struct
import sys
import uuid
import zlib
from contextlib import closing
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class NoQt:
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PySide6', 'shiboken6')):
            raise RuntimeError('P0 inventory must stay Qt-free: ' + fullname)


sys.meta_path.insert(0, NoQt())
from prompt_calculus_studio import composition as comp, core, materials
from prompt_calculus_studio import prompt_materials as pm, prompt_groups as groups
from prompt_calculus_studio import prompt_sources as sources, material_navigation as nav
from prompt_calculus_studio import multi_output as multi, workspace_scene as scenes
from prompt_calculus_studio.content_history import record
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.stage_store import StageStore
from prompt_calculus_studio.snapshots import portable_state


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def profile():
    return dict(id='profile-p0', name='P0 synthetic graph', mode='txt2img',
                prompt=['6', 'text'], multi_text=True, sampler='3', size='5', values={}, graph={
        '3': dict(class_type='KSampler', inputs=dict(positive=['6', 0], negative=['7', 0],
                    latent_image=['5', 0], seed=2, steps=1, cfg=1, denoise=1,
                    sampler_name='euler', scheduler='normal')),
        '5': dict(class_type='EmptyLatentImage', inputs=dict(width=64, height=64, batch_size=1)),
        '6': dict(class_type='CLIPTextEncode', inputs=dict(text='original positive')),
        '7': dict(class_type='CLIPTextEncode', inputs=dict(text='original negative'))})


def fixture():
    state = core.initial_state()
    state.update(items=[], modules=[], selections={}, dictionary={})
    state['workspaces'][0].update(name='P0 workflow A', fixed=[], picks={})
    state['settings'].update(online=False, comfy_enabled=False)
    category_a = materials.add_category(state, '場景分類')
    category_b = materials.add_category(state, '動作分類')
    unrelated = materials.add_category(state, 'SYNTHETIC_UNRELATED_CATEGORY')
    folder = nav.add_folder(state, '創作資料')
    nav.move(state, category_a, folder); nav.move(state, category_b, folder)

    text_root = pm.new('text', '場景文字')
    text_root['prompt'] = 'synthetic street'
    variants = [groups.new_group('晴', 'sunny street'), groups.new_group('雨', 'rainy street')]
    text_root['variants'] = dict(version=1, mode='increment', random_repeats=True,
                                start=variants[0]['id'], selected=variants[0]['id'], groups=variants)
    text_id = materials.save(state, dict(name='場景文字', module=category_a, prompt='synthetic street',
                            composition=text_root, preview='assets/p0-swatch.png', pinned=True))
    external = materials.save(state, dict(name='外部分類原件', module=category_b, prompt='walking',
                                         aliases=['步行'], notes='synthetic original'))

    def placement(ident):
        original = materials.item(state, ident)
        root = comp.clone_node(comp.prototype(original))
        root.update(source_id=ident, source_module=original['module'],
                    source_revision=materials.fingerprint(original))
        return root

    combination = pm.new('combination', '場景組合')
    combination['combination']['modules'] = [placement(text_id), placement(external)]
    combined_id = materials.save(state, dict(name='場景組合', module=category_a, prompt='',
                                            composition=combination, material_type='combination'))
    flow = pm.new('flow', '素材流程')
    first = flow['sequence']['groups'][0]
    first.update(name='第一輪', repeat=3, modules=[placement(combined_id)], text='inactive prose')
    second = groups.new_group('第二輪', 'second round')
    flow['sequence'].update(mode='increment', groups=[first, second])
    flow_id = materials.save(state, dict(name='素材流程', module=category_a, prompt='',
                                        composition=flow, material_type='flow'))
    unrelated_item = materials.save(state, dict(name='SYNTHETIC_UNUSED_ITEM', module=unrelated,
                                               prompt='SYNTHETIC_UNUSED_BODY'))
    state.update(selection_view='canvas', uses={'use-a': placement(combined_id)},
                 prompt_layout='paragraphs')
    state = multi.migrate(state)
    data = state['multi_output']; canvas_a = next(iter(data['canvases'])); output_a = data['current_output']
    state['generation'] = dict(mode='txt2img', profiles=[profile()], chosen={'txt2img': 'profile-p0'}, source=None)
    data['canvases']['canvas-b'] = multi.new_canvas('內部畫布 B', (700, 0))
    data['outputs']['output-b'] = multi.new_output('第二輸出')
    state['uses']['use-b'] = placement(text_id)
    multi.assign(state, 'use-b', 'canvas-b')
    multi.connect(state, 'canvas-b', 'output-b', 'text')
    multi.connect(state, 'output-b', multi.GENERATOR, 'execution')
    multi.bind(state, 'profile-p0', output_a, '6', 'text')
    multi.bind(state, 'profile-p0', 'output-b', '7', 'text')
    state['output_order'] = ['use-b', 'use-a']
    state['draft'] = ''; state['draft_base'] = 'P0 empty manual base'
    multi.select_output(state, 'output-b')
    state['draft'] = 'P0 second manual'; multi.select_output(state, output_a)
    data['outputs']['output-b']['list_draft'] = dict(draft='', draft_base='P0 list base')
    data['outputs']['output-b']['list_state'] = dict(selections={category_b: [external]},
                                                     weights={external: 15}, instances={})
    state['canvas_view'] = [1.25, 120, 80]
    state = prepare_state(state, multi=True)
    reader = sources.add(state, root=sources.library_root(state, flow_id))
    reservation = sources.build(state, [reader], count=1)
    sources.reserve(state, reservation, through=1)
    before = copy.deepcopy(state)
    materials.item(state, text_id)['notes'] = 'P0 saved content history'
    record(state, before, state, label='P0 synthetic material edit')
    workspace_a = state['workspace']; scenes.capture(state)
    workspace_b = scenes.create(state, 'P0 workflow B', duplicate=True)
    scenes.switch(state, workspace_b)
    state['draft'] = 'SYNTHETIC_WORKFLOW_B_DRAFT'
    state['uses']['use-b']['prompt'] = 'SYNTHETIC_WORKFLOW_B_BODY'
    state['canvas_view'] = [.75, -300, 50]
    multi.capture_current(state); scenes.capture(state); scenes.switch(state, workspace_a)
    core.validate_state(state)
    return state, dict(category_a=category_a, category_b=category_b, unrelated=unrelated,
                       text=text_id, external=external, combination=combined_id, flow=flow_id,
                       unused_item=unrelated_item, canvas_a=canvas_a, canvas_b='canvas-b',
                       output_a=output_a, output_b='output-b', workspace_a=workspace_a,
                       workspace_b=workspace_b, reader=reader)


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x40\x80\xc0')) + chunk(b'IEND', b''))


def run(out):
    out = out.resolve()
    if out.exists(): raise ValueError('Output must be a new directory; existing evidence is retained.')
    out.mkdir(parents=True)
    data = out/'data'; documents = out/'documents'; results = out/'output'
    for directory in (data, documents, results): directory.mkdir()
    (documents/'assets').mkdir(); (documents/'assets/p0-swatch.png').write_bytes(png())
    (documents/'場景筆記.md').write_bytes(b'\xef\xbb\xbf' + '# 場景筆記\r\n\r\nP0 合成資料。\r\n'.encode('utf-8'))
    (documents/'空白.md').write_bytes(b'')
    counter = itertools.count(1)
    with patch('uuid.uuid4', side_effect=lambda: uuid.UUID(int=next(counter))), patch('time.time', return_value=1802649600.0):
        state, ids = fixture()
    before = copy.deepcopy(state)
    prepared = prepare_state(state, multi=True)
    assert state == before
    assert prepared['draft'] == ''
    assert prepared['multi_output']['outputs']['output-b']['list_draft']['draft'] == ''
    assert pm.position(sources.source(prepared, ids['reader'])['root'])['repeat'] == 1
    assert len(prepared['multi_output']['canvases']) == 2
    assert len(prepared['multi_output']['bindings']) == 2

    # Single catalog ownership, nested detached provenance, and the old removal contract.
    assert len({item['id'] for item in prepared['items']}) == len(prepared['items'])
    assert all(item['module'] in {m['id'] for m in prepared['modules']} for item in prepared['items'])
    root = materials.item(prepared, ids['combination'])['composition']
    external_part = next(n for n in comp.walk(root) if n.get('source_id') == ids['external'])
    rendered = comp.render(root)
    deleted = copy.deepcopy(prepared); materials.remove(deleted, ids['external'])
    assert comp.render(materials.item(deleted, ids['combination'])['composition']) == rendered
    assert deleted['uses'] == prepared['uses']
    removed = copy.deepcopy(prepared); destination = materials.remove_category(removed, ids['category_a'])
    assert {i['id'] for i in removed['items']} == {i['id'] for i in prepared['items']}
    assert all(i['module'] == destination for i in removed['items'] if i['id'] in
               {old['id'] for old in prepared['items'] if old['module'] == ids['category_a']})
    assert removed['uses'] == prepared['uses'] and removed['draft'] == prepared['draft']
    core.validate_state(removed)

    # This explicitly demonstrates the unsafe alias; it is not a multi-view implementation.
    shared = copy.deepcopy(prepared); fake_view_a = shared
    scenes.switch(shared, ids['workspace_b'])
    assert fake_view_a['workspace'] == ids['workspace_b']
    assert fake_view_a['draft'] == 'SYNTHETIC_WORKFLOW_B_DRAFT'
    # Current generation projection is deliberately lossy and cannot be the file serializer.
    projected = portable_state(prepared)
    assert 'content_history' not in projected
    assert 'pinned' not in next(i for i in projected['items'] if i['id'] == ids['text'])

    with closing(core.Storage(data)) as store:
        store.save(prepared)
        journal = StageStore(store.db)
        for workspace in (ids['workspace_a'], ids['workspace_b']):
            journal.add('run', workspace, status='paused', server='http://127.0.0.1:8188',
                        synthetic_inventory_only=True, frozen_prompt='P0 frozen ' + workspace)
        saved_rows = list(store.db.execute('SELECT id,kind,workspace,owner,position,body FROM stage_journal ORDER BY position'))
        db_state = store.load()
        assert db_state == prepared
        assert store.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    with closing(core.Storage(data)) as store:
        assert store.load() == db_state
        assert saved_rows == list(store.db.execute('SELECT id,kind,workspace,owner,position,body FROM stage_journal ORDER BY position'))
    write_json(documents/'legacy-state-v7.json', db_state)
    write_json(documents/'identity-map.json', ids)
    reopened = json.loads((documents/'legacy-state-v7.json').read_text('utf-8'))
    assert reopened == db_state; core.validate_state(reopened)
    for name in ('legacy_material_cursors.json', 'legacy_stage_snapshot_088.json'):
        (documents/name).write_bytes((ROOT/'tests/fixtures'/name).read_bytes())
    report = dict(purpose='P0 baseline inventory, not P1 codec or UI acceptance', qt_import_blocked=True,
                  state_version=db_state['version'], identity_map=ids,
                  categories=len(db_state['modules']), materials=len(db_state['items']),
                  workspaces=len(db_state['workspaces']), internal_canvases=2, bindings=2,
                  material_kinds=sorted({pm.kind(i) for i in db_state['items']}),
                  original_owner_count_per_item=1, detached_cross_category_render_preserved=True,
                  detached_cross_category_source=external_part['source_module'],
                  remove_category_keeps_materials_copies_and_empty_draft=True,
                  unsafe_global_state_alias_reproduced=True,
                  snapshot_projection_loses_pinned_and_history=True,
                  saved_reader_repeat=1, sqlite_roundtrip_equal=True,
                  synthetic_journal_rows_preserved=len(saved_rows),
                  original_user_databases_opened=False,
                  fixture_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted(documents.rglob('*')) if p.is_file()})
    write_json(results/'inventory.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out)
