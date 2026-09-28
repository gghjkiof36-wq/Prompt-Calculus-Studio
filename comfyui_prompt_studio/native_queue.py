"""Journal commands to the real browser queue; never execute a stored graph."""
import copy
import hashlib
import json
import time
import uuid
from .workflow_state import matches, server_identity
from .shared.generation import api_graph, parameter_bindings, image_target, text_fields
from .shared.multi_output import bound_texts
from .shared.snapshots import validate_snapshot
from .shared.native_graph import graph_matches, input_schema


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class BrowserUnavailable(ValueError):
    """No browser candidate; a separate confirmed-close path may be considered."""


class NativeQueue:
    def __init__(self,service,multi_user=None):
        self.service=service; self.sessions={};self.multi_user=multi_user or (lambda:False)
        from .input_bridge import InputBridge
        self.inputs=InputBridge(service)
        with service.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS native_operations (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            # A restart cannot prove whether a delivered browser command ran.
            for ident,raw in db.execute('SELECT id,body FROM native_operations').fetchall():
                value=json.loads(raw)
                if value['state'] in ('pending','delivered','prepared','submitted'):
                    value.update(state='unconfirmed',error='擴充已重新啟動，提交須對帳，不會重送。')
                # No HTTP submit from the previous service process can still
                # enqueue after this startup. Preserve receipts, not its lock.
                value['submit_inflight']=False
                db.execute('UPDATE native_operations SET body=? WHERE id=?',(json.dumps(value,ensure_ascii=False),ident))

    def available(self):
        try:return self.multi_user() is False
        except Exception:return False

    def require_supported(self):
        if not self.available():raise ValueError('此原生入口目前只支援 ComfyUI 單一使用者模式；未執行舊副本。')

    def read(self,ident):
        with self.service.connect() as db:row=db.execute('SELECT body FROM native_operations WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('找不到原生執行要求。')
        return json.loads(row[0])

    def save(self,value):
        with self.service.connect() as db:
            db.execute('INSERT OR REPLACE INTO native_operations VALUES (?,?)',(value['id'],json.dumps(value,ensure_ascii=False)))

    def poll(self,value):
        self.require_supported()
        identity=value.get('identity'); session=value.get('session'); client=value.get('client_id'); epoch=value.get('epoch')
        workflows=value.get('workflows',[])
        if (not isinstance(session,str) or not 1<=len(session)<=100 or not isinstance(client,str) or not 1<=len(client)<=100
            or not isinstance(identity,dict) or type(epoch) is not int or epoch<0
            or any(not isinstance(identity.get(k,''),str) or len(identity.get(k,''))>1000 for k in ('workflow','path','frontend_id'))):
            raise ValueError('原生分頁身分無效。')
        if (not isinstance(workflows,list) or len(workflows)>256 or any(not isinstance(item,dict) or
            any(not isinstance(item.get(k,''),str) or len(item.get(k,''))>1000 for k in ('workflow','path','frontend_id')) for item in workflows)):
            raise ValueError('原生工作流目錄無效。')
        now=time.monotonic(); self.sessions={k:v for k,v in self.sessions.items() if now-v['seen']<10}
        if session not in self.sessions and len(self.sessions)>=32:raise ValueError('原生分頁數量超過上限。')
        self.sessions[session]=dict(identity=copy.deepcopy(identity),workflows=copy.deepcopy(workflows),client_id=client,epoch=epoch,seen=now,ready=value.get('ready') is True,user_scope='default',bindings_protocol=value.get('bindings_protocol'),capture_protocol=value.get('capture_protocol'),navigation_protocol=value.get('navigation_protocol'))
        with self.service.connect() as db:
            rows=db.execute("SELECT body FROM native_operations WHERE json_extract(body,'$.state')='pending'").fetchall()
        commands=[]
        for raw, in rows:
            operation=json.loads(raw)
            if operation['session']!=session:continue
            if time.time()-operation['created']>10 or any(operation[k]!=value.get(k) for k in ('identity','epoch','client_id')) or not value.get('ready'):
                operation.update(state='failed',error='原生工作流已切換或忙碌，未提交。');self.save(operation);continue
            if (operation.get('action')=='open' or operation.get('needs_navigation')) and sum(self.target_matches(item,operation['target']) for item in workflows)!=1:
                operation.update(state='failed',error='綁定的原生工作流已關閉或不再唯一，未切換。');self.save(operation);continue
            operation['state']='delivered';self.save(operation)
            commands.append({k:copy.deepcopy(operation[k]) for k in ('id','identity','epoch','action','target') if k in operation})
            if operation.get('action')!='open':
                profile=next(p for p in operation['snapshot']['state']['generation']['profiles'] if p['id']==operation['workflow'])
                commands[-1]['texts']=[dict(b,class_type=profile['graph'][b['node']]['class_type']) for b in operation['texts']]
                commands[-1]['images']=copy.deepcopy(operation.get('images',[]))
        return dict(commands=commands)

    @staticmethod
    def target_matches(identity,target):
        return matches(identity,dict(workflow_id=target.get('workflow'),origin=dict(path=target.get('path')),frontend_id=target.get('frontend_id')), {})

    def start(self,value,server='',action='queue'):
        self.require_supported()
        ident=value.get('id')
        if not isinstance(ident,str) or not ident or len(ident)>100:raise ValueError('操作識別碼無效。')
        snapshot=copy.deepcopy(validate_snapshot(value.get('snapshot'))); workflow=value.get('workflow')
        profiles=snapshot['state'].get('generation',{}).get('profiles',[])
        profile=next((p for p in profiles if p['id']==workflow),None)
        if profile is None:raise ValueError('PCS 工作流綁定已失效。')
        if not isinstance(profile.get('frontend_id'),str) or not profile['frontend_id']:
            raise ValueError('這個綁定缺少原生工作流身分，請重新選擇原生工作流；不以同名圖代替。')
        origin=profile.get('origin',{}).get('server')
        if server and origin and server_identity(server)!=server_identity(origin):raise ValueError('工作流綁定屬於另一個 ComfyUI 服務。')
        texts=[]
        if action!='open':
            if action=='capture' and 'queue_texts' in value:
                for binding in value['queue_texts']:
                    if (not isinstance(binding,dict) or (binding.get('node'),binding.get('field')) not in text_fields(profile['graph'])
                            or not isinstance(binding.get('text'),str) or binding.get('text_source') not in ('pcs','web')):
                        raise ValueError('Queue 明確文字對應無效。')
                    texts.append(copy.deepcopy(binding))
            elif action=='capture' and not text_fields(profile['graph']):texts=[]
            else:texts=bound_texts(snapshot['state'],profile)
        images=[]; uploaded=value.get('image','')
        requested=value.get('images',[])
        if not isinstance(requested,list) or len(requested)>100:raise ValueError('圖片輸入清單無效。')
        requested=copy.deepcopy(requested)
        if uploaded:requested.append(dict(node=image_target(profile),image=uploaded))
        seen=set()
        for entry in requested:
            if not isinstance(entry,dict):raise ValueError('圖片輸入無效。')
            uploaded=entry.get('image');node=entry.get('node')
            from pathlib import PurePosixPath
            if not isinstance(uploaded,str) or len(uploaded)>1000 or '\\' in uploaded or ':' in uploaded or PurePosixPath(uploaded).is_absolute() or '..' in PurePosixPath(uploaded).parts:
                raise ValueError('圖片輸入路徑無效。')
            if not isinstance(node,str) or profile['graph'].get(node,{}).get('class_type')!='LoadImage':raise ValueError('請選擇接收 PCS 圖片的 LoadImage 節點。')
            if node in seen:raise ValueError('同一個 LoadImage 不可重複指定圖片。')
            seen.add(node);images.append(dict(node=node,field='image',class_type='LoadImage',text=uploaded))
        if action=='queue' and not texts and not images:raise ValueError('請連接 CLIP 輸入或圖片輸入。')
        target=dict(workflow=workflow,path=profile.get('origin',{}).get('path',''),frontend_id=profile['frontend_id'])
        if action=='queue' and snapshot['state'].get('multi_output',{}).get('version',1)<5:
            self.service.work_queue.require_idle()
        fingerprint=digest(value if action=='queue' else [action,value])
        # Expire abandoned transport transactions before accepting a distinct
        # explicit click. This never re-sends or discards the old receipt.
        with self.service.connect() as db:
            stale=[row[0] for row in db.execute("SELECT id FROM native_operations WHERE json_extract(body,'$.state') IN ('pending','delivered','prepared','submitted') AND json_extract(body,'$.created')<?",(time.time()-30,))]
        for old in stale:self.status(old)
        with self.service.connect() as db:
            prior=db.execute('SELECT body FROM native_operations WHERE id=?',(ident,)).fetchone()
            if prior:
                saved=json.loads(prior[0])
                if saved['request_hash']!=fingerprint:raise ValueError('同一操作識別碼的內容不同。')
                return self.status(ident)
            pending=db.execute("SELECT COUNT(*) FROM native_operations WHERE json_extract(body,'$.state') IN ('pending','delivered','prepared','submitted')").fetchone()[0]
            if pending:raise ValueError('已有原生提交待完成，未建立另一份要求。')
        candidates=self.candidates(target)
        if not candidates and action!='open':raise BrowserUnavailable('請在唯一的 ComfyUI 分頁打開已綁定的同一份工作流；未執行 PCS 舊副本。')
        if len(candidates)!=1:raise ValueError('請在唯一的 ComfyUI 分頁打開已綁定的同一份工作流；未執行 PCS 舊副本。')
        session,live=candidates[0]
        target_count=sum(self.target_matches(item,target) for item in live.get('workflows',[]))
        if target_count>1 or action=='open' and target_count!=1:raise ValueError('找不到唯一的已載入原生工作流，未提交。')
        needs_navigation=not self.target_matches(live['identity'],target)
        if needs_navigation and live.get('navigation_protocol')!=1:
            raise ValueError('請更新擴充並重新整理 ComfyUI 網頁，才能定位另一個已開啟的工作流。')
        if action=='capture' and live.get('capture_protocol')!=1:
            raise ValueError('請更新擴充並重新整理網頁，以準備 Queue 工作快照。')
        if (images or any('text_source' in b for b in texts)) and live.get('bindings_protocol')!=1:
            raise ValueError('ComfyUI 網頁尚未載入新版綁定功能，請更新擴充並重新整理網頁。')
        if not live['ready']:raise ValueError('原生分頁正在提交或無法確認空閒，未提交。')
        operation=dict(id=ident,request_hash=fingerprint,created=time.time(),state='pending',error='',session=session,
            identity=copy.deepcopy(live['identity']),epoch=live['epoch'],client_id=live['client_id'],snapshot=snapshot,workflow=workflow,texts=texts,images=images,user_scope='default',target=target,needs_navigation=needs_navigation)
        if action=='open':operation.update(action='open',target=target)
        if action=='capture':operation.update(action='capture',server=server)
        self.save(operation);return self.status(ident)

    def checked(self,value):
        operation=self.read(value.get('id'))
        if any(value.get(k)!=operation[k] for k in ('session','identity','epoch')):raise ValueError('原生回覆不屬於這次分頁／工作流。')
        return operation

    def candidates(self,target):
        return [(key,live) for key,live in self.sessions.items() if time.monotonic()-live['seen']<3 and
                (self.target_matches(live['identity'],target) or any(self.target_matches(item,target) for item in live.get('workflows',[])))]

    def activate(self,value):
        self.require_supported()
        operation=self.checked(value);opened=value.get('opened_identity');epoch=value.get('opened_epoch')
        if (operation['state']!='delivered' or not operation.get('needs_navigation') or operation.get('resolved_identity')
            or value.get('client_id')!=operation['client_id'] or type(epoch) is not int or epoch<=operation['epoch']
            or not isinstance(opened,dict) or not self.target_matches(opened,operation['target'])):
            raise ValueError('原生切換回執無效，未提交。')
        # The selected browser can be busy loading beyond the heartbeat age;
        # other fresh browsers must still not contain the same native target.
        if any(key!=operation['session'] for key,_ in self.candidates(operation['target'])):
            raise ValueError('原生工作流的分頁身分不再唯一，未提交。')
        live=self.sessions.get(operation['session'])
        if not live or live['client_id']!=operation['client_id']:raise ValueError('原生連線已變更，未提交。')
        operation.update(resolved_identity=copy.deepcopy(opened),resolved_epoch=epoch)
        self.save(operation)
        live.update(identity=copy.deepcopy(opened),epoch=epoch,seen=time.monotonic())
        return dict(ok=True)

    def prepare(self,value):
        self.require_supported()
        operation=self.checked(value)
        if operation.get('action')=='open' or operation['state']!='delivered' or value.get('client_id')!=operation['client_id']:raise ValueError('原生提交已結束或連線已變更。')
        graph=api_graph(value.get('output')); visual=copy.deepcopy(value.get('workflow'))
        if not isinstance(visual,dict) or not isinstance(visual.get('nodes'),list):raise ValueError('原生可視工作流資料無效。')
        identity=operation.get('resolved_identity',operation['identity'])
        if operation.get('needs_navigation') and not operation.get('resolved_identity'):raise ValueError('尚未收到原生切換回執，未提交。')
        if visual.get('id')!=identity['frontend_id']:raise ValueError('原生可視工作流身分與綁定不符。')
        profile=next(p for p in operation['snapshot']['state']['generation']['profiles'] if p['id']==operation['workflow'])
        candidates=self.candidates(operation['target'])
        if (len(candidates)!=1 or candidates[0][0]!=operation['session'] or
            candidates[0][1]['identity']!=identity or candidates[0][1]['epoch']!=operation.get('resolved_epoch',operation['epoch'])):
            raise ValueError('原生工作流的分頁身分不再唯一，未提交。')
        if sum(self.target_matches(item,operation['target']) for item in candidates[0][1].get('workflows',[]))>1:
            raise ValueError('原生工作流的目錄身分不再唯一，未提交。')
        self.prepare_payload(operation,graph,visual)
        if operation.get('action')=='capture':
            work=self.service.work_queue.capture(operation)
            operation.update(state='captured',work=work)
            self.save(operation)
        return dict(ok=True)

    def prepare_payload(self,operation,graph,visual):
        """Journal one verified payload (native serialization or released state)."""
        profile=next(p for p in operation['snapshot']['state']['generation']['profiles'] if p['id']==operation['workflow'])
        texts=[]
        for binding in operation['texts']:
            node,field=binding['node'],binding['field']; actual=graph.get(node,{}).get('inputs',{}).get(field)
            if not isinstance(actual,str) or graph[node]['class_type']!=profile['graph'][node]['class_type']:
                raise ValueError('原生文字綁定已移除或變更，未提交。')
            if binding.get('text_source')=='pcs' and actual!=binding['text']:
                raise ValueError('PCS 提示詞尚未正確套用到 #'+node+' / '+field+'，未提交。')
            texts.append(dict(binding,text=actual,source='native'))
        for image in operation.get('images',[]):
            node=graph.get(image['node'],{})
            if node.get('class_type')!='LoadImage' or node.get('inputs',{}).get('image')!=image['text']:
                raise ValueError('PCS 圖片尚未正確套用到 LoadImage 節點，未提交。')
        current=dict(profile,graph=graph)
        identity=operation.get('resolved_identity',operation['identity'])
        generation=dict(mode=profile['mode'],workflow=profile['name'],workflow_id=profile['id'],
            workflow_sha256=digest(graph),parameters={k:graph[n]['inputs'][f] for k,(n,f) in parameter_bindings(current).items()},
            origin=copy.deepcopy(profile.get('origin',{})),frontend_id=identity['frontend_id'],
            native_operation=operation['id'],native_identity=identity,user_scope='default')
        try:
            import nodes
            schema=input_schema(graph,nodes.NODE_CLASS_MAPPINGS)
        except ImportError:
            schema={}
        operation.update(state='prepared',graph=graph,visual=visual,input_types=schema,
            marker=dict(snapshot=operation['snapshot'],texts=texts,source_texts=operation['texts'],generation=generation))
        self.save(operation);return dict(ok=True)

    def decorate(self,data):
        """Attach evidence to the real native POST, never replace its inputs."""
        extra=data.get('extra_data',{}).get('extra_pnginfo',{})
        visual=extra.get('workflow')
        if not isinstance(visual,dict) or not isinstance(visual.get('extra'),dict):return False
        ident=visual['extra'].get('pcs_native_operation')
        if not ident:return False
        # The one-shot transport marker must never remain in a saved PNG's
        # editable workflow, where re-opening it would look like a replay.
        extra['workflow']['extra'].pop('pcs_native_operation',None)
        recorded=False
        try:
            self.require_supported()
            operation=self.read(ident)
            if (operation['state']!='prepared' or data.get('client_id')!=operation['client_id']
                or digest(data.get('prompt'))!=digest(operation['graph'])):raise ValueError('原生提交與已記錄的操作不符。')
            prompt_id=str(data.get('prompt_id') or uuid.uuid4());data['prompt_id']=prompt_id
            marker=operation['marker']; first=marker['texts'][0] if marker['texts'] else None
            envelope=dict(schema_version=1,submission_id=prompt_id,problems=[],texts=marker['texts'],source_texts=marker['source_texts'],generation=marker['generation'],
                input_values=copy.deepcopy(operation['snapshot']['state'].get('_execution_inputs',{})),
                bindings=[dict(node_id=first['node'] if first else '',field=first['field'] if first else '',
                    node_title=operation['graph'][first['node']]['class_type'] if first else '圖片輸入',snapshot=operation['snapshot'])])
            extra.pop('prompt_studio_request',None);extra['prompt_studio']=copy.deepcopy(envelope)
            operation.update(state='submitted',submit_inflight=True,prompt_id=prompt_id,payload=dict(prompt_id=prompt_id,prompt=copy.deepcopy(data['prompt']),input_types=operation.get('input_types',{}),
                extra_data=dict(extra_pnginfo=copy.deepcopy(extra))))
            with self.service.connect() as db:
                # Both receipts must commit together before Comfy may validate
                # and enqueue this request. A partial journal is not evidence.
                db.execute('INSERT OR REPLACE INTO native_operations VALUES (?,?)',
                    (operation['id'],json.dumps(operation,ensure_ascii=False)))
                db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?)',(prompt_id,json.dumps(envelope,ensure_ascii=False),time.time()))
            recorded=True
        except (ValueError,KeyError,TypeError):
            pass
        finally:
            # Comfy catches exceptions from on_prompt handlers and continues;
            # even unexpected I/O or commit errors must invalidate the request.
            # Let unexpected exceptions propagate for the backend error log.
            if not recorded:
                data['prompt']={}
                extra.pop('prompt_studio',None)
        return True

    def reply(self,value):
        self.require_supported()
        operation=self.checked(value); prompt_id=value.get('prompt_id')
        if operation.get('action')=='open':
            if operation['state']!='delivered':return self.status(operation['id'])
            opened=value.get('opened_identity')
            if opened and not value.get('error'):
                if not isinstance(opened,dict) or not self.target_matches(opened,operation['target']):raise ValueError('原生開啟回覆與綁定不符。')
                operation.update(state='opened',opened_identity=copy.deepcopy(opened),error='')
            else:operation.update(state='unconfirmed' if value.get('uncertain') else 'failed',error=str(value.get('error','未確認原生工作流已開啟。'))[:1000])
            self.save(operation);return self.status(operation['id'])
        if operation['state'] not in ('delivered','prepared','submitted'):return self.status(operation['id'])
        if prompt_id:
            if operation.get('prompt_id')!=prompt_id:raise ValueError('原生回執任務編號與後端記錄不符。')
            operation.update(state='queued',submit_inflight=False,error=str(value.get('error',''))[:1000])
        else:operation.update(state='unconfirmed' if value.get('uncertain') or operation['state']=='submitted' else 'failed',error=str(value.get('error','未取得提交回執。'))[:1000])
        self.save(operation);return self.status(operation['id'])

    def status(self,ident):
        self.require_supported()
        operation=self.read(ident)
        if operation['state'] in ('pending','delivered','prepared','submitted') and time.time()-operation['created']>30:
            operation.update(state='unconfirmed' if operation['state']!='pending' else 'failed',error='原生提交逾時，不會自動重送。');self.save(operation)
        return {k:copy.deepcopy(operation[k]) for k in ('id','state','error','prompt_id','payload','opened_identity','work') if k in operation}

    def cancel_pending(self):
        with self.service.connect() as db:
            rows=db.execute("SELECT body FROM native_operations WHERE json_extract(body,'$.state') IN ('pending','delivered','prepared')").fetchall()
        for raw, in rows:
            operation=json.loads(raw);operation.update(state='failed',error='已取消原生提交。');self.save(operation)

    def cancel(self,ident,queue,interrupt):
        """Cancel only a journaled operation. Hold the executor lock through interruption."""
        import heapq
        self.require_supported();operation=self.read(ident)
        if operation['state'] in ('pending','delivered','prepared'):
            operation.update(state='failed',error='已取消這項 PCS 工作。');self.save(operation)
            return dict(ok=True,state='failed')
        prompt=operation.get('prompt_id')
        mutex=getattr(queue,'mutex',None)
        if mutex is None:raise ValueError('ComfyUI 無法核對指定任務的取消；後續已暫停，請在原生任務紀錄處理。')
        with mutex:
            running=list(queue.currently_running.values());pending=list(queue.queue)
            matching=[p for p in running+pending if len(p)>3 and p[1]==prompt]
            for p in matching:
                marker=p[3].get('extra_pnginfo',{}).get('prompt_studio',{}).get('generation',{})
                if marker.get('native_operation')!=ident or not graph_matches(operation.get('graph'),p[2],operation.get('input_types')):
                    raise ValueError('任務歸屬不符，未取消其他來源的工作。')
            if any(p[1]==prompt for p in pending):
                queue.queue[:]=[p for p in queue.queue if p[1]!=prompt];heapq.heapify(queue.queue)
                operation.update(state='failed',error='已移除這項尚未執行的 PCS 工作。');self.save(operation)
                return dict(ok=True,state='failed')
            if any(p[1]==prompt for p in running):
                interrupt();return dict(ok=True,state='cancelling')
        result=self.recovery(ident,queue)
        if result['recovery']['location']=='missing' and result['recovery']['can_abandon']:
            return self.abandon(ident,queue)
        return dict(ok=True,state='settled',reason=result['recovery']['reason'],recovery=result['recovery'])

    def _recovery(self, operation, queue):
        """Called with the executor lock held; absence alone is not completion."""
        prompt=operation.get('prompt_id');ident=operation['id']
        for location,items in (('running',queue.currently_running.values()),('queued',queue.queue)):
            for item in items:
                marker=item[3].get('extra_pnginfo',{}).get('prompt_studio',{}).get('generation',{}) if len(item)>3 and isinstance(item[3],dict) else {}
                if (prompt and item[1]==prompt) or marker.get('native_operation')==ident:
                    return dict(location=location,can_abandon=False,reason='這項工作仍在 ComfyUI '+('執行' if location=='running' else '等待')+'；尚未解除追蹤。')
        if operation['state'] in ('pending','delivered','prepared') or operation.get('submit_inflight',operation['state'] in ('submitted','unconfirmed') and bool(prompt)):
            return dict(location='submitting',can_abandon=False,reason='這項原生操作仍待提交或回覆，不能略過；請先取消，若服務已中斷請重新啟動服務後核對。')
        history=getattr(queue,'get_history',None)
        if not callable(history):return dict(location='unavailable',can_abandon=False,reason='無法讀取 ComfyUI 歷史，尚未解除追蹤。')
        entry=history(prompt_id=prompt).get(prompt) if prompt else None
        if entry:
            status=entry.get('status',{})
            terminal=status.get('completed') is True or status.get('status_str')=='error'
            return dict(location='history',can_abandon=terminal,reason='已找到 ComfyUI 歷史，正在核對原工作結果。')
        return dict(location='missing',can_abandon=True,reason='原任務已不在 ComfyUI 佇列，歷史也不存在。可按「解除阻塞」停止追蹤；保留紀錄，不重送。')

    def recovery(self, ident, queue):
        self.require_supported()
        mutex=getattr(queue,'mutex',None)
        if mutex is None:raise ValueError('無法鎖定並核對原生佇列，尚未解除追蹤。')
        with mutex:
            operation=self.read(ident)
            result={k:copy.deepcopy(operation[k]) for k in ('id','state','error','prompt_id','payload') if k in operation}
            result['recovery']=dict(self._recovery(operation,queue),checked=time.time())
            return result

    def occupied(self, running, queued, history):
        """Keep unknown legacy submissions occupied and retain terminal evidence."""
        known={p[1] for p in (*running,*queued)}
        for prompt in (*running,*queued):
            extra=prompt[3].get('extra_pnginfo',{}) if len(prompt)>3 and isinstance(prompt[3],dict) else {}
            marker=extra.get('prompt_studio',{})
            if marker and not marker.get('generation',{}).get('queue_job'):return True
        with self.service.connect() as db:
            rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM native_operations')]
        for operation in rows:
            if (operation.get('action') in ('capture','open') or operation.get('queue_terminal')
                    or operation['state'] in ('failed','opened','captured')):
                continue
            prompt=operation.get('prompt_id')
            if not prompt or prompt in known:return True
            entry=history(prompt).get(prompt,{})
            original=entry.get('prompt',[])
            terminal=entry.get('status',{}).get('completed') is True or entry.get('status',{}).get('status_str')=='error'
            if len(original)<3 or original[1]!=prompt or not terminal:
                return True
            operation['queue_terminal']=True;self.save(operation)
        return False

    def abandon(self, ident, queue):
        """Retire an uncertain receipt only while no matching executor job exists."""
        self.require_supported()
        mutex=getattr(queue,'mutex',None)
        if mutex is None:raise ValueError('無法鎖定並核對原生佇列，未略過。')
        with mutex:
            operation=self.read(ident);proof=self._recovery(operation,queue)
            if not proof['can_abandon']:raise ValueError(proof['reason'])
            # Updating the receipt also rejects any late prepare/decorate/reply.
            operation.update(state='failed',queue_terminal=True,abandoned=True,submit_inflight=False,
                recovery=dict(proof,checked=time.time()),error='已解除這筆舊任務的追蹤；保留紀錄，沒有重送。')
            self.save(operation)
        return dict(ok=True,state='abandoned',recovery=operation['recovery'],reason=operation['error'])

    def reconcile(self,ident,known_ids):
        self.require_supported()
        operation=self.read(ident)
        if operation.get('prompt_id') in known_ids and operation['state'] in ('submitted','unconfirmed'):
            operation.update(state='queued',submit_inflight=False,error='已由 ComfyUI 佇列／歷史確認原生提交。');self.save(operation)
        return self.status(ident)
