"""Durable chain plans/items/attempts. No history pruning of dependency rows."""
import copy
import json
import time
import uuid


class ChainStore:
    def __init__(self,db):
        self.db=db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS chain_runs (id TEXT PRIMARY KEY, workspace TEXT, created REAL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS chain_items (id TEXT PRIMARY KEY, run TEXT, round INTEGER, stage INTEGER, position INTEGER, body TEXT NOT NULL, UNIQUE(run,round,stage,position))')
            db.execute('CREATE TABLE IF NOT EXISTS chain_attempts (id TEXT PRIMARY KEY, item TEXT, created REAL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS chain_results (id TEXT PRIMARY KEY, item TEXT, node TEXT, position INTEGER, body TEXT NOT NULL, UNIQUE(item,node,position))')
        for run in self.runs():
            if run['status'] not in ('complete','cancelled'):
                self.update_run(run['id'],status='paused',message='重新開啟後保留進度；確認原任務後按繼續。')

    def read(self,table,ident):
        row=self.db.execute('SELECT body FROM '+table+' WHERE id=?',(ident,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self,table,ident,**changes):
        value=self.read(table,ident)
        if value is None:raise ValueError('串接紀錄不存在。')
        value.update(copy.deepcopy(changes))
        with self.db:self.db.execute('UPDATE '+table+' SET body=? WHERE id=?',(json.dumps(value,ensure_ascii=False),ident))
        return value

    def runs(self,workspace=None):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM chain_runs'+(' WHERE workspace=?' if workspace else '')+' ORDER BY created DESC',(workspace,) if workspace else ())]

    def create(self,workspace,server,plan,rounds,entries,claimed=()):
        from .chain_model import revision
        run=dict(id=uuid.uuid4().hex,workspace=workspace,server=server,plan=copy.deepcopy(plan),revision=revision(plan),rounds=rounds,round=1,stage=0,status='running',message='',created=time.time())
        with self.db:
            self.db.execute('INSERT INTO chain_runs VALUES (?,?,?,?)',(run['id'],workspace,run['created'],json.dumps(run,ensure_ascii=False)))
            # Entry references contain no decoded images. All other stages are
            # expanded transactionally once their predecessor is fully ready.
            for number in range(1,rounds+1):
                for i,entry in enumerate(entries):self.ensure_item(run['id'],number,0,i,entry)
            # A queue entry and its chain root are claimed in one transaction;
            # a crash cannot leave the same input dispatchable in two runners.
            for row in claimed:
                current=self.read('input_queue',row['id'])
                if current['state']!='waiting':raise ValueError('入口預排程已更新，請重新開始。')
                current.update(state='chain',chain_run=run['id'])
                self.db.execute('UPDATE input_queue SET body=? WHERE id=?',(json.dumps(current,ensure_ascii=False),row['id']))
        return run

    def update_run(self,ident,**changes):return self.put('chain_runs',ident,**changes)
    def update_item(self,ident,**changes):return self.put('chain_items',ident,**changes)

    def attempts(self,item):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM chain_attempts WHERE item=? ORDER BY created',(item,))]

    def ensure_item(self,run,round,stage,position,entry):
        item=dict(id=uuid.uuid4().hex,run=run,round=round,stage=stage,position=position,status='waiting',entry=copy.deepcopy(entry),attempt=None,error='')
        self.db.execute('INSERT OR IGNORE INTO chain_items VALUES (?,?,?,?,?,?)',(item['id'],run,round,stage,position,json.dumps(item,ensure_ascii=False)))

    def items(self,run,round=None,stage=None):
        clause='run=?';args=[run]
        if round is not None:clause+=' AND round=?';args.append(round)
        if stage is not None:clause+=' AND stage=?';args.append(stage)
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM chain_items WHERE '+clause+' ORDER BY round,stage,position',args)]

    def results(self,item):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM chain_results WHERE item=? ORDER BY position',(item,))]

    def save_result(self,item,node,index,source,reference):
        value=dict(id=item+':'+node+':'+str(index),item=item,node=node,index=index,source=source,reference=reference)
        with self.db:self.db.execute('INSERT OR IGNORE INTO chain_results VALUES (?,?,?,?,?)',(value['id'],item,node,index,json.dumps(value,ensure_ascii=False)))
        return value

    def attempt(self,item,prepared):
        ident=uuid.uuid4().hex
        value=dict(id=ident,item=item['id'],created=time.time(),prepared=copy.deepcopy(prepared))
        with self.db:
            self.db.execute('INSERT INTO chain_attempts VALUES (?,?,?,?)',(ident,item['id'],value['created'],json.dumps(value,ensure_ascii=False)))
            self.db.execute('UPDATE chain_items SET body=? WHERE id=?',(json.dumps(dict(item,status='preparing',attempt=ident,error=''),ensure_ascii=False),item['id']))
        return ident

    def expand(self,run,entries):
        with self.db:
            for i,entry in enumerate(entries):self.ensure_item(run['id'],run['round'],run['stage']+1,i,entry)
            run=dict(run,stage=run['stage']+1)
            self.db.execute('UPDATE chain_runs SET body=? WHERE id=?',(json.dumps(run,ensure_ascii=False),run['id']))
        return run
