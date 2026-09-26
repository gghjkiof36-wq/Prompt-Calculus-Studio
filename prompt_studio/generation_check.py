"""Packaged diagnostic against the isolated CPU fixture on port 8190 only."""
import json
import time
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage,QFontDatabase
from PySide6.QtWidgets import QApplication
from .pnginfo import png_metadata
from .snapshots import image_snapshots


def start(window):
    window.state['settings'].update(online=False,material='solid')
    if QApplication.platformName()=='offscreen':
        for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'): QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
        window.state['settings']['font_family']='Microsoft JhengHei'
    window.apply_theme(); window.resize(1440,900); window.enter_canvas()
    window.canvas.add_tag('landscape')
    source=window.store.directory/'fixture.png'; picture=QImage(64,48,QImage.Format.Format_RGB32)
    picture.fill(0xff526c82); picture.save(str(source))
    profile=dict(id='packaged-img2img',name='圖生圖流程測試',mode='img2img',prompt=['2','text'],image='1',
                 sampler='',size='',values={},seed_mode='fixed',graph={
        '1':dict(class_type='LoadImage',inputs=dict(image='fixture.png')),
        '2':dict(class_type='PromptStudioTestFixture',inputs=dict(image=['1',0],text='')),
        '3':dict(class_type='PreviewImage',inputs=dict(images=['2',0]))})
    window.generation_panel.set_source(str(source)); window.generation_panel.save_profile(profile)
    expected='landscape, sunset '+str(time.time_ns()); window.final.setPlainText(expected)
    window.comfy.connect_to('http://127.0.0.1:8190'); timer=QTimer(window); started=time.monotonic(); submitted=False
    def finish(result):
        timer.stop(); window.canvas.fit(); QApplication.processEvents()
        window.grab().save(str(window.store.directory/'img2img-smoke.png'))
        (window.store.directory/'img2img-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        window.close()
    def check():
        nonlocal submitted
        try:
            if time.monotonic()-started>35: raise RuntimeError('Isolated ComfyUI fixture timed out')
            if not submitted and window.comfy.can_run:
                submitted=True; window.comfy.run(1); window.final.setPlainText('next prompt stays editable')
            jobs=window.comfy.generation.records()
            if not jobs: return
            if jobs[0]['state']=='failed': raise RuntimeError(jobs[0]['error'])
            if jobs[0]['state']!='complete': return
            ident=jobs[0].get('prompt_id',jobs[0]['id'])
            for row in window.catalog.rows('recent'):
                record=window.catalog.get(row['id'])
                if record['source']['prompt_id']!=ident: continue
                meta=png_metadata(record['path']); snap=image_snapshots(meta)[0]['snapshot']
                result=dict(ok=snap['final_prompt']==expected and window.final.toPlainText()=='next prompt stays editable'
                            and window.state['generation']['source']['name']=='fixture.png' and snap['schema_version']==3
                            and window.canvas.results.record is not None,
                            schema=snap['schema_version'],canvas_preview=window.canvas.results.record is not None,
                            browser_required=window.comfy.ready,submitted_prompt=snap['final_prompt'],
                            source_unchanged=window.state['generation']['source']['name']=='fixture.png',
                            preview_received=True,job=ident,metadata=meta['raw']['prompt_studio']['generation'])
                finish(result); return
        except Exception as exc: finish(dict(ok=False,error=str(exc)))
    timer.timeout.connect(check); timer.start(100)
