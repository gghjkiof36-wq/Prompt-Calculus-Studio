"""Scoped configuration-independent records over the existing desktop database."""
import copy
import json
import time
import uuid

from . import journal_codec as codec

ACTIVE=('waiting','running','preparing','submitted','collecting','failed','unconfirmed','result_error','results_pending')


class StageStore:
    def __init__(self,db):
        from .core import _Transactions
        self.db=db
        # Storage-owned connections share depth across every StageStore. Plain
        # SQLite connections (standalone journal tests/previews) keep a local owner.
        self._transactions=getattr(db,'_transactions',None) or _Transactions(db)
        from .storage_layout import initialize
        initialize(db)
        for run in self.open_runs():
            if (run['status']=='paused' and run.get('pause_reason') not in ('restart','disconnect')
                    or self.can_reconnect(run)):
                continue
            self.update(run['id'],status='paused',pause_reason='restart',message='重新開啟後保留進度，請從紀錄繼續。')

    def can_reconnect(self,run):
        if run['status']!='running' or not run.get('stack'):
            return False
        frame=run['stack'][-1]
        if (run.get('await_channel') and len(run['stack'])==1 and frame.get('kind')=='seq'
                and frame.get('index')==0 and run.get('round')==1):
            return True  # The original click is frozen and has not entered its first stage.
        attempt=frame.get('job')
        # An issued frame waits for the same attempt; only its accepted result advances it.
        return bool(attempt and self.db.execute(
            """SELECT 1 FROM stage_header WHERE id=? AND kind='attempt' AND owner=?
            AND json_extract(body,'$.result_delivery')=1
            AND COALESCE(json_extract(body,'$.cancel_requested'),0)=0""",
            (attempt,run['id'])).fetchone())

    def open_runs(self,workspace=None):
        sql="SELECT "+codec.RECORD_COLUMNS+" FROM stage_header INDEXED BY stage_header_open_runs WHERE kind='run' AND json_extract(body,'$.status') NOT IN ('complete','cancelled')"
        args=[]
        if workspace is not None:sql+=' AND workspace=?';args.append(workspace)
        return codec.records(self.db,self.db.execute(sql+' ORDER BY position',args))

    def has_open_runs(self,workspace):
        return self.db.execute("SELECT 1 FROM stage_header WHERE kind='run' AND workspace=? AND json_extract(body,'$.status') NOT IN ('complete','cancelled') LIMIT 1",(workspace,)).fetchone() is not None

    def progress_runs(self,workspace):
        """Read progress fields, not the frozen scenes inside every frame."""
        rows=self.db.execute("""SELECT json_extract(r.body,'$.id','$.server','$.plan','$.status','$.message'),
            (SELECT json_group_array(json_object('kind',json_extract(f.value,'$.kind'),
                'key',json_extract(f.value,'$.key'),'owner',json_extract(f.value,'$.owner'),
                'done',json_extract(f.value,'$.done'),'feed',CASE WHEN json_type(f.value,'$.feed')='object' AND json_extract(f.value,'$.feed')!='{}'
                    THEN json_object('total',json_extract(f.value,'$.feed.total')) ELSE NULL END))
             FROM json_each(r.body,'$.stack') f)
            FROM stage_header r WHERE kind='run' AND workspace=?
            AND json_extract(body,'$.status') NOT IN ('complete','cancelled') ORDER BY position""",(workspace,))
        result=[]
        for header,frames in rows:
            ident,server,plan,status,message=json.loads(header)
            result.append(dict(id=ident,server=server,plan=plan,status=status,message=message,stack=json.loads(frames)))
        return result

    def entry_count(self,owner):
        return self.db.execute("SELECT COUNT(*) FROM stage_header WHERE kind='entry' AND owner=?",(owner,)).fetchone()[0]

    def attempt_heads(self):
        """Polling needs ownership/status; capture is read only on completion or seeds."""
        rows=self.db.execute("""SELECT json_extract(a.body,'$.id','$.workspace','$.owner','$.stage','$.status',
                '$.created','$.round','$.parent','$.cancel_requested'),
            EXISTS(SELECT 1 FROM json_each(a.projection,'$.parameter_stages') s
                WHERE s.value=json_extract(a.body,'$.stage')),
            (SELECT json_extract(s.value,'$.error') FROM json_each(a.projection,'$.parameter_errors') s
                WHERE json_extract(s.value,'$.stage')=json_extract(a.body,'$.stage') LIMIT 1)
            FROM stage_header a WHERE kind='attempt' AND json_extract(body,'$.status') IN ("""+
            ','.join('?' for _ in ACTIVE)+') ORDER BY position',ACTIVE)
        fields=('id','workspace','owner','stage','status','created','round','parent','cancel_requested')
        result=[]
        for header,parameters,error in rows:
            codec.raise_projection_error(error)
            result.append(dict(zip(fields,json.loads(header)),has_parameters=bool(parameters)))
        return result

    def latest_stage_status(self,workspace,stage):
        """Paint status without fetching or sorting complete prompt/image snapshots."""
        row=self.db.execute("""SELECT json_extract(body,'$.status'),json_extract(body,'$.error')
            FROM stage_header WHERE kind='attempt' AND workspace=? AND json_extract(body,'$.stage')=?
            ORDER BY position DESC LIMIT 1""",(workspace,stage)).fetchone()
        return dict(status=row[0],error=row[1] or '') if row else None

    def status_value(self,ident):
        row=self.db.execute("SELECT json_extract(body,'$.status') FROM stage_header WHERE id=?",(ident,)).fetchone()
        return row[0] if row else None

    def latest_entry_attempt(self,workspace,ident):
        """Read only the latest descendant attempt, including nested scopes."""
        # Parent-first traversal and an affinity-free bound keep both lookups
        # on the parent index, rather than scanning a workspace's history.
        row=self.db.execute("""WITH RECURSIVE descendants(id) AS (
                VALUES (?) UNION
                SELECT e.id FROM descendants d CROSS JOIN stage_header e
                    ON json_extract(e.body,'$.parent')=+d.id
                    WHERE e.kind='entry' AND e.workspace=?
            ), latest(id) AS (
                SELECT (SELECT a.id FROM stage_header a
                    WHERE a.kind='attempt' AND a.workspace=? AND json_extract(a.body,'$.parent')=+d.id
                    ORDER BY a.position DESC LIMIT 1) FROM descendants d
            )
            SELECT json_extract(body,'$.status'),json_extract(body,'$.cancel_requested')
            FROM stage_header WHERE id=(SELECT a.id FROM latest
                JOIN stage_header a ON a.id=latest.id ORDER BY a.position DESC LIMIT 1)""",
            (ident,workspace,workspace)).fetchone()
        return dict(status=row[0],cancel_requested=row[1]) if row else None

    def entry_states(self,workspace,scheduler):
        """Minimal ownership/state projection for a collapsed schedule card."""
        query="SELECT id,json_extract(body,'$.run'),json_extract(body,'$.status'),COALESCE(json_extract(body,'$.execution_group'),'legacy:'||json_extract(body,'$.run')) FROM stage_header INDEXED BY stage_header_entry_display WHERE kind='entry' AND workspace=? AND json_extract(body,'$.scheduler')=? AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+') ORDER BY position'
        return [dict(id=ident,workspace=workspace,run=run,status=status,execution_group=group)
                for ident,run,status,group in self.db.execute(query,(workspace,scheduler,*ACTIVE))]

    def entry_summaries(self,workspace,scheduler,*,group=None,limit=None,offset=0):
        """List display fields, leaving frozen graphs/text for explicit detail reads."""
        query="""SELECT id,workspace,owner,
            json_extract(body,'$.run','$.scheduler','$.parent','$.status','$.label'),
            json_extract(projection,'$.image'),json_extract(projection,'$.image_count'),json_extract(projection,'$.error')
            FROM stage_header WHERE kind='entry' AND workspace=? AND json_extract(body,'$.scheduler')=?"""
        args=[workspace,scheduler]
        if group is None:
            query+=" AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')';args.extend(ACTIVE)
        else:
            query+=" AND COALESCE(json_extract(body,'$.execution_group'),'legacy:'||json_extract(body,'$.run'))=?";args.append(group)
        query+=' ORDER BY position'
        if limit is not None:query+=' LIMIT ? OFFSET ?';args.extend((limit,offset))
        result=[]
        for row in self.db.execute(query,args):
            codec.raise_projection_error(row[6])
            run,key,parent,status,label=json.loads(row[3])
            image=json.loads(row[4]) if row[4] is not None else None
            count=row[5]
            result.append(dict(id=row[0],workspace=row[1],owner=row[2],run=run,scheduler=key,
                               parent=parent,status=status,label=label,image=image,image_count=count))
        return result

    def active_count(self,workspace,scheduler,owner=None):
        query="SELECT COUNT(*) FROM stage_header WHERE kind='entry' AND workspace=? AND json_extract(body,'$.scheduler')=? AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')'
        args=[workspace,scheduler,*ACTIVE]
        if owner is not None:query+=' AND owner=?';args.append(owner)
        return self.db.execute(query,args).fetchone()[0]

    def entry_descendants(self,workspace,ids):
        rows=self.db.execute("""WITH RECURSIVE descendants(id) AS (
            SELECT value FROM json_each(?) UNION
            SELECT e.id FROM descendants d CROSS JOIN stage_header e
            ON json_extract(e.body,'$.parent')=+d.id WHERE e.kind='entry' AND e.workspace=?)
            SELECT e.id,json_extract(e.body,'$.status','$.run','$.parent','$.scheduler')
            FROM descendants d JOIN stage_header e ON e.id=d.id WHERE e.kind='entry' AND e.workspace=?""",(json.dumps(list(ids)),workspace,workspace))
        result=[]
        for ident,raw in rows:
            status,run,parent,scheduler=json.loads(raw)
            result.append(dict(id=ident,status=status,run=run,parent=parent,scheduler=scheduler))
        return result

    def entry_attempts(self,workspace,ids,*,active=False):
        query="SELECT "+codec.RECORD_COLUMNS+" FROM stage_header WHERE kind='attempt' AND workspace=? AND json_extract(body,'$.parent') IN (SELECT value FROM json_each(?))"
        args=[workspace,json.dumps(list(ids))]
        if active:query+=" AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')';args.extend(ACTIVE)
        return codec.records(self.db,self.db.execute(query+' ORDER BY position',args))

    def next_entry(self,owner):
        query="SELECT "+codec.record_columns('e')+""" FROM stage_header e LEFT JOIN stage_header b
            ON b.kind='batch_control' AND b.workspace=e.workspace AND b.owner=json_extract(e.body,'$.scheduler')
            AND json_extract(b.body,'$.group')=COALESCE(json_extract(e.body,'$.execution_group'),'legacy:'||json_extract(e.body,'$.run'))
            WHERE e.kind='entry' AND e.owner=? AND COALESCE(json_extract(b.body,'$.bypassed'),0)=0
            AND COALESCE(json_extract(b.body,'$.cancelled'),0)=0 AND json_extract(e.body,'$.status') IN ("""+','.join('?' for _ in ACTIVE)+") ORDER BY COALESCE(json_extract(b.body,'$.rank'),1000000000000+CASE WHEN json_type(b.body,'$.serial') IS NOT NULL THEN b.position ELSE e.position END),e.position LIMIT 1"
        row=self.db.execute(query,(owner,*ACTIVE)).fetchone()
        return codec.records(self.db,[row])[0] if row else None

    def batch_controls(self,workspace,scheduler):
        records=(json.loads(r[0]) for r in self.db.execute("SELECT body FROM stage_header WHERE kind='batch_control' AND workspace=? AND owner=?",(workspace,scheduler)))
        return {r['group']:r for r in records}

    def batch_control(self,workspace,scheduler,group):
        row=self.db.execute("SELECT body FROM stage_header WHERE kind='batch_control' AND workspace=? AND owner=? AND json_extract(body,'$.group')=?",(workspace,scheduler,group)).fetchone()
        return json.loads(row[0]) if row else {}

    def next_batch_serial(self,workspace,scheduler):
        row=self.db.execute("SELECT COALESCE(MAX(json_extract(body,'$.serial')),0)+1 FROM stage_header WHERE kind='batch_control' AND workspace=? AND owner=?",(workspace,scheduler)).fetchone()
        return row[0]

    def batch_heads(self,workspace,owner=None):
        query="""SELECT id,owner,json_extract(body,'$.run','$.scheduler','$.execution_group','$.total','$.expanded','$.deferred')
            FROM stage_header WHERE kind='batch_snapshot' AND workspace=?"""
        args=[workspace]
        if owner is not None:query+=" AND (owner=? OR EXISTS(SELECT 1 FROM json_each(projection,'$.segments') WHERE json_extract(value,'$.owner')=?))";args.extend((owner,owner))
        return [dict(id=ident,owner=owned,**dict(zip(('run','scheduler','execution_group','total','expanded','deferred'),json.loads(raw))))
                for ident,owned,raw in self.db.execute(query+' ORDER BY position',args)]

    def delete_batch_snapshot(self,ident):
        with self.transaction():
            if self.db.execute("SELECT 1 FROM stage_header WHERE kind='batch_snapshot' AND id=?",(ident,)).fetchone():
                codec.delete(self.db,ident)

    def pending_batches(self,owner):
        return self.db.execute("""SELECT 1 FROM stage_header WHERE kind='batch_snapshot' AND
            ((owner=? AND json_type(projection,'$.segments') IS NULL) OR EXISTS(SELECT 1 FROM json_each(projection,'$.segments') WHERE json_extract(value,'$.owner')=?
                AND json_extract(value,'$.start')+json_extract(value,'$.total')>json_extract(body,'$.expanded'))) LIMIT 1""",(owner,owner)).fetchone() is not None

    def preferred_entry(self,workspace,scheduler):
        """Resolve pending group priority across run boundaries, after live work."""
        if not self.db.execute("SELECT 1 FROM stage_header WHERE kind='batch_control' AND workspace=? AND owner=? LIMIT 1",(workspace,scheduler)).fetchone():return None
        row=self.db.execute("""SELECT e.id,e.owner,json_extract(e.body,'$.run') FROM stage_header e
            JOIN stage_header r ON r.id=json_extract(e.body,'$.run')
            LEFT JOIN stage_header b ON b.kind='batch_control' AND b.workspace=e.workspace AND b.owner=?
                AND json_extract(b.body,'$.group')=COALESCE(json_extract(e.body,'$.execution_group'),'legacy:'||r.id)
            WHERE e.kind='entry' AND e.workspace=? AND json_extract(e.body,'$.scheduler')=?
                AND (json_extract(r.body,'$.status') IN ('running','waiting') OR
                     (json_extract(e.body,'$.status')='running' AND json_extract(b.body,'$.cancelled')=1))
                AND json_extract(e.body,'$.status') IN ('waiting','running')
                AND (json_extract(e.body,'$.status')='running' OR
                     (COALESCE(json_extract(b.body,'$.bypassed'),0)=0 AND COALESCE(json_extract(b.body,'$.cancelled'),0)=0))
            ORDER BY (json_extract(e.body,'$.status')='running') DESC,
                COALESCE(json_extract(b.body,'$.rank'),1000000000000+CASE WHEN json_type(b.body,'$.serial') IS NOT NULL THEN b.position ELSE e.position END),e.position LIMIT 1""",(scheduler,workspace,scheduler)).fetchone()
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
        return [r[0] for r in self.db.execute("SELECT id FROM stage_header WHERE kind='entry' AND owner=? AND json_extract(body,'$.status')='waiting' ORDER BY position",(owner,))]

    def scheduler_runs(self,workspace,scheduler):
        query="""SELECT DISTINCT json_extract(body,'$.run') FROM stage_header WHERE kind='entry' AND workspace=?
            AND json_extract(body,'$.scheduler')=? AND json_extract(body,'$.status') IN ('waiting','running','retained')
            UNION SELECT id FROM stage_header r WHERE kind='run' AND workspace=?
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
            FROM stage_header INDEXED BY stage_header_entry_display WHERE kind='entry' AND workspace=? AND json_extract(body,'$.scheduler')=?
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
            FROM stage_header r,json_each(r.body,'$.stack') f
            WHERE r.kind='run' AND r.workspace=? AND json_extract(r.body,'$.status') NOT IN ('complete','cancelled')
            AND json_extract(f.value,'$.node.key')=? AND json_extract(f.value,'$.feed.next')<json_extract(f.value,'$.feed.total')""",(workspace,scheduler))
        for ident,remaining,created,position in feeds:
            group=groups.setdefault(ident,dict(id=ident,counts={},total=0,pending=0,created=created,position=position,label='批次供應'))
            group['pending']+=remaining;group['total']+=remaining
        settings=self.batch_controls(workspace,scheduler)
        for ident,control in settings.items():
            if 'total' not in control or control.get('cancelled'):continue
            remaining=control['total']-control['expanded']
            if not remaining:continue
            group=groups.setdefault(ident,dict(id=ident,counts={},total=0,pending=0,created=control['created'],
                position=self.db.execute('SELECT position FROM stage_header WHERE id=?',(control['id'],)).fetchone()[0],label=control.get('label','執行')))
            group['pending']+=remaining;group['total']+=remaining
        for group in groups.values():
            control=settings.get(group['id'],{})
            group['serial']=control.get('serial')
            if group['serial']:
                group['position']=self.db.execute('SELECT position FROM stage_header WHERE id=?',(control['id'],)).fetchone()[0]
                group['created']=control['created']
            group['bypassed']=bool(control.get('bypassed'));group['cancelled']=bool(control.get('cancelled'))
            if group['cancelled']:group['pending']=0
            group['unexpanded']=group['pending']
            group['pending']+=group['counts'].get('waiting',0)+group['counts'].get('retained',0)
        return sorted((g for g in groups.values() if not settings.get(g['id'],{}).get('withdrawn') and (g['id']==include or g['pending'] or any(g['counts'].get(s,0) for s in (*ACTIVE,'retained')))),
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
                positions=[self.db.execute('SELECT position FROM stage_header WHERE id=?',(key,)).fetchone()[0] for key in before]
                for position,key in zip(positions,after):self.db.execute('UPDATE stage_header SET position=? WHERE id=?',(position,key))

    def patch(self,ident,*,_prepared=None,**values):
        """Replace only changed roots; unchanged content never gets decoded."""
        if not values:return
        with self.transaction():
            row=self.db.execute('SELECT id,kind,workspace,owner,position,body,projection,content_paths FROM stage_header WHERE id=?',(ident,)).fetchone()
            if row is None:raise ValueError('執行紀錄不存在。')
            header=json.loads(row[5]);projected=json.loads(row[6]);refs=json.loads(row[7])
            if header.get('kind')=='entry' and ('saved' in values or 'source' in values):
                item=self.read(ident);item.update(values)
                codec.write(self.db,item,row[4],columns=row[:4])
                return
            partial=dict(id=ident,kind=header.get('kind'));partial.update(values)
            part,derived,new_refs,content=_prepared if _prepared is not None else codec.split(self.db,partial)
            for key in values:
                header[key]=part[key]
                prefix=codec.pointer((key,))
                refs={path:digest for path,digest in refs.items() if path!=prefix and not path.startswith(prefix+'/')}
            refs.update(new_refs)
            if header.get('kind')=='attempt' and 'snapshot_state' in values:projected.pop('error',None)
            projected.update(derived)
            codec.write_parts(self.db,header,projected,refs,content,row[4],columns=row[:4])

    def replace_record(self,body,position):
        """Restore an owned complete record, including its independent SQL slot."""
        with self.transaction():
            columns=self.db.execute('SELECT id,kind,workspace,owner FROM stage_header WHERE id=?',(body['id'],)).fetchone()
            if columns is None:raise ValueError('執行紀錄不存在。')
            codec.write(self.db,body,position,columns=columns)

    def rows(self,kind,workspace=None,owner=None,active=False):
        sql='SELECT '+codec.RECORD_COLUMNS+' FROM stage_header WHERE kind=?';args=[kind]
        for key,val in (('workspace',workspace),('owner',owner)):
            if val is not None:sql+=' AND '+key+'=?';args.append(val)
        if active:sql+=" AND json_extract(body,'$.status') IN ("+','.join('?' for _ in ACTIVE)+')';args.extend(ACTIVE)
        return codec.records(self.db,self.db.execute(sql+' ORDER BY position',args))

    def read(self,ident):
        row=self.db.execute('SELECT '+codec.RECORD_COLUMNS+' FROM stage_header WHERE id=?',(ident,)).fetchone()
        return codec.records(self.db,[row])[0] if row else None

    @property
    def transaction_depth(self):
        return self._transactions.depth

    def transaction(self):
        """Accept journal rows and their document cursor as one durable write."""
        return self._transactions.transaction()

    def add(self,kind,workspace,owner='',*,ident=None,_captured=False,_return_record=True,**values):
        item=dict(id=ident or uuid.uuid4().hex,kind=kind,workspace=workspace,owner=owner,created=time.time(),**values)
        with self.transaction():
            if ident and self.db.execute('SELECT 1 FROM stage_header WHERE id=?',(ident,)).fetchone():
                raise ValueError('資料不一致')
            position=self.db.execute('SELECT COALESCE(MAX(position),0)+1 FROM stage_header').fetchone()[0]
            codec.write(self.db,item,position)
        return self.read(item['id']) if _return_record else item['id']

    def add_captured(self,kind,workspace,owner='',*,_return_record=True,**values):
        """Freeze borrowed JSON synchronously; never retain a borrowed object."""
        return self.add(kind,workspace,owner,_captured=True,_return_record=_return_record,**values)

    def update(self,ident,**values):
        self.patch(ident,**values)
        item=self.read(ident)
        if item is None:raise ValueError('執行紀錄不存在。')
        return item

    def reorder(self,owner,ids):
        rows=self.rows('entry',owner=owner,active=True);waiting=[i for i in rows if i['status']=='waiting']
        if len(ids)!=len(set(ids)) or set(ids)!={i['id'] for i in waiting}:raise ValueError('只能拖曳尚未提交的項目。')
        with self.transaction():
            positions=sorted(self.db.execute('SELECT position FROM stage_header WHERE id=?',(i['id'],)).fetchone()[0] for i in waiting)
            for index,key in zip(positions,ids):self.db.execute('UPDATE stage_header SET position=? WHERE id=?',(index,key))

    def edit(self,ident,saved,expected_saved=None):
        item=self.read(ident)
        if not item or item['status']!='waiting':raise ValueError('已提交的項目僅供查看。')
        if expected_saved is not None and item['saved']!=expected_saved:raise ValueError('這一項已被修改，請重新開啟後比較。')
        self.update(ident,saved=saved)

    def edit_parameters(self,ident,stage,config,expected_saved,profile=None):
        """Compare-and-save one waiting item's parameters, never its live Stage."""
        from .stage_parameters import validate
        validate(config)
        with self.transaction():
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
        with self.transaction():
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
        with self.transaction():
            for row in entries:self.update(row['id'],status='waiting')
