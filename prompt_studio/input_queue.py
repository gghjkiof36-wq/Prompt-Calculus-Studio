"""Persistent bounded buffers of typed inputs, independent of Canvas undo."""
import copy
import json
import time
import uuid
from .flow_data import CAPACITY, validate_value

ACTIVE = ('waiting','preparing','submitted','unconfirmed','failed')


class InputQueue:
    def __init__(self, db, workspace=None):
        self.db=db;self.workspace=workspace
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS input_queue (id TEXT PRIMARY KEY, owner TEXT NOT NULL, position INTEGER NOT NULL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS input_queue_control (owner TEXT PRIMARY KEY, body TEXT NOT NULL)')
        if workspace:self.migrate_owners();self.migrate_execution()

    def migrate_execution(self):
        """Old waiting buffers are evidence, not consent to run after upgrade."""
        with self.db:
            for owner,raw in list(self.db.execute('SELECT owner,body FROM input_queue_control')):
                control=json.loads(raw)
                if control.get('execution_revision')==2:continue
                if not owner.startswith('live:'):
                    for ident,body in list(self.db.execute('SELECT id,body FROM input_queue WHERE owner=?',(owner,))):
                        item=json.loads(body)
                        if item['state'] in ACTIVE:
                            item.update(previous_state=item['state'],state='retained',error='舊版保留紀錄，未自動重新提交。')
                            self.db.execute('UPDATE input_queue SET body=? WHERE id=?',(json.dumps(item,ensure_ascii=False),ident))
                    control.update(legacy_feed=control.get('feed'),feed=None,active=None)
                control.update(execution_revision=2,paused=True)
                self.db.execute('UPDATE input_queue_control SET body=? WHERE owner=?',(json.dumps(control,ensure_ascii=False),owner))

    def scoped(self, owner):
        if owner is None or not self.workspace or owner.startswith(('live:','schedule:')):return owner
        return 'schedule:'+self.workspace()+':'+owner

    def migrate_owners(self):
        with self.db:
            for old,raw in list(self.db.execute('SELECT owner,body FROM input_queue_control')):
                body=json.loads(raw);route=body.get('route',{})
                if old.startswith(('live:','schedule:')) or not route.get('workspace'):continue
                new='schedule:'+route['workspace']+':'+old
                if self.db.execute('SELECT 1 FROM input_queue_control WHERE owner=?',(new,)).fetchone():continue
                self.db.execute('UPDATE input_queue_control SET owner=? WHERE owner=?',(new,old))
            for ident,old,raw in list(self.db.execute('SELECT id,owner,body FROM input_queue')):
                body=json.loads(raw);route=body.get('route',{})
                if old.startswith(('live:','schedule:')) or not route.get('workspace'):continue
                new='schedule:'+route['workspace']+':'+old;body['owner']=new
                self.db.execute('UPDATE input_queue SET owner=?,body=? WHERE id=?',(new,json.dumps(body,ensure_ascii=False),ident))

    def rows(self, owner=None, history=False):
        owner=self.scoped(owner)
        clauses=[];args=[]
        if owner is not None:clauses.append('owner=?');args.append(owner)
        if not history:
            clauses.append("json_extract(body,'$.state') IN ("+','.join('?' for _ in ACTIVE)+')');args.extend(ACTIVE)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM input_queue'+where+' ORDER BY position',args)]

    def read(self, ident):
        row=self.db.execute('SELECT body FROM input_queue WHERE id=?',(ident,)).fetchone()
        if row is None:raise ValueError('預排程項目不存在。')
        return json.loads(row[0])

    def control(self, owner):
        owner=self.scoped(owner)
        row=self.db.execute('SELECT body FROM input_queue_control WHERE owner=?',(owner,)).fetchone()
        return json.loads(row[0]) if row else dict(paused=False,feed=None,execution_revision=2)

    def set_control(self, owner, **changes):
        owner=self.scoped(owner)
        data=self.control(owner);data.update(copy.deepcopy(changes))
        with self.db:self.db.execute('INSERT OR REPLACE INTO input_queue_control VALUES (?,?)',(owner,json.dumps(data,ensure_ascii=False)))
        return data

    def recover(self):
        """Reopening retains inputs, and requires an explicit resume."""
        for owner in {r['owner'] for r in self.rows()}:
            self.set_control(owner,paused=True)

    def add(self, owner, inputs, *, route, source=None, label=''):
        owner=self.scoped(owner)
        if not isinstance(inputs,dict) or not inputs:raise ValueError('預排程沒有接入資料。')
        for item in inputs.values():validate_value(item)
        item=dict(id=uuid.uuid4().hex,owner=owner,inputs=copy.deepcopy(inputs),route=copy.deepcopy(route),
                  source=copy.deepcopy(source),label=label,state='waiting',created=time.time(),error='')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if len(self.rows(owner))>=CAPACITY:raise ValueError('預排程已滿十項；請等待完成或移除一項。')
            self.db.execute('INSERT INTO input_queue VALUES (?, ?, (SELECT COALESCE(MAX(position),0)+1 FROM input_queue), ?)',
                            (item['id'],owner,json.dumps(item,ensure_ascii=False)))
        return item

    def update(self, ident, **changes):
        with self.db:
            item=self.read(ident);item.update(copy.deepcopy(changes))
            self.db.execute('UPDATE input_queue SET body=? WHERE id=?',(json.dumps(item,ensure_ascii=False),ident))
        return item

    def edit(self, ident, inputs):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            item=self.read(ident)
            if item['state']!='waiting':raise ValueError('只能修改尚未派送的項目。')
            if set(inputs)!=set(item['inputs']):raise ValueError('編輯不能變更項目的輸入端口。')
            for key,value in inputs.items():
                validate_value(value)
                if value['type']!=item['inputs'][key]['type']:raise ValueError('輸入型別不可變更。')
            item['inputs']=copy.deepcopy(inputs);item['modified']=time.time()
            self.db.execute('UPDATE input_queue SET body=? WHERE id=?',(json.dumps(item,ensure_ascii=False),ident))

    def edit_pending(self, ident, action):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');item=self.read(ident)
            if item['state']!='waiting':raise ValueError('只能調整尚未派送的項目。')
            if action=='remove':
                item.update(state='removed');self.db.execute('UPDATE input_queue SET body=? WHERE id=?',(json.dumps(item,ensure_ascii=False),ident));return
            items=self.rows(item['owner']);pending=[r['id'] for r in items if r['state']=='waiting']
            at=pending.index(ident);pending.remove(ident)
            pending.insert(0 if action=='next' else max(0,at-1) if action=='up' else min(len(pending),at+1),ident)
            positions=[r[0] for r in self.db.execute('SELECT position FROM input_queue WHERE owner=? ORDER BY position',(item['owner'],))]
            order=[r['id'] for r in self.rows(item['owner'],history=True)]
            moving=iter(pending);order=[next(moving) if key in pending else key for key in order]
            for pos,key in zip(positions,order):self.db.execute('UPDATE input_queue SET position=? WHERE id=?',(pos,key))

    def reorder(self, owner, ids):
        owner=self.scoped(owner)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            pending=[r['id'] for r in self.rows(owner) if r['state']=='waiting']
            if len(ids)!=len(set(ids)) or set(ids)!=set(pending):raise ValueError('等待清單已更新，請重新拖曳。')
            positions=[r[0] for r in self.db.execute("SELECT position FROM input_queue WHERE owner=? AND json_extract(body,'$.state')='waiting' ORDER BY position",(owner,))]
            for pos,ident in zip(positions,ids):self.db.execute('UPDATE input_queue SET position=? WHERE id=?',(pos,ident))
