"""Offline journal-only undo: exact records, no native submission machinery."""
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from prompt_calculus_studio.stage_store import StageStore
from prompt_calculus_studio import queue_history as history


class QueueHistoryTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close);self.store=StageStore(self.db)
        self.run=self.store.add('run','w',server='local',status='paused',stack=[])
        self.notices=[]
        self.runner=SimpleNamespace(store=self.store,client=SimpleNamespace(url='local'),
            pause=lambda ident,message:self.store.update(ident,status='paused',message=message),notify=self.notices.append)
        window=SimpleNamespace(state={'workspace':'w'},comfy=SimpleNamespace(input_flow=SimpleNamespace(chain=self.runner)),
            store=SimpleNamespace(directory=Path('synthetic')),notice=self.notices.append)
        self.canvas=SimpleNamespace(window=window,undo_stack=[],redo_stack=[])
        self.a=self.entry('a');self.b=self.entry('b')

    def entry(self,label):
        return self.store.add('entry','w',self.run['id'],run=self.run['id'],scheduler='q',label=label,status='waiting',saved={'text':''})

    def undo(self):return history.restore(self.canvas,self.canvas.undo_stack,self.canvas.redo_stack,False)
    def redo(self):return history.restore(self.canvas,self.canvas.redo_stack,self.canvas.undo_stack,True)

    def remove(self):
        history.perform(self.canvas,[self.a['id']],lambda:self.store.remove(self.a['id']))

    def test_remove_undo_redo_preserves_identity_and_sibling(self):
        original=self.store.read(self.b['id']);self.remove();self.undo()
        self.assertEqual(self.store.read(self.a['id']),self.a)
        self.assertEqual(self.store.read(self.b['id']),original)
        self.assertTrue(self.store.scheduler_paused('w','q'))
        self.redo();self.assertEqual(self.store.read(self.a['id'])['status'],'removed')

    def test_empty_text_edits_and_reorder_restore(self):
        history.perform(self.canvas,[self.a['id']],lambda:self.store.edit(self.a['id'],{'text':'changed'}))
        self.undo();self.assertEqual(self.store.read(self.a['id'])['saved'],{'text':''})
        self.redo();self.assertEqual(self.store.read(self.a['id'])['saved'],{'text':'changed'})
        ids=[self.b['id'],self.a['id']]
        history.perform(self.canvas,ids,lambda:self.store.reorder(self.run['id'],ids))
        self.undo();self.assertEqual([r['id'] for r in self.store.rows('entry')],[self.a['id'],self.b['id']])

    def test_late_attempt_rejects_restore_without_mutation(self):
        self.remove();self.store.add('attempt','w',self.run['id'],parent=self.a['id'],status='unconfirmed')
        before=history.capture(self.store,[self.a['id'],self.b['id']]);self.undo()
        self.assertEqual(history.capture(self.store,before),before);self.assertEqual(len(self.canvas.undo_stack),1)

    def test_foreign_workspace_and_modified_record_reject(self):
        self.remove();self.canvas.window.state['workspace']='other';self.undo()
        self.assertEqual(self.store.read(self.a['id'])['status'],'removed')
        self.canvas.window.state['workspace']='w';self.store.update(self.a['id'],saved={'text':'foreign'})
        self.undo();self.assertEqual(self.store.read(self.a['id'])['saved'],{'text':'foreign'})

    def test_finished_owner_cannot_be_revived(self):
        self.remove();self.store.update(self.run['id'],status='complete');self.undo()
        self.assertEqual(self.store.read(self.a['id'])['status'],'removed')

    def test_copy_undo_retains_history_and_redo_same_id(self):
        copied=self.store.copy_entry(self.a['id']);history.created(self.canvas,copied['id']);self.undo()
        self.assertEqual(self.store.read(copied['id'])['status'],'removed')
        self.redo();self.assertEqual(self.store.read(copied['id']),copied)

    def test_restore_refuses_over_capacity(self):
        self.remove()
        for i in range(9):self.entry(str(i))
        self.undo();self.assertEqual(self.store.read(self.a['id'])['status'],'removed')

    def test_existing_attempt_never_creates_undo(self):
        self.store.add('attempt','w',self.run['id'],parent=self.a['id'],status='failed')
        self.remove();self.assertFalse(self.canvas.undo_stack)


if __name__=='__main__':unittest.main()
