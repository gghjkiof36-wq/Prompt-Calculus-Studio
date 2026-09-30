"""Scoped configuration-independent records over the existing desktop database."""
import copy
import json
import time
import uuid

ACTIVE=('waiting','running','preparing','submitted','collecting','failed','unconfirmed','result_error')


class StageStore:
    def __init__(self,db):
        self.db=db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS stage_journal (id TEXT PRIMARY KEY, kind TEXT, workspace TEXT, owner TEXT, position INTEGER, body TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS stage_owner ON stage_journal(kind,workspace,owner,position)')
        for run in self.rows('run'):
            if run['status'] not in ('complete','cancelled'):self.update(run['id'],status='paused',pause_reason='restart',message='重新開啟後保留進度，請從紀錄繼續。')

    def rows(self,kind,workspace=None,owner=None,active=False):
        sql='SELECT body FROM stage_journal WHERE kind=?';args=[kind]
        for key,val in (('workspace',workspace),('owner',owner)):
            if val is not None:sql+=' AND '+key+'=?';args.append(val)
        if active:sql+=" AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')';args.extend(ACTIVE)
        return [json.loads(r[0]) for r in self.db.execute(sql+' ORDER BY position',args)]

    def read(self,ident):
        row=self.db.execute('SELECT body FROM stage_journal WHERE id=?',(ident,)).fetchone()
        return json.loads(row[0]) if row else None

    def add(self,kind,workspace,owner='',**values):
        item=dict(id=uuid.uuid4().hex,kind=kind,workspace=workspace,owner=owner,created=time.time(),**copy.deepcopy(values))
        with self.db:
            if kind=='entry' and sum(r.get('scheduler')==item.get('scheduler') for r in self.rows(kind,workspace,active=True))>=10:
                raise ValueError('預排程已滿十項，未加入這次要求。')
            self.db.execute('INSERT INTO stage_journal VALUES (?,?,?,?,(SELECT COALESCE(MAX(position),0)+1 FROM stage_journal),?)',
                (item['id'],kind,workspace,owner,json.dumps(item,ensure_ascii=False)))
        return item

    def update(self,ident,**values):
        item=self.read(ident)
        if not item:raise ValueError('執行紀錄不存在。')
        item.update(copy.deepcopy(values))
        with self.db:self.db.execute('UPDATE stage_journal SET body=? WHERE id=?',(json.dumps(item,ensure_ascii=False),ident))
        return item

    def reorder(self,owner,ids):
        rows=self.rows('entry',owner=owner,active=True);waiting=[i for i in rows if i['status']=='waiting']
        if len(ids)!=len(set(ids)) or set(ids)!={i['id'] for i in waiting}:raise ValueError('只能拖曳尚未提交的項目。')
        with self.db:
            positions=sorted(self.db.execute('SELECT position FROM stage_journal WHERE id=?',(i['id'],)).fetchone()[0] for i in waiting)
            for index,key in zip(positions,ids):self.db.execute('UPDATE stage_journal SET position=? WHERE id=?',(index,key))

    def edit(self,ident,saved):
        item=self.read(ident)
        if not item or item['status']!='waiting':raise ValueError('已提交的項目僅供查看。')
        self.update(ident,saved=saved)

    def remove(self,ident):
        item=self.read(ident)
        if not item or item['status']!='waiting':raise ValueError('只能移除等待項目。')
        self.update(ident,status='removed')

    def scheduler_paused(self,workspace,key):
        return any(r.get('scheduler')==key and r.get('paused') for r in self.rows('control',workspace))

    def set_scheduler_paused(self,workspace,key,paused):
        row=next((r for r in self.rows('control',workspace) if r.get('scheduler')==key),None)
        if row:self.update(row['id'],paused=bool(paused))
        else:self.add('control',workspace,scheduler=key,paused=bool(paused),status='control')

    def restore_retained(self,run):
        entries=[r for r in self.rows('entry',run['workspace']) if r['run']==run['id'] and r['status']=='retained']
        active=self.rows('entry',run['workspace'],active=True)
        for key in {r['scheduler'] for r in entries}:
            if sum(r['scheduler']==key for r in entries+active)>10:raise ValueError('預排程最多十項；請先移除其他等待項目，再恢復這份流程。')
        with self.db:
            for row in entries:self.update(row['id'],status='waiting')
