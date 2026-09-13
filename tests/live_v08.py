"""Real ComfyUI transport acceptance, using CPU image/text fixture nodes."""
import copy,json,os,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen'); os.environ['PROMPT_STUDIO_V08']='1'
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QImage
from prompt_studio.window import Window
from prompt_studio import multi_output as model
from prompt_studio.composition_image import document,layer,render_image
from prompt_studio.snapshots import image_snapshots,restore_snapshot,validate_snapshot
from prompt_studio.pnginfo import png_metadata
APP=QApplication([])

def wait(predicate,seconds=30):
    deadline=time.monotonic()+seconds
    while not predicate():
        if time.monotonic()>deadline: raise AssertionError('Timed out: '+window.comfy.message+' / '+window.comfy.generation.message)
        QTest.qWait(30)

folder=ROOT/'qa'/('v08-live-'+uuid.uuid4().hex[:8]); window=Window(folder)
try:
    window.state['settings'].update(online=False,material='solid'); window.show(); window.set_interface_mode('canvas'); QTest.qWait(20)
    canvas=window.canvas; cid=next(iter(canvas.containers)); output=canvas.data()['current_output']
    positive='positive original '+uuid.uuid4().hex[:8]; expected_text='('+positive+':1.0)'
    canvas.add_tag(positive,canvas.containers[cid].pos()+QPointF(30,70))
    negative_canvas=canvas.add_canvas(QPointF(0,-1300)); negative=canvas.add_output(QPointF(900,-1300)); canvas.commit(lambda s:model.connect(s,negative_canvas,negative,'text'))
    third=canvas.add_canvas(QPointF(-1500,-1300)); canvas.add_tag('unbound spare',canvas.containers[third].pos()+QPointF(30,80))
    graph={'1':dict(class_type='LoadImage',inputs=dict(image='fixture.png')),
           '2':dict(class_type='PromptStudioV08Text',inputs=dict(text='workflow positive')),
           '3':dict(class_type='PromptStudioV08Text',inputs=dict(text='workflow negative')),
           '4':dict(class_type='PromptStudioV08Image',inputs=dict(image=['1',0],positive=['2',0],negative=['3',0])),
           '5':dict(class_type='PreviewImage',inputs=dict(images=['4',0]))}
    profile=dict(id='v08-live',name='v0.8 CPU transport',mode='img2img',prompt=None,multi_text=True,image='1',sampler='',size='',values={},graph=graph)
    window.generation_panel.save_profile(profile)
    canvas.commit(lambda s:model.bind(s,profile['id'],output,'2','text')); canvas.commit(lambda s:model.bind(s,profile['id'],negative,'3','text'))
    canvas.commit(lambda s:model.connect(s,negative,model.GENERATOR,'execution'))
    doc=document(96,64); shape=layer('rect',8,8,48,40); shape['fill']='#ff0000'; doc['layers'].append(shape)
    canvas.commit(lambda s:s['multi_output']['canvases'][cid].update(image=doc)); canvas.commit(lambda s:model.connect(s,cid,output,'image'))
    expected=render_image(doc,window.store.directory)
    window.comfy.connect_to('http://127.0.0.1:8191'); wait(lambda:window.comfy.can_run)
    assert not window.comfy.ready,'Must not require a browser lease'
    window.comfy.run(1)
    window.final.setPlainText('edited after submission')
    changed=copy.deepcopy(doc); changed['layers'][0]['fill']='#0000ff'; canvas.commit(lambda s:s['multi_output']['canvases'][cid].update(image=changed))
    image_line=next(c for c in canvas.data()['connections'] if c['kind']=='image'); canvas.commit(lambda s:model.disconnect(s,image_line['id']))
    wait(lambda:window.comfy.generation.records() and window.comfy.generation.records()[0]['state']=='complete')
    job=window.comfy.generation.records()[0]; prompt_id=job.get('prompt_id',job['id'])
    def records(): return [window.catalog.get(r['id']) for r in window.catalog.rows('recent')]
    wait(lambda:any(r.get('source',{}).get('prompt_id')==prompt_id for r in records()))
    record=next(r for r in records() if r.get('source',{}).get('prompt_id')==prompt_id)
    metadata=png_metadata(record['path']); envelope=metadata['raw']['prompt_studio']; bindings=image_snapshots(metadata); assert bindings
    snapshot=bindings[0]['snapshot']; validate_snapshot(snapshot)
    assert envelope['texts'][0]['text']==expected_text and envelope['texts'][1]['text']==''
    assert len(envelope['texts'])==2 and envelope['problems']==[]
    assert metadata['raw']['prompt']['2']['inputs']['text']==expected_text
    assert metadata['raw']['prompt']['3']['inputs']['text']==''
    assert metadata['raw']['prompt']['1']['inputs']['image'].startswith('prompt_studio/')
    actual=QImage(record['path']); assert actual.size()==expected.size()
    for x,y in ((0,0),(10,10),(30,30),(70,50)): assert actual.pixelColor(x,y).rgb()==expected.pixelColor(x,y).rgb()
    restored=restore_snapshot(window.state,snapshot); assert restored['multi_output']['canvases'][cid]['image']==doc
    assert len(restored['multi_output']['canvases'])==3
    result=dict(passed=True,backend='ComfyUI 0.21.1 CPU fixture',dual_text=True,empty_negative=True,unbound_spare=True,composition_upload=True,
        snapshot_frozen=True,metadata_restore=True,output_pixels_verified=True,quality_assessed=False,prompt_id=prompt_id,result_path=record['path'],data=str(folder))
    (ROOT/'qa/v08-live-results.json').write_text(json.dumps(result,indent=2),encoding='utf-8'); print(json.dumps(result,indent=2))
finally: window.close(); APP.processEvents()
