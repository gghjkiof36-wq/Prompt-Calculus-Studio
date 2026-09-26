"""Retain actual backend execution events for a late joining Web client.

No event is inferred from a timer, a node label, or a generated image. The
original sender still receives every event and owns the WebSocket transport.
"""
import copy
import threading
from collections import OrderedDict


EVENTS = frozenset(('execution_start', 'execution_cached', 'executing', 'progress',
    'progress_state', 'executed', 'execution_success', 'execution_error', 'execution_interrupted'))


class BackgroundEvents:
    def __init__(self, send):
        self.send = send
        self.lock = threading.RLock()
        self.jobs = OrderedDict()

    def register(self, prompt_id, client_id):
        with self.lock:
            if prompt_id in self.jobs:
                if self.jobs[prompt_id]['client_id'] != client_id:
                    raise ValueError('任務連線身分不符。')
                return
            if len(self.jobs) >= 100:
                finished = next((key for key, job in self.jobs.items() if job['finished']), None)
                if finished is None:
                    raise ValueError('背景任務記錄已滿。')
                del self.jobs[finished]
            self.jobs[prompt_id] = dict(client_id=client_id, seq=0, events=[], subscribers={}, finished=False)

    def observe(self, event, data, sid=None):
        with self.lock:
            prompt_id = data.get('prompt_id') if isinstance(data, dict) else None
            job = self.jobs.get(prompt_id)
            # Some native executing-null messages omit prompt_id. Do not guess
            # their task from last_node_id; execution_success clears this run.
            if event in EVENTS and job is not None and sid == job['client_id']:
                job['seq'] += 1
                item = dict(seq=job['seq'], event=event, data=copy.deepcopy(data))
                # Progress is a replaceable backend state; retain start/cache/
                # executed ordering while bounding long sampler runs.
                if event in ('progress', 'progress_state', 'executing'):
                    job['events'] = [e for e in job['events'] if e['event'] != event]
                job['events'].append(item)
                if event in ('execution_success', 'execution_error', 'execution_interrupted'):
                    job['finished'] = True
                for client, subscription in tuple(job['subscribers'].items()):
                    self.deliver(prompt_id,item,client,subscription,False)
            self.send(event, data, sid)

    def deliver(self,prompt_id,item,client_id,subscription,replayed):
        self.send('pcs_background_event',dict(prompt_id=prompt_id,subscription_id=subscription,
            replayed=replayed,**copy.deepcopy(item)),client_id)

    def attach(self, prompt_id, client_id, subscription):
        """Caller has verified the restored revision and native job mapping.

        Locking the real event observer and snapshot delivery together prevents
        a newer progress event being delivered before an older replay.
        """
        with self.lock:
            job = self.jobs.get(prompt_id)
            if job is None:
                raise ValueError('這筆任務沒有可接回的真實執行事件。')
            if client_id == job['client_id']:
                raise ValueError('背景連線不可當作網頁連線。')
            if job['finished']:
                return dict(prompt_id=prompt_id,seq=job['seq'],finished=True,replayed=False)
            if job['subscribers'].get(client_id)==subscription:
                return dict(prompt_id=prompt_id, seq=job['seq'], replayed=False)
            self.send('pcs_background_snapshot',dict(prompt_id=prompt_id,subscription_id=subscription,
                watermark=job['seq'],events=copy.deepcopy(sorted(job['events'],key=lambda e:e['seq']))),client_id)
            job['subscribers'][client_id]=subscription
            return dict(prompt_id=prompt_id, seq=job['seq'], replayed=True, finished=job['finished'])

    def detach(self, client_id):
        with self.lock:
            for job in self.jobs.values():
                job['subscribers'].pop(client_id,None)
