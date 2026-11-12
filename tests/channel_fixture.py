"""Clock-injected peer using the real extension log; no ComfyUI or GPU."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import json

from test_comfy_integration import Service
from prompt_calculus_studio import comfy_event_protocol as p
from prompt_calculus_studio.comfy_client import RequestFailure


class Clock:
    def __init__(self): self.now=0.
    def __call__(self): return self.now


class Peer:
    def __init__(self, root, clock, service=None):
        self.root,self.clock=root,clock
        self.service=service or Service(root,root,root);self.log=self.service.events
        self.calls=[];self.pending=[];self.hold=set();self.running=[]
        self.drop_terminal=False;self.reset_once=False;self.submissions=1

    @property
    def identity(self): return dict(server_instance=self.log.server_instance,epoch=self.log.epoch)

    def capability(self):
        return dict(capabilities=[p.CAPABILITY],event_channel=dict(schema=p.SCHEMA,transport='long-poll',
            limits=p.limits(),**self.identity),extension_info=dict(root=str(self.root/'custom_nodes'/'comfyui_prompt_calculus_studio')))

    def append(self, entity='native_operations:one', kind='operation.queued', payload=None, prompt='prompt-one', scope=None):
        with self.log.connection() as db:
            event=self.log.append(db,kind,payload or dict(state='queued'),entity=entity,prompt_id=prompt,scope=scope)
        self.log.committed();return event

    def send(self,route,done,failed,deadline):
        path=urlsplit(route).path
        row=SimpleNamespace(route=route,path=path,done=done,failed=failed,deadline=deadline,aborted=False,
                            due=self.clock()+(.25 if path!='events' else 1))
        row.abort=lambda:setattr(row,'aborted',True)
        self.calls.append((self.clock(),route));self.pending.append(row)
        return row

    def response(self, route):
        path=urlsplit(route).path
        if path=='config':return dict(token='test-token')
        if path=='events/capability':return self.capability()
        q={k:v[0] for k,v in parse_qs(urlsplit(route).query,keep_blank_values=True).items()}
        identity=(q['server_instance'],q['epoch']);scope=json.loads(q['scope']);digest=q['scope_digest']
        if path=='events':
            if self.reset_once:self.reset_once=False;raise RequestFailure('reset',True,409)
            result=self.log.events(q['since'],0,identity,scope,digest)
            if self.drop_terminal:result['events']=[item for item in result['events'] if item['kind']!='native.execution_success']
            return result
        if path=='events/heartbeat':
            self.log.check(identity,scope,digest)
            return self.log.heartbeat(self.running,scope)
        if path=='events/snapshot':return self.log.snapshot(identity,scope,digest,q['snapshot_token'],q['after'])
        raise AssertionError('Unexpected route (no dispatch is allowed): '+route)

    def flush(self):
        for row in list(self.pending):
            if row.aborted:self.pending.remove(row);continue
            if row.path in self.hold or row.due>self.clock():continue
            self.pending.remove(row)
            try: result=self.response(row.route)
            except Exception as exc:
                row.failed(exc if isinstance(exc,RequestFailure) else RequestFailure(str(exc),True,400))
            else: row.done(result)

    def advance(self, core, seconds):
        end=self.clock()+seconds
        while self.clock()<end:
            self.clock.now=round(min(end,self.clock()+.25),6)
            core.tick();self.flush()

    def reload(self):
        self.service=Service(self.root,self.root,self.root);self.log=self.service.events
