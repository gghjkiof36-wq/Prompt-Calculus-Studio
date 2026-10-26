"""Load the authored delivery through PCS and retain text and independent copies."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.build_user_delivery_089 import assemble_learning, TEMPLATES
from prompt_calculus_studio.local_documents import category, io


class UserDeliveryMaterialsTests(unittest.TestCase):
    def test_complete_library_loads_and_preserves_authored_prompts(self):
        with tempfile.TemporaryDirectory(prefix='pcs-materials-') as area:
            root = Path(area)/'files'
            assemble_learning(root)
            catalog = json.loads((TEMPLATES/'materials.json').read_text(encoding='utf-8'))
            count = 5
            for spec in catalog['categories']:
                state = category.to_state(io.load(root/'素材', spec['name']+'.pcs-category').document)
                count += len(state['items'])
                for name, prompt in spec['entries']:
                    item = next(item for item in state['items'] if item['name'] == name)
                    self.assertEqual(item['prompt'], prompt)
                    self.assertEqual(item['content_kind'], spec['kind'])
                combination = next(item for item in state['items'] if item['material_type'] == 'combination')
                before = copy.deepcopy(combination)
                state['items'][0]['prompt'] = 'user edit'
                state['items'][0]['composition']['prompt'] = 'user edit'
                self.assertEqual(combination, before)
            self.assertEqual(count, 41)
            self.assertEqual({p.stem for p in (root/'素材').glob('*.pcs-category')},
                             {'場景與環境', '光線與氣氛', '構圖與鏡頭', '材質與風格'})
            for name in ('研究與學習', '寫作與整理'):
                self.assertFalse(list(root.rglob(name+'.pcs-category')))
                note = root/'筆記'/(name+'.md')
                self.assertEqual(note.read_bytes(), (TEMPLATES/'Notes'/note.name).read_bytes())
                self.assertTrue(note.read_text(encoding='utf-8').startswith('# '+name))
            self.assertEqual(len(list((root/'筆記模板').glob('*.md'))), 8)
            self.assertTrue((root/'Markdown 相容性測試/Markdown-相容性測試.md').is_file())
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with self.assertRaises(ValueError):
                assemble_learning(root)
            self.assertEqual({p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}, before)
