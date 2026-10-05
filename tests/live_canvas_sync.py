"""Qt -> real isolated backend -> protocol consumer, without generating images.

The consumer stands in for the browser; this verifies transport, not browser UI.
"""
import json,os,sys,tempfile,time,urllib.request,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.core import build_prompt
APP=QApplication([]); BASE='http://127.0.0.1:8190/prompt_studio/'; token=''
def api(route,data=None):
    request=urllib.request.Request(BASE+route,data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type':'application/json','X-Prompt-Studio':token})
    with urllib.request.urlopen(request,timeout=5) as response: return json.load(response)
def wait(predicate):
    until=time.monotonic()+10
    while time.monotonic()<until:
        APP.processEvents()
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('Canvas sync timed out')
token=api('config')['token']; session='canvas-sync-test-'+uuid.uuid4().hex
assert not api('desktop/status')['ready'],'Do not take an existing browser lease'
lease=api('desktop/claim',dict(session=session,target='Isolated protocol consumer'))['lease']
with tempfile.TemporaryDirectory(dir=ROOT/'qa') as folder:
    w=Window(Path(folder)); w.state['settings']['online']=False
    try:
        w.set_interface_mode('canvas'); w.canvas.add_tag('landscape, soft light')
        expected=build_prompt(w.state); w.comfy.connect_to('http://127.0.0.1:8190')
        wait(lambda:w.comfy.ready and bool(w.comfy.last_signature) and not w.comfy.replies)
        command=api('desktop/wait',dict(session=session,lease=lease))
        assert command['kind']=='sync' and command['snapshot']['schema_version']==3
        assert command['snapshot']['final_prompt']==expected
        api('desktop/ack',dict(session=session,lease=lease,id=command['id']))
        prior=w.comfy.last_signature; w.final.setPlainText('changed manual landscape')
        wait(lambda:w.comfy.last_signature!=prior and not w.comfy.replies)
        updated=api('desktop/wait',dict(session=session,lease=lease))
        assert updated['snapshot']['final_prompt']=='changed manual landscape'
        assert command['snapshot']['final_prompt']==expected
        api('desktop/ack',dict(session=session,lease=lease,id=updated['id']))
        report=dict(ok=True,schema=3,initial_and_manual_sync=True,original_snapshot_preserved=True,browser_ui_tested=False)
        (ROOT/'qa/canvas-sync-live-results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('PASS: Canvas schema 3 and subsequent manual edit reached real backend; snapshots remain independent.')
    finally:
        w.close(); APP.processEvents(); api('desktop/release',dict(session=session))
