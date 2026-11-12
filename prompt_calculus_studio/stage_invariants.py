"""Cold, operation-scoped journal repair. Never submits, retries or pumps."""
import copy
import hashlib

from .comfy_event_store import EventStore
from .comfy_event_protocol import encode


def receipt(attempt, action):
    # Local receipts do not impersonate a server epoch, nor enter Canvas Undo.
    return dict(server_instance='local', epoch='stage-continuation-v1',
                seq=attempt['id']+':'+action, entity='attempt:'+attempt['id'],
                kind='local.invariant', receipt_revision=0,
                payload=dict(owner=attempt['owner'], action=action))


def continue_result(runner, attempt, result, *, repair=False):
    """Called inside the same accepting transaction as result/job acceptance."""
    events=EventStore(runner.store.db)
    event=receipt(attempt,'continue')
    current=runner.store.read(attempt['owner'])
    frame=next((f for f in current['stack'] if f.get('job')==attempt['id']),None)
    if events.contains(event):
        if frame and current['status']!='cancelled':raise ValueError('資料不一致')
        return current,frame
    if frame:
        if current['status']!='cancelled':
            from .stage_runner import merge_results
            merge_results(frame['produced'],{attempt['stage']:result})
            frame['index']+=1
            autos={key for s in current['plan']['stages'].values() if s['auto'] for key in (s.get('suppliers') or [s['auto']])}
            for key,value in attempt.get('sources',{}).items():
                if key not in autos:current.setdefault('source_positions',{}).setdefault(key,value)
        frame['job']=None
        runner.save(current)
    elif repair:
        return current,None
    events.accept(event,'local_continuation')
    return current,frame


