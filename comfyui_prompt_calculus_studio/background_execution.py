"""Bind confirmed-close state to the existing PCS operation and Comfy prompt."""
import copy
import json
import time
import uuid
from .native_queue import digest
from .workflow_state import matches, server_identity
from .shared.multi_output import bound_texts
from .shared.snapshots import validate_snapshot


class CaptureRejected(ValueError):
    def __init__(self,message,operation_id):
        super().__init__(message);self.operation_id=operation_id


class BackgroundExecution:
    def __init__(self,service,events):
        self.service=service;self.core=service.background;self.events=events

    def scope(self,identity,server):
        library=self.service.read_library(include_connection=True)
        connection=library['connection'];state=library['state']
        if not connection.get('enabled') or server_identity(connection.get('server',''))!=server_identity(server):
            raise ValueError('PCS 尚未連線至這個 ComfyUI。')
        candidates=[p for p in state.get('generation',{}).get('profiles',[])
            if p.get('frontend_id')==identity.get('frontend_id') and p.get('origin',{}).get('path')
            and (not identity.get('path') or p['origin']['path']==identity['path'])
            and server_identity(p.get('origin',{}).get('server',''))==server_identity(server)]
        if len(candidates)!=1:raise ValueError('請在 PCS 綁定唯一的原生工作流。')
        profile=candidates[0]
        key=dict(library_id=library['library_id'],workspace_id=state['workspace'],workflow_id=profile['id'],
                 frontend_id=profile['frontend_id'],path=profile['origin']['path'])
        return key,library,profile

    def checked_key(self,value,server):
        key=value.get('key')
        if not isinstance(key,dict):raise ValueError('背景工作流身分無效。')
        actual,library,profile=self.scope(key,server)
        if actual!=key:raise ValueError('PCS 素材庫、工作區或工作流已切換。')
        return key,library,profile

    def context(self,value,server):
        identity=value.get('identity',{})
        if not isinstance(identity,dict):raise ValueError('工作流身分無效。')
        if not identity.get('frontend_id'):return {}
        try:key,library,profile=self.scope(identity,server)
        except ValueError:
            # An unbound native workflow may open normally; no headless lease
            # is acquired and it cannot be mistaken for the bound graph.
            return {}
        result=dict(key=key,server_epoch=self.core.server_epoch,revision=0)
        try:snapshot=self.core.snapshot(dict(key=key))
        except ValueError as error:
            if str(error)=='background_state_missing':return result
            raise
        result.update(revision=snapshot['revision'],digest=snapshot['digest'],phase=snapshot['phase'])
        result.update({k:snapshot.get(k) for k in ('capture_op_id','release_op_id','advance_op_id','advance_from_revision','edit_seq')})
        if snapshot['phase'] in ('unknown','uncertain') or snapshot.get('unresolved'):
            raise ValueError('背景工作流狀態尚未確認，PCS 執行已鎖定；不會改用磁碟上的舊工作流。')
        if value.get('cold') and snapshot['phase']!='released':
            raise ValueError('上次網頁未完成正常關頁交接，請核對最新工作流；不會載入保存的舊副本執行。')
        if snapshot['phase']=='released' and snapshot.get('content'):
            result['accepted']=snapshot['content']
            result['source_receipt']=snapshot.get('source_receipt')
            with self.service.connect() as db:
                operations=[json.loads(row[0]) for row in db.execute('SELECT body FROM background_operations')]
            op=next((op for op in reversed(operations) if op['key']==key and op['state']=='queued'
                     and op.get('next_revision')==snapshot['revision'] and op['server_epoch']==self.core.server_epoch),None)
            if op:
                result['operation']=dict(op_id=op['op_id'],prompt_id=op['prompt_id'],key=key,revision=op['revision'],digest=op['digest'],
                    visual=op['payload']['extra_data']['extra_pnginfo']['workflow'],output=op['payload']['prompt'])
        return result

    def guard_start(self,value,server):
        """An available browser must not bypass an unresolved durable state."""
        snapshot=validate_snapshot(value.get('snapshot'))
        profile=next((p for p in snapshot['state']['generation']['profiles'] if p['id']==value.get('workflow')),None)
        if profile is None:raise ValueError('PCS 工作流綁定已失效。')
        key=dict(library_id=snapshot['library_id'],workspace_id=snapshot['state']['workspace'],workflow_id=profile['id'],
                 frontend_id=profile.get('frontend_id',''),path=profile.get('origin',{}).get('path',''))
        self.checked_key(dict(key=key),server)
        self.context(dict(identity=key),server)

    def edit(self,action,value,server):
        key,library,profile=self.checked_key(value,server)
        if action=='capture':
            proof=value.get('source_receipt')
            stamped=dict(value,source_revision=proof.get('source_revision') if isinstance(proof,dict) else None)
            # Recover an exact committed request before consulting today's PCS
            # source. A changed source does not erase yesterday's lost ACK.
            cached=self.core.capture_receipt(stamped)
            if cached is not None:return cached
            try:
                self._validate_source(key,library,profile,stamped)
                return self.core.capture(stamped)
            except ValueError as error:
                cached=self.core.capture_receipt(stamped)
                if cached is not None:return cached
                raise CaptureRejected(str(error),value['op_id']) from error
        if action not in ('lease','release'):raise ValueError('背景狀態操作無效。')
        result=getattr(self.core,action)(value)
        if action=='release':
            # Explicit acknowledged pagehide, not elapsed poll silence.
            for session,live in list(self.service.native_queue.sessions.items()):
                if matches(live['identity'],dict(workflow_id=profile['id']),profile):
                    self.service.native_queue.sessions.pop(session,None)
        return result

    @staticmethod
    def _validate_source(key,library,profile,value):
        bindings=bound_texts(library['state'],profile);source_revision=digest(bindings)
        proof=value.get('source_receipt');owner='/'.join(key[k] for k in ('library_id','workspace_id','workflow_id'))
        if (not isinstance(proof,dict) or proof.get('owner')!=owner or proof.get('source_revision')!=source_revision
            or not isinstance(proof.get('texts'),list) or len(proof['texts'])!=len(bindings)):
            raise ValueError('網頁尚未確認目前 PCS 文字來源，請等待同步完成。')
        fields={(b['node'],b['field']):b for b in bindings};seen=set()
        for item in proof['texts']:
            if not isinstance(item,dict):raise ValueError('文字來源收據無效。')
            field=(item.get('node'),item.get('field'));binding=fields.get(field)
            node=value.get('output',{}).get(item.get('node'),{})
            if (binding is None or field in seen or item.get('mode') not in ('pcs','manual')
                or item.get('class_type')!=node.get('class_type')
                or node.get('class_type')!=profile['graph'][binding['node']]['class_type']
                or not isinstance(item.get('text'),str) or node.get('inputs',{}).get(binding['field'])!=item['text']
                or (item['mode']=='pcs' and item['text']!=binding['text'])):
                raise ValueError('文字來源收據與網頁實際內容不符，尚未完成同步。')
            seen.add(field)

    async def start(self,value,server,post,verify=lambda *_:False):
        snapshot=copy.deepcopy(validate_snapshot(value.get('snapshot')))
        profile=next((p for p in snapshot['state']['generation']['profiles'] if p['id']==value.get('workflow')),None)
        if profile is None:raise ValueError('PCS 工作流綁定已失效。')
        key=dict(library_id=snapshot['library_id'],workspace_id=snapshot['state']['workspace'],workflow_id=profile['id'],
            frontend_id=profile.get('frontend_id',''),path=profile.get('origin',{}).get('path',''))
        self.checked_key(dict(key=key),server)
        receipt=self.core.snapshot(dict(key=key))
        operation=self.core.start(dict(key=key,op_id=value['id'],server_epoch=self.core.server_epoch,
            revision=receipt['revision'],digest=receipt['digest'],source_revision=digest(bound_texts(snapshot['state'],profile))))
        if not operation['submit_allowed']:
            return self.service.native_queue.status(value['id'])
        prompt_id=str(uuid.uuid4());client_id='pcs-background-'+operation['op_id']
        payload=copy.deepcopy(operation['payload']);payload.update(prompt_id=prompt_id,client_id=client_id)
        visual=payload['extra_data']['extra_pnginfo']['workflow']
        native=dict(id=value['id'],request_hash=digest(value),created=time.time(),state='prepared',error='',session='',epoch=0,
            identity=dict(workflow=key['workflow_id'],frontend_id=key['frontend_id'],path=key['path']),client_id=client_id,
            snapshot=snapshot,workflow=key['workflow_id'],texts=bound_texts(snapshot['state'],profile),user_scope='default',
            background=dict(key=key,revision=operation['revision'],digest=operation['digest'],server_epoch=self.core.server_epoch))
        attempted=False
        try:
            self.service.native_queue.prepare_payload(native,payload['prompt'],visual)
            visual.setdefault('extra',{})['pcs_native_operation']=native['id']
            self.events.register(prompt_id,client_id)
            # This is the ordinary backend /prompt route, including native
            # validation, execution and prompt-handler journaling. Never emulate
            # queue.put or execute nodes from an alternate private runner.
            attempted=True
            status,response=await post(payload)
            if status>=400:
                # Only the pinned native route's pre-queue validation errors
                # prove rejection. A 5xx can occur after queue.put succeeded.
                error=response.get('error',{})
                attempted=not(status==400 and isinstance(error,dict) and error.get('type') in (
                    'prompt_outputs_failed_validation','prompt_no_outputs','no_prompt'))
                raise ValueError('ComfyUI 拒絕背景工作流：'+str(response.get('error','請查看終端紀錄。')))
            if response.get('prompt_id')!=prompt_id:raise ValueError('背景提交的任務回執不符，不會重送。')
            recorded=self.service.native_queue.read(native['id'])
            if recorded.get('prompt_id')!=prompt_id or recorded['state']!='submitted':raise ValueError('背景提交尚未完成記錄核對。')
            if not verify(prompt_id,payload['prompt']):raise ValueError('ComfyUI 實際接收的工作流與確認版本不符，已停止後续操作。')
            self.core.bind_prompt(dict(key=key,op_id=native['id'],prompt_id=prompt_id,server_epoch=self.core.server_epoch))
            recorded.update(state='queued');self.service.native_queue.save(recorded)
            return self.service.native_queue.status(native['id'])
        except Exception as error:
            try:self.core.submission_failed(dict(key=key,op_id=native['id'],server_epoch=self.core.server_epoch,uncertain=attempted))
            except ValueError:pass # A committed bind remains committed even if its HTTP reply is lost.
            try:recorded=self.service.native_queue.read(native['id'])
            except ValueError:recorded=native
            recorded.update(state='unconfirmed' if attempted else 'failed',error=str(error))
            self.service.native_queue.save(recorded)
            if attempted:return self.service.native_queue.status(native['id'])
            raise

    def attach(self,value,server,sockets):
        self.checked_key(value,server)
        if value.get('new_client_id') not in sockets:raise ValueError('網頁執行連線尚未就緒。')
        op=self.service.native_queue.read(value.get('op_id'))
        frozen=op.get('background',{})
        if any(frozen.get(k)!=value.get(k) for k in ('key','revision','digest','server_epoch')):raise ValueError('接回任務的背景版本不符。')
        self.core.attach_snapshot(dict(value,prompt_id=op.get('prompt_id')))
        subscription=value.get('subscription_id')
        if not isinstance(subscription,str) or not 1<=len(subscription)<=100:raise ValueError('進度訂閱無效。')
        return self.events.attach(op['prompt_id'],value['new_client_id'],subscription)
