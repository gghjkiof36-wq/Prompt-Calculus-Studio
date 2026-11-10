"""Frozen migration IDs and real journal rows compared with the S3 source.

The external baseline probe uses PCS_COMPAT_CAPTURE. Ordinary tests can only
read the checked-in 8e9e87e receipt. No body normalization is performed.
"""
import copy
from contextlib import contextmanager
import hashlib
import itertools
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
import weakref
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.generation_panel import GenerationPanel
from prompt_calculus_studio.stage_context import execution_scope
from prompt_calculus_studio.stage_store import StageStore
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.window import Window

FIXTURES = Path(__file__).with_name('fixtures')
INPUT = FIXTURES / 'document_write_legacy_input.json'
EXPECTED = FIXTURES / 'document_write_compatibility_8e9e87e.json'


@contextmanager
def frozen_ids():
    serial = itertools.count(1)
    with patch('uuid.uuid4', side_effect=lambda: uuid.UUID(bytes=hashlib.sha256(str(next(serial)).encode()).digest()[:16])), \
         patch('time.time', return_value=1700000000.):
        yield


class Owner:
    def __init__(self, store, state):
        self.store, self.state = store, state
        self.changed = Mock()
        self.document_changed = Mock()
        store._document_owner = weakref.ref(self)
        if hasattr(Window, 'update_document'):
            self.update_document = Window.update_document.__get__(self)


class DocumentWriteCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_frozen_migrations_and_journal_match_s3(self):
        capture = os.environ.get('PCS_COMPAT_CAPTURE')
        source = json.loads(INPUT.read_text(encoding='utf-8'))
        result = {}
        for multi in (False, True):
            with self.subTest(multi=multi), frozen_ids(), tempfile.TemporaryDirectory() as directory:
                store = Storage(directory)
                try:
                    state = prepare_state(copy.deepcopy(source), multi=multi)
                    owner = Owner(store, state)
                    before = copy.deepcopy(state)
                    panel = GenerationPanel(owner)
                    try:
                        # The old getter wrote defaults, but the old panel's
                        # real construction never called it. Compare that old
                        # saved body with the new read-only getter included.
                        if not capture:
                            panel.settings()
                        self.assertEqual(state, before)
                        store.save(state)
                        result['multi' if multi else 'single'] = store.db.execute(
                            'SELECT body FROM document WHERE id=1').fetchone()[0]
                    finally:
                        panel.deleteLater(); self.app.processEvents()
                finally:
                    store.close()

        # These old controllers are deliberately unchanged. Missing-key
        # defaults must not swallow their first real mode/choice write.
        for name in ('mode', 'chosen'):
            with self.subTest(legacy_writer=name), frozen_ids(), tempfile.TemporaryDirectory() as directory:
                store = Storage(directory)
                try:
                    state = prepare_state(copy.deepcopy(source), multi=False)
                    state.pop('generation', None)
                    state['selection_view'] = 'canvas'
                    owner = Owner(store, state)
                    panel = GenerationPanel(owner)
                    try:
                        panel.reveal_controls = Mock()
                        if name == 'mode': panel.mode.setCurrentIndex(1)
                        else: panel.workflow_changed()
                        self.assertIn('generation', state)
                        self.assertEqual(state['generation']['mode'], 'img2img' if name == 'mode' else 'txt2img')
                        if name == 'chosen': self.assertEqual(state['generation']['chosen'], {'txt2img':''})
                        store.save(state)
                        result['legacy-'+name] = store.db.execute('SELECT body FROM document WHERE id=1').fetchone()[0]
                    finally:
                        panel.deleteLater(); self.app.processEvents()
                finally:
                    store.close()

        for fail in (True, False):
            with self.subTest(rollback=fail), frozen_ids(), tempfile.TemporaryDirectory() as directory:
                store = Storage(directory)
                try:
                    owner = Owner(store, prepare_state(copy.deepcopy(source), multi=True))
                    journal = StageStore(store.db)
                    first = journal.add('entry', owner.state['workspace'], status='ready', payload={'text':'before'})
                    store.save(owner.state)
                    before = copy.deepcopy(owner.state)
                    read_rows = lambda: store.db.execute('SELECT * FROM stage_journal ORDER BY position').fetchall()
                    read_body = lambda: store.db.execute('SELECT body FROM document WHERE id=1').fetchone()[0]
                    rows_before, body_before = read_rows(), read_body()
                    callbacks = []
                    try:
                        with execution_scope(owner, journal) as tx:
                            def change(state): state['items'][0]['prompt'] = 'journal interleave'
                            if capture:
                                tx.register('items')
                                change(owner.state)
                            else:
                                owner.update_document(change, fields=('items',), reason='journal-interleave')
                            journal.update(first['id'], status='complete', payload={'text':'after'})
                            journal.add_captured('batch_snapshot', owner.state['workspace'], snapshot=owner.state)
                            journal.add('entry', owner.state['workspace'], status='pending')
                            self.assertNotEqual(read_rows(), rows_before)
                            store.save(owner.state)
                            store.after_commit(lambda: callbacks.append('committed'))
                            self.assertEqual(callbacks, [])
                            if fail: raise RuntimeError('injected')
                    except RuntimeError as exc:
                        if not fail or str(exc) != 'injected': raise
                    if fail:
                        self.assertEqual(owner.state, before)
                        self.assertEqual(read_rows(), rows_before)
                        self.assertEqual(read_body(), body_before)
                        self.assertEqual(callbacks, [])
                    else:
                        self.assertEqual(callbacks, ['committed'])
                        self.assertEqual(len(read_rows()), 3)
                    result['rollback' if fail else 'commit'] = dict(
                        state=owner.state, rows=read_rows(), body=read_body(), callbacks=callbacks)
                finally:
                    store.close()

        if capture:
            Path(capture).write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        else:
            expected = json.loads(EXPECTED.read_text(encoding='utf-8'))
            # Convert SQLite tuples only; document/journal bodies remain the
            # actual TEXT, byte for byte, including original JSON key order.
            actual = json.loads(json.dumps(result, ensure_ascii=False))
            self.assertEqual(set(actual), set(expected))
            for name in expected:
                with self.subTest(comparison=name): self.assertEqual(actual[name], expected[name])
        type(self).body_bytes = {name: len(result[name].encode('utf-8')) for name in ('single','multi')}


if __name__ == '__main__': unittest.main()
