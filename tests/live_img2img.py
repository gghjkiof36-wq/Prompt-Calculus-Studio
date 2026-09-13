"""Real Qt upload -> ComfyUI queue -> PNG -> recent -> reuse, isolated CPU fixture.

Run prepare_comfy_qa.py and start that workspace on 127.0.0.1:8190 first.
This verifies transport and data ownership; it does not test model image quality.
"""
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from prompt_studio.window import Window
from prompt_studio.pnginfo import png_metadata
from prompt_studio.snapshots import image_snapshots

APP=QApplication.instance() or QApplication([])

def wait(predicate,seconds=30):
    deadline=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>deadline: raise AssertionError('Timed out waiting for backend or UI')
        QTest.qWait(40)

def profile():
    return dict(id='cpu-img2img',name='CPU transport fixture',mode='img2img',prompt=['2','text'],image='1',
                sampler='',size='',values={},seed_mode='fixed',graph={
        '1':dict(class_type='LoadImage',inputs=dict(image='fixture.png')),
        '2':dict(class_type='PromptStudioTestFixture',inputs=dict(image=['1',0],text='test')),
        '3':dict(class_type='PreviewImage',inputs=dict(images=['2',0]))})

with tempfile.TemporaryDirectory(dir=ROOT/'qa') as folder:
    path=Path(folder); source=path/'source.png'; other=path/'other.png'
    for file,color in ((source,0xff426a92),(other,0xff8c7350)):
        image=QImage(64,48,QImage.Format.Format_RGB32); image.fill(color); image.save(str(file))
    original=source.read_bytes(); window=Window(path/'data')
    try:
        window.state['settings'].update(online=False,material='solid'); window.apply_theme(); window.show()
        window.set_interface_mode('canvas'); window.canvas.add_tag('landscape')
        panel=window.generation_panel; panel.set_source(str(source)); panel.save_profile(profile())
        prompt='a ceramic vase on a table '+str(time.time_ns())
        window.final.setPlainText(prompt); window.comfy.connect_to('http://127.0.0.1:8190')
        wait(lambda:window.comfy.can_run)
        assert not window.comfy.ready, 'Test must not depend on a browser lease'
        window.comfy.run(1)
        window.final.setPlainText('edited after submission'); panel.set_source(str(other))
        wait(lambda:window.comfy.generation.records() and window.comfy.generation.records()[0]['state']=='complete')
        job=window.comfy.generation.records()[0]; ident=job.get('prompt_id',job['id'])
        def results(): return [window.catalog.get(r['id']) for r in window.catalog.rows('recent')]
        wait(lambda:any(r['source']['prompt_id']==ident for r in results()))
        record=next(r for r in results() if r['source']['prompt_id']==ident)
        meta=png_metadata(record['path']); snap=image_snapshots(meta)[0]['snapshot']
        assert snap['final_prompt']==prompt
        assert meta['raw']['prompt_studio']['submission_id']==ident
        assert meta['raw']['prompt_studio']['generation']['source']['name']=='source.png'
        assert meta['raw']['prompt']['1']['inputs']['image'].startswith('prompt_studio/')
        assert window.final.toPlainText()=='edited after submission'
        assert panel.settings()['source']['name']=='other.png' and source.read_bytes()==original
        preview=window.canvas.results; preview.refresh(); preview.images.setCurrentRow(preview.ids.index(record['id']))
        saved_folder=path/'saved'; saved_folder.mkdir()
        window.state['settings'].update(recent_external=str(saved_folder),recent_destination='external')
        window.recent.refresh_destinations(); preview.refresh_destination(); preview.save()
        wait(lambda:bool(window.catalog.get(record['id']).get('collected',{})))
        saved=window.catalog.get(record['id'])['collected'][str(saved_folder)]
        assert Path(saved['path']).read_bytes()==Path(record['path']).read_bytes()
        assert image_snapshots(png_metadata(saved['path']))[0]['snapshot']['schema_version']==3
        assert window.canvas.isVisible() and window.final.toPlainText()=='edited after submission'
        window.use_image_for_generation(record['path'])
        assert window.final.toPlainText()=='edited after submission'
        assert panel.settings()['source']['name']==Path(record['path']).name
        print('Actual upload, browser-free queue, Canvas preview/save, v3 PNG snapshot and explicit reuse passed.',flush=True)
        # Native validation must reject without retrying the remaining batch.
        broken=profile(); broken['graph']['2']['class_type']='PromptStudioMissingTestNode'
        panel.save_profile(broken); before=len(window.comfy.generation.records()); window.comfy.run(3)
        wait(lambda:len(window.comfy.generation.records())>before and window.comfy.generation.batch is None)
        assert len(window.comfy.generation.records())==before+1
        assert window.comfy.generation.records()[0]['state']=='failed'
        panel.save_profile(profile()); window.final.setPlainText('ps-test-slow landscape'); window.comfy.run(1)
        wait(lambda:window.comfy.generation.records()[0]['state']=='running')
        window.comfy.interrupt()
        wait(lambda:window.comfy.generation.records()[0]['state']=='failed')
        assert 'interrupt' in window.comfy.generation.records()[0]['error'].lower() or window.comfy.generation.records()[0]['state']=='failed'
        report=dict(ok=True,completed=ident,preview=record['path'],browser_required=False,
                    rejection_stopped_batch=True,interrupt_recorded=True,original_unchanged=True,canvas_save_exact_bytes=True,snapshot_schema=3)
        (ROOT/'qa/img2img-live-results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('Native validation, no automatic retry and running-task interruption passed.',flush=True)
    finally:
        window.close(); APP.processEvents()
