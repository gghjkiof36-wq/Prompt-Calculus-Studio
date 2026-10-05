"""Exercise the real ComfyUI queue and PNG saver using a one-pixel CPU fixture."""
import json
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prompt_calculus_studio.pnginfo import png_metadata
from prompt_calculus_studio.snapshots import image_snapshots

BASE = 'http://127.0.0.1:8190'
QA = ROOT / 'qa' / 'comfy-integration'
token = ''


def request(route, data=None):
    headers={'Content-Type':'application/json', 'X-Prompt-Studio': token}
    r=urllib.request.Request(BASE+route, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    with urllib.request.urlopen(r,timeout=15) as response: return json.load(response)


config=request('/prompt_studio/config'); token=config['token']
library=request('/prompt_studio/library')
snapshot=request('/prompt_studio/compose', library)
workspace=library['state']['workspace']
request('/prompt_studio/config',dict(workspace=workspace,destination=str(QA/'saved')))
workflow=dict(last_node_id=3,last_link_id=2,nodes=[
    dict(id=1,type='LoadImage',pos=[40,150],size=[260,320],flags={},order=0,mode=0,
        outputs=[dict(name='IMAGE',type='IMAGE',links=[1],slot_index=0),dict(name='MASK',type='MASK',links=None)],
        properties={},widgets_values=['fixture.png','image']),
    dict(id=2,type='PromptStudioTestFixture',title='測試文字與圖片',pos=[380,150],size=[280,210],flags={},order=1,mode=0,
        inputs=[dict(name='image',type='IMAGE',link=1)],outputs=[dict(name='IMAGE',type='IMAGE',links=[2],slot_index=0)],
        properties={},widgets_values=['']),
    dict(id=3,type='SaveImage',pos=[740,150],size=[300,320],flags={},order=2,mode=0,
        inputs=[dict(name='images',type='IMAGE',link=2)],properties={},widgets_values=['PromptStudio-QA'])],
    links=[[1,1,0,2,0,'IMAGE'],[2,2,0,3,0,'IMAGE']],groups=[],config={},extra={},version=0.4)
(QA/'sample-workflow.json').write_text(json.dumps(workflow,ensure_ascii=False,indent=2),encoding='utf-8')
workflow['nodes'][1]['properties']['prompt_studio']=dict(version=1,field='text',snapshot=snapshot)
prompt={
    '1':dict(class_type='LoadImage',inputs=dict(image='fixture.png')),
    '2':dict(class_type='PromptStudioTestFixture',inputs=dict(image=['1',0],text=snapshot['final_prompt'])),
    '3':dict(class_type='SaveImage',inputs=dict(images=['2',0],filename_prefix='PromptStudio-QA'))}
outputs=[]
for run in range(2):
    if run: prompt['3']['inputs']['filename_prefix']='PromptStudio-QA-cached'
    submitted=request('/prompt',dict(prompt=prompt,extra_data=dict(extra_pnginfo=dict(workflow=workflow))))
    ident=submitted['prompt_id']
    for _ in range(60):
        history=request('/history/'+ident)
        if ident in history: break
        time.sleep(.2)
    else: raise RuntimeError('Fixture timed out')
    entry=history[ident]
    assert entry['status']['status_str']=='success',entry['status']
    image=entry['outputs']['3']['images'][0]
    collected=request('/prompt_studio/collect',dict(image=image,prompt_id=ident,workspace=workspace))
    assert collected['has_generation'] and collected['has_snapshot'],collected
    raw=png_metadata(collected['path'])
    assert image_snapshots(raw)[0]['snapshot']['final_prompt']==snapshot['final_prompt']
    assert raw['raw']['prompt_studio']['submission_id']==ident
    duplicate=request('/prompt_studio/collect',dict(image=image,prompt_id=ident,workspace=workspace))
    assert duplicate['already']
    outputs.append(dict(id=ident,metadata=collected,events=entry['status']['messages']))
# Even SaveImage can be cached: there is no newly generated PNG to relabel.
# The queue still records this submission's separate module snapshot.
workflow['nodes'][1]['properties']['prompt_studio']['snapshot']['state']['items'][0]['name'] = 'same text, new snapshot label'
cached_id=request('/prompt',dict(prompt=prompt,extra_data=dict(extra_pnginfo=dict(workflow=workflow))))['prompt_id']
for _ in range(60):
    cached_history=request('/history/'+cached_id)
    if cached_id in cached_history: break
    time.sleep(.2)
else: raise RuntimeError('Fully cached fixture timed out')
cached_entry=cached_history[cached_id]
assert cached_entry['status']['status_str']=='success'
assert not cached_entry['outputs'], cached_entry['outputs']
connection=sqlite3.connect((QA/'user'/'prompt_studio'/'integration.sqlite3').as_uri()+'?mode=ro',uri=True)
try: recorded=json.loads(connection.execute('SELECT body FROM jobs WHERE id=?',(cached_id,)).fetchone()[0])
finally: connection.close()
assert recorded['submission_id']==cached_id
assert recorded['bindings'][0]['snapshot']['state']['items'][0]['name']=='same text, new snapshot label'
try:
    request('/prompt_studio/config',dict(workspace=workspace,destination='missing'))
    raise AssertionError('Invalid destination accepted')
except urllib.error.HTTPError as error: assert error.code==400
report=dict(ok=True,results=outputs,fully_cached=dict(id=cached_id,events=cached_entry['status']['messages'],snapshot_recorded=True))
(QA/'live-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('Real ComfyUI queue, repeated cached execution, PNG metadata, byte-copy collection and duplicate protection passed.')
