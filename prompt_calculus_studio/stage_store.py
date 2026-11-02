"""Scoped configuration-independent records over the existing desktop database."""
import copy
import json
import time
import uuid
from contextlib import contextmanager, nullcontext

ACTIVE=('waiting','running','preparing','submitted','collecting','failed','unconfirmed','result_error')


class StageStore:
    def __init__(self,db):
        self.db=db;self.transaction_depth=0
        from .sqlite_schema import schema_transaction
        with schema_transaction(db):
            db.execute('CREATE TABLE IF NOT EXISTS stage_journal (id TEXT PRIMARY KEY, kind TEXT, workspace TEXT, owner TEXT, position INTEGER, body TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS stage_owner ON stage_journal(kind,workspace,owner,position)')
            db.execute("CREATE INDEX IF NOT EXISTS stage_batch_control ON stage_journal(workspace,owner,json_extract(body,'$.group')) WHERE kind='batch_control'")
            db.execute('CREATE INDEX IF NOT EXISTS stage_position ON stage_journal(position)')
            db.execute('CREATE INDEX IF NOT EXISTS stage_owner_order ON stage_journal(kind,owner,position)')
            db.execute("CREATE INDEX IF NOT EXISTS stage_group ON stage_journal(kind,workspace,json_extract(body,'$.scheduler'),json_extract(body,'$.execution_group'),position)")
            db.execute("""CREATE INDEX IF NOT EXISTS stage_entry_display ON stage_journal(kind,workspace,
                json_extract(body,'$.scheduler'),json_extract(body,'$.status'),
                COALESCE(json_extract(body,'$.execution_group'),'legacy:'||json_extract(body,'$.run')),
                json_extract(body,'$.run'),id,position,json_extract(body,'$.created'),json_extract(body,'$.label'))""")
            db.execute("CREATE INDEX IF NOT EXISTS stage_latest ON stage_journal(kind,workspace,json_extract(body,'$.stage'),position)")
            db.execute("CREATE INDEX IF NOT EXISTS stage_active ON stage_journal(kind,json_extract(body,'$.status'),workspace,owner,position)")
            db.execute("CREATE INDEX IF NOT EXISTS stage_open_runs ON stage_journal(workspace,position) WHERE kind='run' AND json_extract(body,'$.status') NOT IN ('complete','cancelled')")
            db.execute("CREATE INDEX IF NOT EXISTS stage_parent ON stage_journal(kind,workspace,json_extract(body,'$.parent'),position)")
        for run in self.open_runs():
            self.update(run['id'],status='paused',pause_reason='restart',message='重新開啟後保留進度，請從紀錄繼續。')

    def open_runs(self,workspace=None):
        sql="SELECT body FROM stage_journal WHERE kind='run' AND json_extract(body,'$.status') NOT IN ('complete','cancelled')"
        args=[]
        if workspace is not None:sql+=' AND workspace=?';args.append(workspace)
        return [json.loads(row[0]) for row in self.db.execute(sql+' ORDER BY position',args)]

    def latest_stage_status(self,workspace,stage):
        """Paint status without fetching or sorting complete prompt/image snapshots."""
        row=self.db.execute("""SELECT json_extract(body,'$.status'),json_extract(body,'$.error')
            FROM stage_journal WHERE kind='attempt' AND workspace=? AND json_extract(body,'$.stage')=?
            ORDER BY position DESC LIMIT 1""",(workspace,stage)).fetchone()
        return dict(status=row[0],error=row[1] or '') if row else None

    def status_value(self,ident):
        row=self.db.execute("SELECT json_extract(body,'$.status') FROM stage_journal WHERE id=?",(ident,)).fetchone()
        return row[0] if row else None

    def latest_entry_attempt(self,workspace,ident):
        """Read only the latest descendant attempt, including nested scopes."""
        # Parent-first traversal and an affinity-free bound keep both lookups
        # on the parent index, rather than scanning a workspace's history.
        row=self.db.execute("""WITH RECURSIVE descendants(id) AS (
                VALUES (?) UNION
                SELECT e.id FROM descendants d CROSS JOIN stage_journal e
                    ON json_extract(e.body,'$.parent')=+d.id
                    WHERE e.kind='entry' AND e.workspace=?
            ), latest(id) AS (
                SELECT (SELECT a.id FROM stage_journal a
                    WHERE a.kind='attempt' AND a.workspace=? AND json_extract(a.body,'$.parent')=+d.id
                    ORDER BY a.position DESC LIMIT 1) FROM descendants d
            )
            SELECT json_extract(body,'$.status'),json_extract(body,'$.cancel_requested')
            FROM stage_journal WHERE id=(SELECT a.id FROM latest
                JOIN stage_journal a ON a.id=latest.id ORDER BY a.position DESC LIMIT 1)""",
            (ident,workspace,workspace)).fetchone()
        return dict(status=row[0],cancel_requested=row[1]) if row else None

    def entry_states(self,workspace,scheduler):
        """Minimal ownership/state projection for a collapsed schedule card."""
        query="SELECT id,json_extract(body,'$.run'),json_extract(body,'$.status'),COALESCE(json_extract(body,'$.execution_group'),'legacy:'||json_extract(body,'$.run')) FROM stage_journal INDEXED BY stage_entry_display WHERE kind='entry' AND workspace=? AND json_extract(body,'$.scheduler')=? AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+') ORDER BY position'
        return [dict(id=ident,workspace=workspace,run=run,status=status,execution_group=group)
                for ident,run,status,group in self.db.execute(query,(workspace,scheduler,*ACTIVE))]

    def entry_summaries(self,workspace,scheduler,*,group=None,limit=None,offset=0):
        """List display fields, leaving frozen graphs/text for explicit detail reads."""
        query="""SELECT id,workspace,owner,
            json_extract(body,'$.run','$.scheduler','$.parent','$.status','$.label'),
            json_extract(body,'$.source'),
            (SELECT value FROM json_each(body,'$.saved.values') WHERE json_extract(value,'$.type')='image' LIMIT 1),
            (SELECT value FROM json_each(body,'$.saved.inputs') WHERE json_extract(value,'$.type')='image' LIMIT 1),
            (SELECT json_extract(value,'$[0]') FROM json_each(body,'$.saved.batches') WHERE json_array_length(value)>0 LIMIT 1),
            (SELECT MAX(json_array_length(value)) FROM json_each(body,'$.saved.batches'))
            FROM stage_journal WHERE kind='entry' AND workspace=? AND json_extract(body,'$.scheduler')=?"""
        args=[workspace,scheduler]
        if group is None:
            query+=" AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')';args.extend(ACTIVE)
        else:
            query+=" AND COALESCE(json_extract(body,'$.execution_group'),'legacy:'||json_extract(body,'$.run'))=?";args.append(group)
        query+=' ORDER BY position'
        if limit is not None:query+=' LIMIT ? OFFSET ?';args.extend((limit,offset))
        result=[]
        for row in self.db.execute(query,args):
            run,key,parent,status,label=json.loads(row[3])
            source=json.loads(row[4]) if row[4] is not None else None
            first=row[5] if row[5] is not None else row[6]
            value=json.loads(first)['value'] if first is not None else None
            batch=json.loads(row[7]) if row[7] is not None else None
            image=source or value or batch
            count=1 if source else row[8] if row[8] is not None else 1 if image else None
            result.append(dict(id=row[0],workspace=row[1],owner=row[2],run=run,scheduler=key,
                               parent=parent,status=status,label=label,image=image,image_count=count))
        return result

    def active_count(self,workspace,scheduler,owner=None):
        query="SELECT COUNT(*) FROM stage_journal WHERE kind='entry' AND workspace=? AND json_extract(body,'$.scheduler')=? AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')'
        args=[workspace,scheduler,*ACTIVE]
        if owner is not None:query+=' AND owner=?';args.append(owner)
        return self.db.execute(query,args).fetchone()[0]

    def entry_descendants(self,workspace,ids):
        rows=self.db.execute("""WITH RECURSIVE descendants(id) AS (
            SELECT value FROM json_each(?) UNION
            SELECT e.id FROM descendants d CROSS JOIN stage_journal e
            ON json_extract(e.body,'$.parent')=+d.id WHERE e.kind='entry' AND e.workspace=?)
            SELECT e.id,json_extract(e.body,'$.status','$.run','$.parent','$.scheduler')
            FROM descendants d JOIN stage_journal e ON e.id=d.id WHERE e.kind='entry' AND e.workspace=?""",(json.dumps(list(ids)),workspace,workspace))
        result=[]
        for ident,raw in rows:
            status,run,parent,scheduler=json.loads(raw)
            result.append(dict(id=ident,status=status,run=run,parent=parent,scheduler=scheduler))
        return result

    def entry_attempts(self,workspace,ids,*,active=False):
        query="SELECT body FROM stage_journal WHERE kind='attempt' AND workspace=? AND json_extract(body,'$.parent') IN (SELECT value FROM json_each(?))"
        args=[workspace,json.dumps(list(ids))]
        if active:query+=" AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')';args.extend(ACTIVE)
        return [json.loads(r[0]) for r in self.db.execute(query+' ORDER BY position',args)]

    def next_entry(self,owner):
        query="""SELECT e.body FROM stage_journal e LEFT JOIN stage_journal b
            ON b.kind='batch_control' AND b.workspace=e.workspace AND b.owner=json_extract(e.body,'$.scheduler')
            AND json_extract(b.body,'$.group')=COALESCE(json_extract(e.body,'$.execution_group'),'legacy:'||json_extract(e.body,'$.run'))
            WHERE e.kind='entry' AND e.owner=? AND COALESCE(json_extract(b.body,'$.bypassed'),0)=0
            AND COALESCE(json_extract(b.body,'$.cancelled'),0)=0 AND json_extract(e.body,'$.status') IN ("""+','.join('?' for _ in ACTIVE)+") ORDER BY COALESCE(json_extract(b.body,'$.rank'),1000000000000+e.position),e.position LIMIT 1"
        row=self.db.execute(query,(owner,*ACTIVE)).fetchone()
        return json.loads(row[0]) if row else None

    def batch_controls(self,workspace,scheduler):
        records=(json.loads(r[0]) for r in self.db.execute("SELECT body FROM stage_journal WHERE kind='batch_control' AND workspace=? AND owner=?",(workspace,scheduler)))
        return {r['group']:r for r in records}

    def batch_control(self,workspace,scheduler,group):
        row=self.db.execute("SELECT body FROM stage_journal WHERE kind='batch_control' AND workspace=? AND owner=? AND json_extract(body,'$.group')=?",(workspace,scheduler,group)).fetchone()
        return json.loads(row[0]) if row else {}

    def preferred_entry(self,workspace,scheduler):
        """Resolve pending group priority across run boundaries, after live work."""
        if not self.db.execute("SELECT 1 FROM stage_journal WHERE kind='batch_control' AND workspace=? AND owner=? LIMIT 1",(workspace,scheduler)).fetchone():return None
        row=self.db.execute("""SELECT e.id,e.owner,json_extract(e.body,'$.run') FROM stage_journal e
            JOIN stage_journal r ON r.id=json_extract(e.body,'$.run')
            LEFT JOIN stage_journal b ON b.kind='batch_control' AND b.workspace=e.workspace AND b.owner=?
                AND json_extract(b.body,'$.group')=COALESCE(json_extract(e.body,'$.execution_group'),'legacy:'||r.id)
            WHERE e.kind='entry' AND e.workspace=? AND json_extract(e.body,'$.scheduler')=?
                AND (json_extract(r.body,'$.status') IN ('running','waiting') OR
                     (json_extract(e.body,'$.status')='running' AND json_extract(b.body,'$.cancelled')=1))
                AND json_extract(e.body,'$.status') IN ('waiting','running')
                AND (json_extract(e.body,'$.status')='running' OR
                     (COALESCE(json_extract(b.body,'$.bypassed'),0)=0 AND COALESCE(json_extract(b.body,'$.cancelled'),0)=0))
            ORDER BY (json_extract(e.body,'$.status')='running') DESC,
                COALESCE(json_extract(b.body,'$.rank'),1000000000000+e.position),e.position LIMIT 1""",(scheduler,workspace,scheduler)).fetchone()
        return dict(id=row[0],owner=row[1],run=row[2]) if row else None

    def set_batch_control(self,workspace,scheduler,group,**values):
        previous=self.batch_control(workspace,scheduler,group)
        if previous:self.patch(previous['id'],**values)
        else:self.add('batch_control',workspace,scheduler,group=group,**values)

    def reorder_groups(self,workspace,scheduler,ids,expected):
        with self.transaction():
            actual=[g['id'] for g in self.schedule_groups(workspace,scheduler)]
            if actual!=expected:raise ValueError('批次已更新，請重新排列。')
            if len(ids)!=len(set(ids)) or set(ids)!=set(actual):raise ValueError('批次已更新，請重新排列。')
            for rank,group in enumerate(ids):self.set_batch_control(workspace,scheduler,group,rank=rank)

    def waiting_ids(self,owner):
        return [r[0] for r in self.db.execute("SELECT id FROM stage_journal WHERE kind='entry' AND owner=? AND json_extract(body,'$.status')='waiting' ORDER BY position",(owner,))]

    def scheduler_runs(self,workspace,scheduler):
        query="""SELECT DISTINCT json_extract(body,'$.run') FROM stage_journal WHERE kind='entry' AND workspace=?
            AND json_extract(body,'$.scheduler')=? AND json_extract(body,'$.status') IN ('waiting','running','retained')
            UNION SELECT id FROM stage_journal r WHERE kind='run' AND workspace=?
            AND json_extract(body,'$.status') NOT IN ('complete','cancelled')
            AND (EXISTS(SELECT 1 FROM json_each(r.body,'$.plan.scopes') WHERE key=?)
            OR EXISTS(SELECT 1 FROM json_each(r.body,'$.plan.stages') WHERE json_extract(value,'$.scheduler')=?))"""
        return [r[0] for r in self.db.execute(query,(workspace,scheduler,workspace,scheduler,scheduler))]

    def schedule_groups(self,workspace,scheduler,include=None):
        """One accepted Execute per group; old records retain their run boundary.

        Only display fields cross this boundary. Pending automatic rounds are
        counted from their existing feed cursor, without materializing them.
        """
        # Aggregate even the group keys: SQLite otherwise retains a complete
        # body row for bare expressions beside MIN(), despite the display index.
        query="""SELECT MIN(COALESCE(json_extract(body,'$.execution_group'),'legacy:'||json_extract(body,'$.run'))),
            MIN(json_extract(body,'$.status')),COUNT(*),MIN(json_extract(body,'$.created')),
            MIN(position),MIN(json_extract(body,'$.label'))
            FROM stage_journal INDEXED BY stage_entry_display WHERE kind='entry' AND workspace=? AND json_extract(body,'$.scheduler')=?
            GROUP BY COALESCE(json_extract(body,'$.execution_group'),'legacy:'||json_extract(body,'$.run')),json_extract(body,'$.status') ORDER BY 5"""
        groups={}
        for ident,status,count,created,position,title in self.db.execute(query,(workspace,scheduler)):
            group=groups.setdefault(ident,dict(id=ident,counts={},total=0,pending=0,created=created,position=position,label=title or '執行'))
            group['counts'][status]=count;group['total']+=count
            group['position']=min(group['position'],position);group['created']=min(group['created'],created)
        # JSON extraction does not deserialize the run's graph or frozen inputs.
        feeds=self.db.execute("""SELECT COALESCE(json_extract(f.value,'$.ctx.execution_group'),json_extract(r.body,'$.execution_group'),'legacy:'||r.id),
            json_extract(f.value,'$.feed.total')-json_extract(f.value,'$.feed.next'),
            json_extract(r.body,'$.created'),r.position
            FROM stage_journal r,json_each(r.body,'$.stack') f
            WHERE r.kind='run' AND r.workspace=? AND json_extract(r.body,'$.status') NOT IN ('complete','cancelled')
            AND json_extract(f.value,'$.node.key')=? AND json_extract(f.value,'$.feed.next')<json_extract(f.value,'$.feed.total')""",(workspace,scheduler))
        for ident,remaining,created,position in feeds:
            group=groups.setdefault(ident,dict(id=ident,counts={},total=0,pending=0,created=created,position=position,label='批次供應'))
            group['pending']+=remaining;group['total']+=remaining
        settings=self.batch_controls(workspace,scheduler)
        for group in groups.values():
            control=settings.get(group['id'],{})
            group['bypassed']=bool(control.get('bypassed'));group['cancelled']=bool(control.get('cancelled'))
            if group['cancelled']:group['pending']=0
        return sorted((g for g in groups.values() if g['id']==include or g['pending'] or any(g['counts'].get(s,0) for s in (*ACTIVE,'retained'))),
                      key=lambda g:(settings.get(g['id'],{}).get('rank',1000000000000+g['position']),g['position']))

    def reorder_page(self,workspace,scheduler,group,ids,expected):
        """Permute only the visible waiting slots; never move another group.

        The full visible page is compared first so a dispatch or a concurrent
        move cannot silently turn a stale drag into a different queue order.
        """
        with self.transaction():
            actual=self.entry_summaries(workspace,scheduler,group=group,limit=len(expected['ids']),offset=expected['offset'])
            if [r['id'] for r in actual]!=expected['ids'] or [r['status'] for r in actual]!=expected['statuses']:
                raise ValueError('排程內容已更新，請重新調整。')
            waiting={r['id']:r for r in actual if r['status']=='waiting'}
            if len(ids)!=len(set(ids)) or set(ids)!=set(waiting):raise ValueError('只能拖曳尚未提交的項目。')
            owners={r['owner'] for r in waiting.values()}
            for owner in owners:
                before=[r['id'] for r in actual if r['id'] in waiting and r['owner']==owner]
                after=[key for key in ids if waiting[key]['owner']==owner]
                # Crossing scope boundaries changes execution semantics.
                if [waiting[key]['owner'] for key in ids]!=[r['owner'] for r in actual if r['id'] in waiting]:
                    raise ValueError('請在同一輪序列內調整順序。')
                positions=[self.db.execute('SELECT position FROM stage_journal WHERE id=?',(key,)).fetchone()[0] for key in before]
                for position,key in zip(positions,after):self.db.execute('UPDATE stage_journal SET position=? WHERE id=?',(position,key))

    def patch(self,ident,**values):
        """Write changed fields without decoding unchanged frozen run content."""
        if not values:return
        slots=','.join('?,json(?)' for _ in values)
        args=[part for key,value in values.items() for part in ('$.'+key,json.dumps(value,ensure_ascii=False))]
        with self.transaction():
            if not self.db.execute('UPDATE stage_journal SET body=json_set(body,'+slots+') WHERE id=?',(*args,ident)).rowcount:
                raise ValueError('執行紀錄不存在。')

    def rows(self,kind,workspace=None,owner=None,active=False):
        sql='SELECT body FROM stage_journal WHERE kind=?';args=[kind]
        for key,val in (('workspace',workspace),('owner',owner)):
            if val is not None:sql+=' AND '+key+'=?';args.append(val)
        if active:sql+=" AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')';args.extend(ACTIVE)
        return [json.loads(r[0]) for r in self.db.execute(sql+' ORDER BY position',args)]

    def read(self,ident):
        row=self.db.execute('SELECT body FROM stage_journal WHERE id=?',(ident,)).fetchone()
        return json.loads(row[0]) if row else None

    @contextmanager
    def transaction(self):
        """Accept journal rows and their document cursor as one durable write."""
        outer=self.transaction_depth==0
        with self.db if outer else nullcontext():
            self.transaction_depth+=1
            try:yield
            finally:self.transaction_depth-=1

    def add(self,kind,workspace,owner='',**values):
        item=dict(id=uuid.uuid4().hex,kind=kind,workspace=workspace,owner=owner,created=time.time(),**copy.deepcopy(values))
        with self.transaction():
            self.db.execute('INSERT INTO stage_journal VALUES (?,?,?,?,(SELECT COALESCE(MAX(position),0)+1 FROM stage_journal),?)',
                (item['id'],kind,workspace,owner,json.dumps(item,ensure_ascii=False)))
        return item

    def update(self,ident,**values):
        item=self.read(ident)
        if not item:raise ValueError('執行紀錄不存在。')
        item.update(copy.deepcopy(values))
        with self.transaction():self.db.execute('UPDATE stage_journal SET body=? WHERE id=?',(json.dumps(item,ensure_ascii=False),ident))
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
            with self.transaction():
                prior=next((r for r in self.rows('parameter_state',attempt['workspace'],attempt['stage']) if r['signature']==signature),None)
                if prior and prior.get('attempt_created',0)>attempt['created']:continue
                values=dict(signature=signature,attempt=attempt['id'],attempt_created=attempt['created'],prompt_id=job['prompt_id'],
                    used_seed=proof['actual'],status='observed',next_seed=proof.get('next_value'))
                if prior:
                    if all(prior.get(key)==value for key,value in values.items()):continue
                    if (prior.get('attempt')==attempt['id'] and prior.get('prompt_id')==job['prompt_id']
                            and prior.get('next_seed') is not None):continue
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
        with self.db:
            for row in entries:self.update(row['id'],status='waiting')
