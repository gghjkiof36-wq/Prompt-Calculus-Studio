"""Synthetic content only; no application startup and no user DB access."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('p0_inventory_fixture', ROOT / 'tools/local_workspace_p0.py')
p0 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p0)

from prompt_calculus_studio.local_documents import category, workflow, assets, io


class DocumentsCase(unittest.TestCase):
    def setUp(self):
        area = ROOT / 'qa-private/local-workspace/p1/temp'
        area.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='documents-', dir=area)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'synthetic source'
        self.destination = self.root / 'new document root'
        self.source.mkdir()
        self.destination.mkdir()
        (self.source / 'assets').mkdir()
        (self.source / 'assets/p0-swatch.png').write_bytes(p0.png())
        self.state, self.ids = p0.fixture()
        self.before = copy.deepcopy(self.state)

    def category(self):
        return category.from_state(self.state, self.ids['category_a'])

    def workflow(self):
        return workflow.from_state(self.state, self.ids['workspace_a'])

    def portable(self, document):
        return assets.prepare(document, self.source, self.destination)

    def saved(self):
        document = self.portable(self.category())
        result = io.create(self.destination, '分類.pcs-category', document)
        self.assertEqual(result.status, 'saved', result)
        return result
