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

    def edit(self,ident,saved,expected_saved=None):
        item=self.read(ident)
        if not item or item['status']!='waiting':raise ValueError('已提交的項目僅供查看。')
        if expected_saved is not None and item['saved']!=expected_saved:raise ValueError('這一項已被修改，請重新開啟後比較。')
        self.update(ident,saved=saved)

    def edit_parameters(self,ident,stage,config,expected_saved,profile=None):
        """Compare-and-save one waiting item's parameters, never its live Stage."""
        from .stage_parameters import validate
        validate(config)
        with self.db:
            item=self.read(ident)
            if not item or item['status']!='waiting':raise ValueError('此項已開始準備，參數草稿保留，不能修改已準備內容。')
            if item['saved']!=expected_saved:raise ValueError('這一項已被修改，請重新開啟後比較。')
            if stage not in item['stages']:raise ValueError('此 Stage 不屬於這一項。')
            saved=copy.deepcopy(item['saved']);saved.setdefault('parameter_overrides',{})[stage]=copy.deepcopy(config)
            if profile is not None:
                from .stage_parameters import identity
                if identity(profile)!=config['identity']:raise ValueError('此任務的參數與工作流身分不符。')
                saved.setdefault('parameter_profiles',{})[stage]=copy.deepcopy(profile)
            self.update(ident,saved=saved)

    def copy_entry(self,ident):
        with self.db:
            item=self.read(ident)
            if not item or item['status']!='waiting':raise ValueError('只能複製尚未開始的項目。')
            run=self.read(item['run'])
            if not run or run['status'] in ('complete','cancelled'):raise ValueError('這份流程已結束。')
            values={k:copy.deepcopy(v) for k,v in item.items() if k not in ('id','kind','workspace','owner','created')}
            return self.add('entry',item['workspace'],item['owner'],**values)

    def resolve_parameters(self,workspace,stage,config):
        if not config:return config
        from .stage_parameters import seed_signature,validate
        validate(config)
        result=copy.deepcopy(config)
        for field in result['patches']:
            if not field.get('seed_mode') or field['seed_mode']=='fixed':continue
            signature=seed_signature(result,field)
            prior=next((r for r in self.rows('parameter_state',workspace,stage) if r['signature']==signature),None)
            if prior:
                if prior.get('next_seed') is None:raise ValueError('這個 Stage 的下一顆種子尚未取得原生回執，請從紀錄更新原任務。')
                field['resolved']=prior['next_seed']
        return result

    def record_parameters(self,attempt,job):
        """Only an owned native receipt may advance a seed intention, once."""
        config=attempt['snapshot_state']['multi_output']['stages'][attempt['stage']].get('parameters')
        receipt=job.get('parameter_receipt')
        if not config or not receipt or not job.get('prompt_id') or attempt.get('parameter_replay'):return
        if receipt.get('version')!=1 or receipt.get('identity')!=config['identity'] or receipt.get('owner')!=dict(
                workspace=attempt['workspace'],stage=attempt['stage'],parent=attempt.get('parent')):return
        from .stage_parameters import payload_evidence,validate_value
        try:
            payload_evidence(config,job.get('payload',{}).get('prompt',{}),receipt['fields'])
            for field,proof in zip(config['patches'],receipt['fields']):
                if 'next_value' in proof:validate_value(proof['next_value'],field)
        except (ValueError,KeyError,TypeError):return
        from .stage_parameters import seed_signature
        for field,proof in zip(config['patches'],receipt.get('fields',[])):
            if (not field.get('seed_mode') or field['seed_mode']=='fixed' or proof.get('node')!=field['node']
                    or proof.get('field')!=field['field']):continue
            signature=seed_signature(config,field)
            with self.db:
                prior=next((r for r in self.rows('parameter_state',attempt['workspace'],attempt['stage']) if r['signature']==signature),None)
                if prior and prior.get('attempt_created',0)>attempt['created']:continue
                values=dict(signature=signature,attempt=attempt['id'],attempt_created=attempt['created'],prompt_id=job['prompt_id'],
                    used_seed=proof['actual'],status='observed',next_seed=proof.get('next_value'))
                if prior:
                    if prior.get('prompt_id')==job['prompt_id'] and prior.get('next_seed') is not None:continue
                    self.update(prior['id'],**values)
                else:self.add('parameter_state',attempt['workspace'],attempt['stage'],**values)

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
