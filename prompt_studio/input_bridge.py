"""Publish buffer heads; native clicks request the very same head, not a copy."""
import hashlib
import uuid
from .flow_data import scheduler_for


class InputBridge:
    def __init__(self,runner):
        self.runner=runner;self.session=uuid.uuid4().hex;self.pending=False;self.ack=[]
        self.client_id=hashlib.sha256(str(runner.window.store.directory.resolve()).casefold().encode()).hexdigest()

    def entries(self):
        runner=self.runner;state=runner.window.state
        profiles={p['id']:p for p in state.get('generation',{}).get('profiles',[])}
        entries=[]
        for owner,control in runner.controls():
            route=control.get('route',{});scheduler=route.get('scheduler');rows=runner.store.rows(owner)
            if not scheduler or route.get('server')!=runner.client.url or not rows:continue
            projected=state
            if route['workspace']!=state['workspace']:
                scene=state.get('workspace_scenes',{}).get('items',{}).get(route['workspace'])
                if not scene:continue
                projected=dict(state,**scene)
            try:attached=scheduler_for(projected,route['workflow'])==scheduler
            except ValueError:attached=False
            if not attached:
                if not control['paused']:runner.store.set_control(owner,paused=True)
                continue
            profile=profiles.get(route['workflow'])
            if not profile or not profile.get('frontend_id'):continue
            head=next((r for r in rows if r['state'] in ('preparing','submitted','unconfirmed')),rows[0])
            entries.append(dict(owner=owner,head=head['id'],workflow=profile['id'],frontend_id=profile['frontend_id'],
                path=profile.get('origin',{}).get('path',''),paused=control['paused'] or head['state'] in ('failed','unconfirmed') or
                route['workspace']!=state['workspace']))
        return entries

    def poll(self):
        client=self.runner.client
        if self.pending or not client.connected or not getattr(client,'input_bridge_supported',False):return
        self.pending=True;ack=list(self.ack)
        def receive(value):
            self.pending=False;self.ack=[k for k in self.ack if k not in ack]
            for request in value.get('requests',[]):
                if request['id'] in self.ack:continue
                self.ack.append(request['id'])
                try:item=self.runner.store.read(request['head'])
                except ValueError:continue
                if item['owner']!=request['owner'] or item['route']['workspace']!=self.runner.window.state['workspace']:continue
                if item['state']=='waiting' and not self.runner.store.control(item['owner'])['paused']:
                    self.runner.store.update(item['id'],entry_point='native',native_request=request['id'])
            self.runner.pump()
        def failed(error):
            self.pending=False
            for owner,control in self.runner.local_controls():
                if control.get('route',{}).get('scheduler'):self.runner.store.set_control(owner,paused=True)
            self.runner.notify('預排程與原生按鍵同步失敗，已保留並暫停：'+str(error))
        client.request('workflow/inputs/publish',dict(client=self.client_id,session=self.session,entries=self.entries(),ack=ack),done=receive,failed=failed)