class InvariantChecker:
    def __init__(self, client):
        self.client=client
        self.runner=client.input_flow.chain
        self.store=self.runner.store
        self.receipts=EventStore(self.store.db)
        self.fault=None

    def candidates(self, owner=None):
        # Header-only queries use the existing active/owner indexes. Frozen
        # captures and retained queue rows are decoded only for a suspect ID.
        args=[]
        owned=''
        if owner is not None:owned=' AND owner=?';args.append(owner)
        values={r[0] for r in self.store.db.execute("SELECT id FROM stage_header WHERE kind='attempt' AND json_extract(body,'$.status') IN ('preparing','submitted','unconfirmed','result_error')"+owned,args)}
        args=[]
        owned=''
        if owner is not None:owned=' AND r.id=?';args.append(owner)
        values.update(r[0] for r in self.store.db.execute("SELECT json_extract(f.value,'$.job') FROM stage_header r,json_each(r.body,'$.stack') f WHERE r.kind='run'"+owned+" AND json_extract(f.value,'$.job') IS NOT NULL",args))
        return values

    def check(self, ident=None, *, owner=None, notify=True):
        for key in ([ident] if ident is not None else self.candidates(owner)):
            self.operation(key,notify=notify)

    def operation(self, ident, *, notify=True):
        # The healthy path needs only the operation and run headers. Do not
        # inflate a captured scene merely to discover that nothing is wrong.
        head=self.store.db.execute("SELECT owner,json_extract(body,'$.status'),json_extract(body,'$.local_origin') FROM stage_header WHERE id=? AND kind='attempt'",(ident,)).fetchone()
        if not head:return
        job=self.client.generation.record(ident)
        if job and (head[2] or head[1])!='complete':
            status=self.store.status_value(head[0])
            if status!='cancelled' or job['state'] not in ('complete','failed','retired'):return
        attempt=self.store.read(ident)
        if not attempt or attempt.get('kind')!='attempt':return
        runner=self.runner
        if ident==runner.preparing or ident in runner.collecting:return
        run=self.store.read(attempt['owner'])
        if not run:return
        origin=attempt.get('local_origin',attempt['status'])
        frame=next((f for f in run['stack'] if f.get('job')==ident),None)
        if origin=='complete' and frame:
            with runner.accepting():
                attempt=self.store.read(ident)
                result=attempt.get('result')
                if not self.complete_result(attempt,result):
                    self.issue(attempt,'結果待確認',notify=notify);return
                if self.receipts.contains(receipt(attempt,'continue')) and run['status']!='cancelled':
                    self.issue(attempt,'資料不一致',notify=notify);return
                continue_result(runner,attempt,result,repair=True)
                self.store.patch(ident,status='complete',error='',local_issue='',local_origin='complete')
                if self.fault:self.fault('before_repair_commit')
                if notify:self.store._transactions.after_commit(runner.notify)
            return
        if job:
            # S9b: terminal jobs remain discoverable even after their run left
            # open_runs. Clear only a confirmed terminal's cancelled lane.
            if run['status']=='cancelled' and job['state'] in ('complete','failed','retired') and (frame or attempt.get('cancel_requested')):
                event=receipt(attempt,'cancelled-lane')
                with runner.accepting():
                    if not self.receipts.contains(event):
                        if frame:frame['job']=None;runner.save(run)
                        self.store.patch(ident,cancel_requested=False,status='complete' if job['state']=='complete' and attempt.get('result') else 'cancelled')
                        self.receipts.accept(event,'local_cancelled_lane')
                        if self.fault:self.fault('before_repair_commit')
                        if notify:self.store._transactions.after_commit(runner.notify)
            return
        if origin not in ('preparing','submitted','unconfirmed','result_error'):return
        evidence=attempt.get('operation_evidence')
        if self.owned_payload(attempt,run,evidence):
            candidate=copy.deepcopy(evidence)
            # The accepted payload proves identity, not today's remote outcome.
            candidate.update(state='unconfirmed',error='無法確認')
            event=receipt(attempt,'restore:'+hashlib.sha256(encode(evidence)).hexdigest())
            with runner.accepting():
                if self.receipts.contains(event):
                    self.issue(attempt,'資料不一致',notify=notify);return
                self.client.generation._write_job(candidate)
                self.store.patch(ident,status='unconfirmed',error='無法確認',local_issue='')
                self.receipts.accept(event,'local_projection')
                if self.fault:self.fault('before_repair_commit')
                self.store._transactions.after_commit(lambda:self.client.generation.jobs.__setitem__(ident,candidate))
                if notify:self.store._transactions.after_commit(runner.notify)
            return
        unsent=origin=='preparing' and attempt.get('submission_intent')=='not_sent'
        self.issue(attempt,'資料不一致' if origin=='submitted' else '無法確認',unsent=unsent,notify=notify)

    @staticmethod
    def owned_payload(attempt,run,evidence):
        return (isinstance(evidence,dict) and evidence.get('id')==attempt['id']
            and evidence.get('native_operation')==attempt['id'] and evidence.get('server')==run['server']
            and isinstance(evidence.get('created'),(int,float)) and isinstance(evidence.get('chain'),dict)
            and evidence.get('workspace')==attempt['workspace'] and evidence.get('chain',{}).get('run')==run['id']
            and evidence.get('chain',{}).get('attempt')==attempt['id'] and bool(evidence.get('prompt_id'))
            and isinstance(evidence.get('payload'),dict)
            and isinstance(evidence.get('payload',{}).get('prompt'),dict) and bool(evidence['payload']['prompt']))

    def complete_result(self,attempt,result):
        if not isinstance(result,dict) or result.get('run')!=attempt['owner'] or result.get('attempts')!=[attempt['id']]:return False
        if not isinstance(result.get('images'),list) or not isinstance(result.get('records'),list):return False
        if not all(isinstance(r,dict) for r in result['records']):return False
        if not any(r.get('attempt')==attempt['id'] for r in result['records']):return False
        evidence=attempt.get('operation_evidence') or {}
        from .composition_image import source_path
        try:
            for image in result['images']:
                if image.get('reference',{}).get('attempt')!=attempt['id']:return False
                if evidence.get('prompt_id') and image['reference'].get('prompt_id')!=evidence['prompt_id']:return False
                source_path(self.runner.window.store.directory,image,verify=True)
            if 'outputs' in evidence:
                expected={(str(node),i) for node,output in evidence['outputs'].items()
                    if str(node) in evidence['payload']['prompt'] for i,_ in enumerate(output.get('images',[]))}
                actual={(str(image['reference']['node']),image['reference']['index']) for image in result['images']}
                if len(result['images'])!=len(expected) or actual!=expected:return False
        except (OSError,ValueError,KeyError,TypeError,AttributeError):return False
        return 'text' in result and isinstance(result.get('texts'),list)

    def issue(self,attempt,message,*,unsent=False,notify=True):
        status='failed' if unsent else 'unconfirmed'
        if unsent:message='資料不一致'
        if attempt.get('local_issue')==message and attempt['status']==status:return
        event=receipt(attempt,'issue:'+status+':'+message)
        with self.runner.accepting():
            if self.receipts.contains(event):return
            self.store.patch(attempt['id'],status=status,error=message,local_issue=message,
                             local_origin=attempt.get('local_origin',attempt['status']))
            run=self.store.read(attempt['owner'])
            if run['status'] not in ('cancelled','complete'):
                self.store.patch(run['id'],status='paused',message=message,
                    pause_reason=run.get('pause_reason') if run.get('pause_reason') in ('manual','restart') else 'error')
            self.receipts.accept(event,'local_issue')
            if self.fault:self.fault('before_repair_commit')
            if notify:self.store._transactions.after_commit(self.runner.notify)
