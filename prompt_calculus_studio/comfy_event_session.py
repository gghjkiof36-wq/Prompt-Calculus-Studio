"""Single-document host adapter; Workspace owns its own shared adapter."""
import logging
import sqlite3
from PySide6.QtCore import QTimer
from .comfy_event_transport import QtChannel


class DocumentChannel:
    def __init__(self, client):
        self.client=client
        self.channel=None
        self.retiring=[]
        self.generation=0

    def observe(self, identity):
        client=self.client
        key=(client.url,client.token,identity)
        if self.channel is not None and self.key==key:return
        self.stop()
        self.key=key
        self.channel=QtChannel(client.window.store.directory,client.url,client.token,identity,client)
        generation=self.generation
        client.events.bind(identity)
        client.events.probed=self.authorize
        self.channel.healthChanged.connect(lambda value:self.health(value,generation))
        self.channel.deliveryReady.connect(lambda page:self.deliver(page,generation))
        self.channel.command.emit(dict(kind='owner',value=dict(token=client.window.store.library_id,
            library_id=client.window.store.library_id,workspace_id=client.window.state['workspace'],
            path=str(client.window.store.directory),attached=True,servers=[client.url])))

    def authorize(self):
        if self.channel and all(row.get('probed') for row in (*self.client.events.reconcile.pending.values(),*self.client.events.aux.pending.values())):
            self.channel.command.emit(dict(kind='snapshot_authorized'))

    def health(self,value,generation):
        if generation!=self.generation:return
        if 'display' in value:
            if value['identity']==self.channel.identity:
                self.client.events.display(value['display'].get(self.client.window.store.library_id,[]))
            return
        self.channel.identity=dict(value['identity'])
        events=self.client.events
        events.bind(value['identity'])
        if value.get('heartbeat'):events.heartbeat()
        else:events.health(value['health'])
        self.authorize()

    def deliver(self,page,generation):
        def apply(index=0):
            if generation!=self.generation or self.client.stopped or page['identity']!=self.channel.identity:return
            try:
                if index==len(page['events']):
                    self.channel.command.emit(dict(kind='delivered',page=page,running=self.client.events.running_prompts()))
                    if page['snapshot'] and page['done']:
                        self.client.events.snapshot_ready=True
                        self.client.events.ready=self.client.events.channel_healthy and not self.client.events.reconcile.pending
                        self.channel.command.emit(dict(kind='reconciled'))
                    return
                self.client.events.receive(page['events'][index],lambda:apply(index+1),snapshot=bool(page['snapshot']))
            except (sqlite3.Error,OSError,ValueError) as exc:
                logging.getLogger(__name__).warning('document acceptance pending: %s',exc)
                QTimer.singleShot(1000,lambda:apply(index))
        apply()

    def stop(self):
        self.generation+=1
        self.client.events.close()
        if self.channel:
            self.channel.stop();self.retiring.append(self.channel);self.channel=None
        self.retiring=[channel for channel in self.retiring if channel.running]

    def stopped(self):
        self.retiring=[channel for channel in self.retiring if channel.running]
        return not self.retiring
