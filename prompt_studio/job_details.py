"""User-readable diagnostics from the frozen job record, never the current UI."""
import json
import time

NAMES={'submitting':'提交中','queued':'等待中','running':'執行中','complete':'完成','failed':'失敗','unconfirmed':'未確認'}


def job_marker(record):
    extra=record.get('payload',{}).get('extra_data',{}).get('extra_pnginfo',{})
    return extra.get('prompt_studio_request') or extra.get('prompt_studio') or {}


def workflow_name(record):
    return job_marker(record).get('generation',{}).get('workflow') or record.get('requested_workflow','尚未確認')


def describe(record,now=None):
    now=time.time() if now is None else now
    payload=record.get('payload',{}); marker=job_marker(record); gen=marker.get('generation',{})
    if not marker:
        return '\n'.join(['狀態：'+NAMES.get(record['state'],record['state']),'原生操作：'+record['id'],
            '工作流：'+workflow_name(record),'尚未收到原生提交內容；不會顯示 PCS 舊副本。',record.get('error','')])
    snapshot=marker.get('snapshot') or next((b['snapshot'] for b in marker.get('bindings',[]) if 'snapshot' in b),{})
    profile=next((p for p in snapshot.get('state',{}).get('generation',{}).get('profiles',[]) if p['id']==gen['workflow_id']),{})
    origin=gen.get('origin') or profile.get('origin') or {}
    lines=['狀態：'+NAMES.get(record['state'],record['state']),'伺服器：'+record['server'],
           '任務：'+record.get('prompt_id',record['id']),'工作流：'+gen['workflow'],
           '工作流 ID：'+gen['workflow_id'],'來源：'+origin.get('path','PCS 匯入副本'),
           '提交時間：'+time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(record['created']))]
    if record.get('started'):
        elapsed=max(0,int(record.get('finished',now)-record['started'])); lines.append(f'執行經過：{elapsed} 秒')
    if record.get('node'):
        key=record['node']; node=payload['prompt'].get(key,{})
        lines.append('最後執行節點：#'+key+' · '+node.get('_meta',{}).get('title',node.get('class_type','未知節點')))
        if not record.get('finished') and record.get('node_started'):lines.append(f"此節點已等待：{max(0,int(now-record['node_started']))} 秒")
    if record.get('last_seen'):lines.append('最後確認：'+time.strftime('%H:%M:%S',time.localtime(record['last_seen'])))
    if record['state'] in ('running','unconfirmed'):
        lines.append('等待時間不能判定卡死原因；可用任務編號對照 ComfyUI 後端紀錄。')
    if record.get('error'):lines.extend(['','錯誤：'+record['error']])
    for event in record.get('events',[]):
        if isinstance(event,(list,tuple)) and len(event)>1 and isinstance(event[1],dict) and event[0] in ('execution_error','execution_interrupted'):
            item=event[1]; lines.append('失敗節點：#'+str(item.get('node_id',''))+' · '+str(item.get('node_type','')))
            if item.get('exception_type'):lines.append('錯誤類型：'+str(item['exception_type']))
    source=gen.get('source')
    if source:lines.extend(['','來源圖片：'+source['name'],'圖片 SHA256：'+source['sha256'],'送入 ComfyUI：'+source.get('uploaded','')])
    for node,output in record.get('outputs',{}).items():
        for image in output.get('images',[]) if isinstance(output,dict) else []:
            lines.append('結果圖片：#'+node+' · '+image.get('type','output')+'/'+image.get('subfolder','')+'/'+image.get('filename',''))
    lines.extend(['','當次已綁定文字：'])
    texts=marker.get('texts') or ([dict(node=marker['node_id'],field=marker['field'])] if 'node_id' in marker else [])
    for text in texts:
        value=payload['prompt'].get(text['node'],{}).get('inputs',{}).get(text['field'])
        lines.extend(['#'+text['node']+' / '+text['field'],str(value) if value else '（空白）'])
    lines.extend(['','實際提交工作流：',json.dumps(payload['prompt'],ensure_ascii=False,indent=2)])
    return '\n'.join(lines)
