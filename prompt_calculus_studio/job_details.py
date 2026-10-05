"""User-readable diagnostics from the frozen job record, never the current UI."""
import json
import time

NAMES={'submitting':'提交中','queued':'等待中','running':'執行中','complete':'完成','failed':'失敗','unconfirmed':'未確認'}

def native_details(record):
    phases=dict(received='網頁收到要求',select='切換工作流',activate='確認工作流',apply='套用輸入',
        native_wait='等待原生執行入口',serialize='讀取原生工作流',prepare='保存提交內容',transport='送出生成',native_finish='原生提交收尾',reply='回傳結果')
    states=dict(started='開始',done='完成',error='中斷',timeout='逾時')
    lines=[]
    events=record.get('diagnostics',{}).get('events',[])
    if record.get('issued_at') and not events:lines.append('要求已派送；尚無網頁收到要求的紀錄。')
    if events:
        event=next((e for e in events if e['state'] in ('timeout','error')),events[-1])
        lines.append('最後提交階段：'+phases.get(event['phase'],event['phase'])+' · '+states.get(event['state'],event['state']))
    if record.get('first_error'):lines.append('最初錯誤：'+record['first_error'])
    if record.get('terminal_action'):lines.append('後續處理：'+{'cancel':'取消','abandon':'停止追蹤'}.get(record['terminal_action'],record['terminal_action']))
    return '\n'.join(lines)


def job_marker(record):
    extra=record.get('payload',{}).get('extra_data',{}).get('extra_pnginfo',{})
    return extra.get('prompt_studio_request') or extra.get('prompt_studio') or {}


def workflow_name(record):
    return job_marker(record).get('generation',{}).get('workflow') or record.get('requested_workflow','尚未確認')


def describe(record,now=None):
    now=time.time() if now is None else now
    payload=record.get('payload',{}); marker=job_marker(record); gen=marker.get('generation',{})
    recovery=record.get('recovery',{})
    recovery_text=('最近核對：'+recovery['reason']) if recovery.get('reason') else ''
    if not marker:
        return '\n'.join(['狀態：'+NAMES.get(record['state'],record['state']),'原生操作：'+record['id'],
            '工作流：'+workflow_name(record),'尚未收到原生提交內容；不會顯示 PCS 舊副本。',record.get('error',''),native_details(record),recovery_text])
    snapshot=marker.get('snapshot') or next((b['snapshot'] for b in marker.get('bindings',[]) if 'snapshot' in b),{})
    profile=next((p for p in snapshot.get('state',{}).get('generation',{}).get('profiles',[]) if p['id']==gen['workflow_id']),{})
    origin=gen.get('origin') or profile.get('origin') or {}
    lines=['狀態：'+NAMES.get(record['state'],record['state']),'伺服器：'+record['server'],
           '任務：'+record.get('prompt_id',record['id']),'工作流：'+gen['workflow'],
           '工作流 ID：'+gen['workflow_id'],'來源：'+origin.get('path','PCS 匯入副本'),
           '提交時間：'+time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(record['created']))]
    workspace=record.get('workspace')
    if native_details(record):lines.append(native_details(record))
    if recovery_text:lines[1:1]=[recovery_text,'']
    if workspace:
        name=next((w['name'] for w in snapshot.get('state',{}).get('workspaces',[]) if w['id']==workspace),workspace)
        lines.append('來源工作區：'+name)
    if record.get('entry_point'):lines.append('執行入口：'+('ComfyUI 手動按鍵' if record['entry_point']=='native' else 'PCS'))
    if record.get('execution_state')=='complete' and record['state']!='complete':lines.append('執行已結束；內容或輸出核對異常，後續已暫停。')
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
    parameters=record.get('parameter_receipt') or gen.get('stage_parameters',{})
    if parameters.get('fields'):
        lines.extend(['','當次 Stage 參數：'])
        for field in parameters['fields']:
            lines.append('#'+'/'.join(field.get('path',[field['node']]))+' / '+field['field']+'：'+str(field['actual'])+
                         (' · '+field['seed_mode']+' / '+field['seed_timing'] if field.get('seed_mode') else ''))
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
