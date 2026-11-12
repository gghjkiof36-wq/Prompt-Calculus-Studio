"""Document acceptance receipts. Schema creation belongs to storage_layout only."""
import hashlib
import time
from .comfy_event_protocol import encode


class EventStore:
    def __init__(self, db):
        self.db = db
        self.fault = None

    def checkpoint(self, name):
        if self.fault: self.fault(name)

    @staticmethod
    def key(event):
        return event['server_instance'], event['epoch'], str(event['seq'])

    def contains(self, event):
        row = self.db.execute('SELECT digest FROM channel_applied_events WHERE server=? AND epoch=? AND seq=?', self.key(event)).fetchone()
        if row and row[0] != hashlib.sha256(encode(event)).hexdigest():
            raise ValueError('Conflicting event receipt')
        return row is not None

    def accept(self, event, disposition='accepted'):
        # The caller owns accepting()/Storage.transaction. Never commit here.
        if not self.db.in_transaction and not getattr(getattr(self.db,'_transactions',None),'depth',0):
            raise RuntimeError('Event receipt requires the document transaction')
        self.checkpoint('before_receipt')
        self.db.execute('INSERT INTO channel_applied_events VALUES (?,?,?,?,?,?,?,?)',
            (*self.key(event), hashlib.sha256(encode(event)).hexdigest(), event.get('entity',''),
             int(event.get('receipt_revision',0)), disposition, time.time()))
        self.checkpoint('after_receipt')
