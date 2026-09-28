"""Native Run requests share the desktop input-buffer consumer, never its DB."""
import copy
import json
import time
from .workflow_state import matches


class InputBridge:
    def __init__(self,service):
        self.service=service
        with service.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS input_bridge (client TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS input_requests (id TEXT PRIMARY KEY, client TEXT NOT NULL, body TEXT NOT NULL)')
            # Retain desktop-owned contents, but a stopped desktop cannot own
            # the native Run button indefinitely (including replaced copies).
            for client,raw in list(db.execute('SELECT client,body FROM input_bridge')):
                value=json.loads(raw);value['seen']=0
                db.execute('UPDATE input_bridge SET body=? WHERE client=?',(json.dumps(value),client))

    def publish(self,value):
        client=value.get('client');session=value.get('session');entries=value.get('entries')
        if any(not isinstance(v,str) or not 1<=len(v)<=128 for v in (client,session)) or not isinstance(entries,list) or len(entries)>100:
            raise ValueError('預排程同步格式無效。')
        for entry in entries:
            if not isinstance(entry,dict) or any(not isinstance(entry.get(k),str) for k in ('owner','head','workflow','frontend_id','path')) or type(entry.get('paused')) is not bool:
                raise ValueError('預排程工作歸屬無效。')
        with self.service.connect() as db:
            old=db.execute('SELECT body FROM input_bridge WHERE client=?',(client,)).fetchone()
            if old:
                old=json.loads(old[0])
                if old['session']!=session and time.time()-old['seen']<8:raise ValueError('同一 PCS 資料庫已有另一個執行視窗，預排程同步已暫停。')
            for ident in value.get('ack',[]):
                db.execute('DELETE FROM input_requests WHERE id=? AND client=?',(ident,client))
            body=dict(session=session,entries=copy.deepcopy(entries),seen=time.time())
            db.execute('INSERT OR REPLACE INTO input_bridge VALUES (?,?)',(client,json.dumps(body)))
            return dict(requests=[json.loads(raw) for raw, in db.execute('SELECT body FROM input_requests WHERE client=?',(client,))])

    def claim(self,value):
        ident=value.get('id');identity=value.get('identity')
        if not isinstance(ident,str) or not 1<=len(ident)<=128 or not isinstance(identity,dict):raise ValueError('原生執行識別無效。')
        with self.service.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            found=[]
            for client,raw in db.execute('SELECT client,body FROM input_bridge'):
                body=json.loads(raw)
                if time.time()-body['seen']>8:continue
                for entry in body['entries']:
                    if matches(identity,dict(workflow_id=entry['workflow'],frontend_id=entry['frontend_id'],origin=dict(path=entry['path'])),{}):found.append((client,body,entry))
            if not found:return dict(handled=False)
            available=[item for item in found if not item[2]['paused'] and time.time()-item[1]['seen']<=8]
            if len(available)==1:found=available
            if len(found)!=1:return dict(handled=False)
            client,body,entry=found[0]
            if time.time()-body['seen']>8 or entry['paused']:return dict(handled=False)
            request=dict(id=ident,owner=entry['owner'],head=entry['head'],created=time.time())
            prior=db.execute('SELECT body FROM input_requests WHERE id=?',(ident,)).fetchone()
            if not prior:db.execute('INSERT INTO input_requests VALUES (?,?,?)',(ident,client,json.dumps(request)))
            return dict(handled=True,item=entry['head'],message='已交由 PCS 預排程接續；此按鍵不重複提交。')
