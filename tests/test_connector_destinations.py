"""Opaque destination browsing and durable operation bindings, isolated files."""
import base64
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

from prompt_calculus_studio import connector_destinations as destinations
from prompt_calculus_studio.connector_store import TransferStore
from prompt_calculus_studio.conversation_archive import canonical
from prompt_calculus_studio.conversation_import import Destination
from test_connector_capture import sample


class DestinationTests(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.base = Path(area.name)
        self.root = self.base / 'workspace'; self.root.mkdir()
        self.other = self.base / 'external'; self.other.mkdir()
        self.child = self.root / '旅行'; self.child.mkdir()
        self.grandchild = self.child / '京都'; self.grandchild.mkdir()
        self.roots = [self.root]
        self.default = self.other
        self.catalog = destinations.DestinationCatalog(lambda: self.roots, lambda: self.default, lambda path: None)
        self.store = TransferStore(self.base / 'private')

    def test_roots_default_children_parent_and_stable_ids(self):
        result = self.catalog.list()
        self.assertEqual(result['current']['path'], str(self.other))
        self.assertEqual(result['current']['id'], result['default_id'])
        root = next(row for row in result['roots'] if row['path'] == str(self.root))
        listing = self.catalog.list(root['id'])
        self.assertIsNone(listing['current']['parent_id'])
        child = listing['folders'][0]
        self.assertRegex(child['id'], '^[0-9a-f]{32}$')
        self.assertEqual(child['name'], '旅行')
        below = self.catalog.list(child['id'])
        self.assertEqual(below['current']['parent_id'], root['id'])
        self.assertEqual(self.catalog.list(root['id'])['folders'][0]['id'], child['id'])
        self.assertEqual(below['folders'][0]['path'], str(self.grandchild))

    def test_arbitrary_paths_unknown_ids_replaced_directories_and_removed_roots(self):
        for identity in (str(self.other), '../outside', 'a' * 32, None, []):
            with self.subTest(identity=identity), self.assertRaises(destinations.DestinationError):
                self.catalog.resolve(identity)
        root_id = next(r['id'] for r in self.catalog.list()['roots'] if r['path'] == str(self.root))
        child_id = self.catalog.list(root_id)['folders'][0]['id']
        self.child.rename(self.root / 'preserved')
        self.child.mkdir()
        with self.assertRaises(destinations.DestinationError):
            self.catalog.resolve(child_id)
        self.roots.clear()
        with self.assertRaises(destinations.DestinationError):
            self.catalog.resolve(root_id)

    @unittest.skipUnless(os.name == 'nt', 'native Windows junction boundary')
    def test_junction_is_not_listed_or_followed(self):
        link = self.root / 'redirect'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.other)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.addCleanup(lambda: link.rmdir() if link.exists() else None)
        root_id = next(r['id'] for r in self.catalog.list()['roots'] if r['path'] == str(self.root))
        self.assertNotIn('redirect', [r['name'] for r in self.catalog.list(root_id)['folders']])

    def test_enumeration_and_reply_limits_fail_without_truncation(self):
        self.default = self.root
        for index in range(3):
            (self.root / f'folder-{index}').mkdir()
        for name, value in (('MAX_FOLDERS', 2), ('MAX_ENTRIES', 2), ('MAX_REPLY', 10)):
            with self.subTest(limit=name), patch.object(destinations, name, value):
                with self.assertRaises(destinations.DestinationError) as caught:
                    self.catalog.list()
                self.assertEqual(caught.exception.code, 'destination_limit')

    def begin(self, operation=None):
        identity = self.catalog.list()['default_id']
        choice = self.catalog.resolve(identity)
        data = canonical(sample())
        operation = operation or uuid.uuid4().hex
        self.store.begin(operation, len(data), hashlib.sha256(data).hexdigest(),
                         destination_id=identity, destination=choice.destination, destination_root=choice.root)
        return operation, identity, data

    def test_begin_binding_survives_restart_and_cannot_be_changed(self):
        operation, identity, data = self.begin()
        restarted = TransferStore(self.store.directory)
        expected = restarted.get(operation)
        self.assertEqual(expected['destination'], str(self.other))
        self.assertEqual(restarted.begin(operation, len(data), hashlib.sha256(data).hexdigest(),
                                         destination_id=identity)['destination_identity'], expected['destination_identity'])
        for other_id in (None, 'f' * 32):
            with self.assertRaises(destinations.DestinationError):
                restarted.begin(operation, len(data), hashlib.sha256(data).hexdigest(), destination_id=other_id)
        self.store.chunk(operation, 0, base64.b64encode(data).decode())
        self.store.finish(operation)
        with self.assertRaises(destinations.DestinationError):
            self.store.accept(operation, Destination.choose(self.root))

    def test_cancel_tombstone_prevents_late_destination_begin(self):
        operation = uuid.uuid4().hex
        self.store.cancel(operation)
        _, identity, data = self.begin(operation)
        record = self.store.get(operation)
        self.assertEqual(record['state'], 'cancelled')
        self.assertNotIn('destination_id', record)
        self.assertEqual(self.store.begin(operation, len(data), hashlib.sha256(data).hexdigest(),
                                          destination_id=identity)['state'], 'cancelled')

    def test_bound_root_replacement_is_detected_even_if_leaf_identity_survives(self):
        self.default = self.root
        root_id = self.catalog.list()['default_id']
        child_id = self.catalog.list(root_id)['folders'][0]['id']
        choice = self.catalog.resolve(child_id)
        data = canonical(sample()); operation = uuid.uuid4().hex
        self.store.begin(operation, len(data), hashlib.sha256(data).hexdigest(), destination_id=child_id,
                         destination=choice.destination, destination_root=choice.root)
        preserved = self.base / 'preserved-root'
        self.root.rename(preserved); self.root.mkdir()
        (preserved / '旅行').rename(self.child)
        self.assertEqual(Destination.choose(self.child).identity, choice.destination.identity)
        with self.assertRaises(destinations.DestinationError):
            self.store.bound_choice(self.store.get(operation))


if __name__ == '__main__':
    unittest.main()
