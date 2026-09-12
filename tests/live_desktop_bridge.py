"""Real Qt transport -> browser native queue -> PNG -> desktop album, on port 8190 only.

Open the isolated fixture workflow and enable desktop control before running.
"""
import copy
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
os.environ['QT_QPA_PLATFORM']='offscreen'
sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from prompt_studio.window import Window
from prompt_studio.core import initial_state,build_prompt
from prompt_studio.snapshots import image_snapshots
from prompt_studio.pnginfo import png_metadata

APP=QApplication([])
folder=ROOT/'qa'/'desktop-bridge-live'/str(time.time_ns())
folder.mkdir(parents=True)
w=Window(folder)
w.error=lambda message: (_ for _ in ()).throw(RuntimeError(message))
notices=[]
original_notice=w.notice
def notice(message):
    notices.append(message); print(message,flush=True); original_notice(message)
w.notice=notice
def wait(predicate,seconds=25):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        APP.processEvents()
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('Timed out: '+str(notices[-5:]))
def api(route):
    with urllib.request.urlopen('http://127.0.0.1:8190'+route,timeout=5) as response: return json.load(response)
def history_ids(): return set(api('/history'))

try:
    w.state=initial_state(); a,b=w.state['items'][0],w.state['items'][2]
    a.update(prompt='1girl, red eyes, long hair'); b.update(prompt='closed eyes',excludes=['red eyes'])
    w.state['selections']={a['module']:[a['id']],b['module']:[b['id']]}
    w.state['weights']={a['id']:11}; w.state['temporary']=['desktop bridge '+str(time.time_ns())]
    w.catalog.put('album',dict(id='live-favorites',name='測試收藏'))
    w.state['settings']['recent_destination']='album:live-favorites'
    w.refresh_library(); w.refresh_builder(); w.recent.refresh_destinations()
    expected=build_prompt(w.state)
    w.comfy.connect_to('http://127.0.0.1:8190')
    wait(lambda:w.comfy.ready)
    wait(lambda:bool(w.comfy.last_signature))
    print('SYNC READY: '+expected,flush=True)
    before=history_ids(); w.run_controls.count.setValue(3); w.copy_final()
    wait(lambda:not w.comfy.run_id)
    wait(lambda:len(history_ids()-before)==3)
    submitted=history_ids()-before
    assert not any('未完成生成提交' in n or '提交未確認' in n for n in notices)
    entries=api('/history')
    for ident in submitted:
        entry=entries[ident]
        assert entry['status']['status_str']=='success',entry
        assert entry['prompt'][2]['2']['inputs']['text']==expected
    wait(lambda:any(w.catalog.get(r[0])['source']['prompt_id'] in submitted for r in w.catalog.db.execute("SELECT id FROM resources WHERE kind='recent'").fetchall()))
    old=next(w.catalog.get(r[0]) for r in w.catalog.db.execute("SELECT id FROM resources WHERE kind='recent'").fetchall() if w.catalog.get(r[0])['source']['prompt_id'] in submitted)
    # Change desktop text after generating. Collection must still keep the old snapshot.
    w.state['temporary']=['new prompt after generation']; w.refresh_builder(); w.changed()
    w.recent.record=old; w.recent.collect(); wait(lambda:not w.recent.collecting)
    album=w.catalog.rows('image',parent='live-favorites')
    assert len(album)==1,album
    saved=Path(album[0]['path']); original=Path(old['path'])
    assert saved.read_bytes()==original.read_bytes()
    snap=image_snapshots(png_metadata(saved))[0]['snapshot']
    assert snap['final_prompt']==expected and snap['state']['weights'][a['id']]==11
    assert snap['state']['items'][1]['excludes']==['red eyes']
    w.recent.record=w.catalog.get(old['id']); w.recent.collect(); wait(lambda:not w.recent.collecting)
    assert len(w.catalog.rows('image',parent='live-favorites'))==1
    before_stop=history_ids(); w.state['draft']='ps-test-slow '+str(time.time_ns()); w.refresh_builder(); w.changed()
    w.run_controls.count.setValue(5); w.copy_final()
    wait(lambda: bool(api('/queue')['queue_pending']) and bool(api('/queue')['queue_running']))
    w.comfy.interrupt(True)
    wait(lambda:not api('/queue')['queue_pending'] and not api('/queue')['queue_running'])
    stopped=[api('/history')[ident] for ident in history_ids()-before_stop]
    assert len(stopped)==1,stopped
    assert any(message[0]=='execution_interrupted' for message in stopped[0]['status']['messages']),stopped
    assert not stopped[0]['outputs'],stopped
    w.comfy.disconnect(); w.copy_final(); assert APP.clipboard().text()==w.state['draft']
    report=dict(ok=True,batch_count=3,prompt_ids=sorted(submitted),old_prompt=expected,
        saved=str(saved),byte_identical=True,duplicate_prevented=True,stop_and_clear=True,disconnected_copy=True,notices=notices)
    (ROOT/'qa'/'desktop-bridge-live-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: Qt desktop sync, 3 native queue submissions, recent preview, old PNG metadata and deduplicated album collection.',flush=True)
finally:
    w.close(); APP.processEvents()
