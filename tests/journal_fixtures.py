"""Synthetic journal data and complete durable snapshots (never user data)."""
import copy
import json


def snapshot(db, kind=None):
    if kind is None:
        return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1,2').fetchall()
                for table in ('stage_header', 'stage_content', 'stage_content_ref')}
    return dict(header=db.execute('SELECT * FROM stage_header WHERE kind=? ORDER BY id', (kind,)).fetchall(),
                refs=db.execute('SELECT * FROM stage_content_ref WHERE record_id IN (SELECT id FROM stage_header WHERE kind=?) ORDER BY 1,2', (kind,)).fetchall(),
                content=db.execute('SELECT * FROM stage_content WHERE sha256 IN (SELECT sha256 FROM stage_content_ref WHERE record_id IN (SELECT id FROM stage_header WHERE kind=?)) ORDER BY sha256', (kind,)).fetchall())


def bodies():
    scene = {'圖/~': {'draft': '', 'auto': None, 'false': False, 'zero': 0,
                      'float': 0.0, 'minus': -0.0, 'empty': [], 'unknown': {'é': 'e\u0301'}}}
    saved = dict(scene=scene, values={'z': dict(type='image', value={'name': 'first'}),
                                     'a': dict(type='image', value={'name': 'second'})},
                 inputs={'i': dict(type='image', value={'name': 'input'})},
                 batches={'z': [{'name': 'batch'}], 'a': []}, parameters={},
                 readers=[], source_positions={'cursor': 0}, future={'scene': scene})
    values = [
        dict(kind='run', status='complete', plan={'scopes': {'q': {}}, 'stages': {}},
             frozen_scene=scene, free_scene=scene, round_saved=[saved, None, {}, saved],
             stack=[dict(kind='loop', key='q', owner='owner', done=0, node={'key': 'q'},
                         ctx={'saved': saved, 'results': {}, 'sources': [], 'execution_group': 'g'},
                         feed={'scene': scene, 'plan': {}, 'next': 1, 'total': 3},
                         produced=[], variants={}, retry={'snapshot_state': scene, 'images': [], 'result': None}),
                    None, dict(kind='seq', ctx={'saved': saved}, produced={})],
             feed={'scene': scene, 'next': 0, 'total': 1}),
        dict(kind='entry', status='waiting', saved=saved, source=None, run='r0', scheduler='q',
             execution_group='g', parent=None, stages=['s'], label='項目'),
        dict(kind='attempt', status='submitted', position=0, stage='s', parent='r1',
             snapshot_state={'multi_output': {'stages': {'s': {'parameters': {}}}}, 'scene': scene},
             images=[], result={'texts': ['', None], 'images': []}),
        dict(kind='input', status='sent', sent=True, ended=False, workflow='w'),
        dict(kind='parameter_state', status='observed', next_seed=0, used_seed=0, signature='s'),
        dict(kind='control', status='control', scheduler='q', paused=False),
        dict(kind='batch_snapshot', status='waiting', run='r0', scheduler='q', execution_group='g',
             scene=scene, base={'draft': ''}, supply=None, total=4, expanded=1,
             segments=[dict(owner='owner', start=0, total=4, deferred=False, supply={}, results=[])]),
        dict(kind='batch_control', status='waiting', group='g', serial=2, rank=0, total=4, expanded=1,
             cancelled=False, bypassed=False, cursor_before={'x': None}, cursor_after={'x': 0}, cursor_keys=['x']),
    ]
    return [copy.deepcopy(dict(id='r' + str(i), workspace='w', owner='owner', created=1.0,
                               future={'scene': scene, 'null': None, 'empty': '', 'false': False, 'zero': 0}, **v))
            for i, v in enumerate(values)]


def legacy(db, records=None):
    db.execute('CREATE TABLE stage_journal(id TEXT PRIMARY KEY,kind TEXT,workspace TEXT,owner TEXT,position INTEGER,body TEXT NOT NULL)')
    records = bodies() if records is None else records
    for i, body in enumerate(records):
        db.execute('INSERT INTO stage_journal VALUES (?,?,?,?,?,?)',
                   (body['id'], body['kind'], body['workspace'], body['owner'], i * 7 + 3,
                    json.dumps(body, ensure_ascii=False)))
    db.execute('CREATE TABLE document(id INTEGER PRIMARY KEY,body TEXT)')
    db.execute('INSERT INTO document VALUES (1,?)', (json.dumps({'content_history': ['保留'], 'version': 7}),))
    db.execute('CREATE TABLE content_history(id TEXT,body TEXT)')
    db.execute("INSERT INTO content_history VALUES ('kept','unchanged')")
    db.commit()
    return records
