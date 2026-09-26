"""Confirm the active bound native workflow; never load, generate or change bindings."""
import copy
import time
import uuid
from PySide6.QtCore import QTimer
from .generation import options


def open_bound_workflow(window,workflow):
    client=window.comfy
    profile=next((p for p in options(window.state)['profiles'] if p['id']==workflow),None)
    if not profile:
        window.notice('綁定的工作流已移除。');return
    if not client.connected or not getattr(client,'native_open_supported',False):
        window.notice(client.native_unavailable_reason or '請連線並更新 ComfyUI 擴充，才能確認已綁定的原生工作流。');return
    if client.generation.batch or getattr(client,'opening_native',False):
        window.notice('請等候目前操作完成後，再確認工作流。');return
    operation=str(uuid.uuid4());started=time.monotonic()
    owner=(window.store,window.state['workspace'],client.url,client.epoch)
    source=copy.deepcopy(profile);client.opening_native=operation
    def current():
        return (not client.stopped and client.opening_native==operation and
            owner==(window.store,window.state['workspace'],client.url,client.epoch) and
            next((p for p in options(window.state)['profiles'] if p['id']==workflow),None)==source)
    def finish(message):
        valid=current()
        if client.opening_native==operation:client.opening_native=False
        if valid:window.notice(message)
    def received(value):
        if not current():finish('');return
        if value.get('state')=='opened':finish('已確認 ComfyUI 目前工作流：'+source['name']);return
        if value.get('state') in ('failed','unconfirmed'):
            finish(value.get('error') or '無法確認目前原生工作流。');return
        if time.monotonic()-started>35:
            finish('原生工作流核對結果未確認，請查看 ComfyUI 分頁；不會自動生成。');return
        QTimer.singleShot(500,poll)
    def poll():
        if not current():finish('');return
        client.request('workflow/native/status',dict(id=operation),done=received,failed=lambda error:finish(str(error)))
    window.notice('正在確認 ComfyUI 目前工作流：'+source['name']+'…')
    client.request('workflow/native/open',dict(id=operation,workflow=workflow,snapshot=client.snapshot()),
        done=received,failed=lambda error:finish(str(error)))
