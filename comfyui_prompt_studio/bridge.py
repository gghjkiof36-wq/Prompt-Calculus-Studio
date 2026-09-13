"""One explicitly selected browser target, with expiring ownership and one-shot commands."""
import asyncio
import copy
import time
import uuid
from .shared.snapshots import validate_snapshot


class DesktopBridge:
    def __init__(self):
        self.lease=''; self.session=''; self.target=''; self.seen=0
        self.commands={}; self.wakeup=asyncio.Event(); self.reason='請在 ComfyUI 綁定文字節點，並啟用桌面版控制。'

    def status(self):
        if self.lease and time.monotonic()-self.seen>35:
            self.release(self.session, 'ComfyUI 網頁連線中斷，已暫停桌面控制。')
        return dict(ready=bool(self.lease),lease=self.lease,target=self.target,reason=self.reason,version=2)

    def claim(self,session,target):
        if not isinstance(session,str) or not session or len(session)>100: raise ValueError('連線識別無效。')
        if self.status()['ready'] and session!=self.session:
            raise ValueError('另一個 ComfyUI 分頁正在接受桌面控制，請先在該分頁停止控制。')
        self.release(self.session,'')
        self.session=session; self.target=str(target)[:300]; self.lease=uuid.uuid4().hex
        self.seen=time.monotonic(); self.reason=''; return self.status()

    def release(self,session,reason='已停止桌面控制。'):
        if session!=self.session: return self.status()
        self.lease=''; self.reason=str(reason)[:500]
        for command in self.commands.values():
            if command['state'] in ('queued','delivered'):
                command.update(state='error',error='連線已結束；若已點生成，請查看生成紀錄，不會自動重送。')
                command.pop('snapshot',None)
        self.wakeup.set()
        return dict(ready=False,lease='',target=self.target,reason=self.reason,version=2)

    def require(self,lease):
        if not self.status()['ready'] or lease!=self.lease: raise ValueError('桌面控制綁定已變更，請重新連線。')

    def publish(self,lease,ident,kind,snapshot,count=1):
        self.require(lease)
        if not isinstance(ident,str) or not ident or len(ident)>100 or kind not in ('sync','run'):
            raise ValueError('控制命令格式無效。')
        if ident in self.commands:
            existing=self.commands[ident]
            if existing['lease']!=lease: raise ValueError('命令屬於較早的連線。')
            return self.result(ident)
        validate_snapshot(snapshot)
        if type(count) is not int or not 1 <= count <= 100: raise ValueError('運行次數須介於 1–100。')
        if sum(c['state'] in ('queued','delivered') for c in self.commands.values())>=16:
            raise ValueError('待處理操作過多，請等待目前操作完成。')
        # Only coalesce unsent text updates. A generation always keeps its own snapshot.
        for c in self.commands.values():
            if c['kind']=='sync' and c['state']=='queued': c['state']='superseded'; c.pop('snapshot',None)
        self.commands[ident]=dict(id=ident,kind=kind,snapshot=copy.deepcopy(snapshot),count=count,lease=lease,state='queued',created=time.monotonic())
        while len(self.commands)>100:
            oldest=next((k for k,c in self.commands.items() if c['state'] not in ('queued','delivered')),None)
            if oldest is None: break
            del self.commands[oldest]
        self.wakeup.set(); return self.result(ident)

    async def take(self,session,lease):
        self.require(lease)
        if session!=self.session: raise ValueError('網頁連線已變更。')
        self.seen=time.monotonic()
        def pending():
            return next((c for c in self.commands.values() if c['state']=='queued' and c['lease']==lease),None)
        command=pending()
        if command is None:
            self.wakeup.clear()
            try: await asyncio.wait_for(self.wakeup.wait(),20)
            except asyncio.TimeoutError: pass
            self.require(lease); command=pending()
        self.seen=time.monotonic()
        if command:
            command['state']='delivered'
            return copy.deepcopy(command)
        return None

    def acknowledge(self,session,lease,ident,error='',prompt_id=''):
        self.require(lease)
        command=self.commands.get(ident)
        if session!=self.session or not command or command['lease']!=lease: raise ValueError('命令已失效。')
        if command['state']=='delivered':
            command.update(state='error' if error else 'done',error=str(error)[:1000],prompt_id=str(prompt_id)[:100])
            command.pop('snapshot',None)
        return self.result(ident)

    def result(self,ident):
        command=self.commands.get(ident)
        if not command: raise ValueError('找不到此操作的狀態，請確認 ComfyUI 生成紀錄。')
        return {k:v for k,v in command.items() if k not in ('snapshot','created')}

    def cancel_runs(self):
        for command in self.commands.values():
            if command['kind']=='run' and command['state'] in ('queued','delivered'):
                command.update(state='error',error='已停止運行並清除待執行佇列。'); command.pop('snapshot',None)
