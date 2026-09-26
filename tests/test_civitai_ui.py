import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_studio.window import Window

APP=QApplication.instance() or QApplication([])


class CivitAIUiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.w=Window(self.temp.name)
        self.w.state['settings'].update(online=True,material='solid',reduce_motion=True)
        self.models=Path(self.temp.name)/'models'; (self.models/'loras').mkdir(parents=True)
        self.w.models.root.setText(str(self.models)); self.w.state['settings']['model_root']=str(self.models)
    def tearDown(self): self.w.close(); APP.processEvents(); self.temp.cleanup()

    def test_settings_has_civitai_page_and_plain_details(self):
        page=self.w.settings_page.civitai
        self.assertIn('civitai',self.w.settings_page.index)
        self.assertEqual(self.w.settings_page.comfy_tabs.count(),3)
        self.assertEqual([page.tabs.tabText(i) for i in range(3)],['搜尋模型','下載紀錄','連線設定'])
        self.w.state['settings']['online']=False
        record=dict(id=11,name='Hero <b>Model</b>',type='LORA',creator={'username':'maker'},
            description='<p>Safe <b>description</b></p>',stats={'downloadCount':1200,'thumbsUpCount':12},
            modelVersions=[dict(id=22,name='v2',baseModel='Illustrious',trainedWords=['hero'],images=[],
                files=[dict(id=33,name='hero.safetensors',primary=True,downloadUrl='https://civitai.com/api/download/models/22')])])
        page.current=record; page.version.addItem('v2',record['modelVersions'][0]); page.refresh_version()
        self.assertEqual(page.creator.text(),'maker'); self.assertEqual(page.trigger.text(),'hero')
        self.assertFalse(hasattr(page,'description')); self.assertTrue(page.download.isEnabled())
        self.assertEqual(page.fields['Usage Tips'].text(),'未提供')

    def test_identification_commits_completed_batch(self):
        model=self.models/'loras'/'x.safetensors'; model.write_bytes(b'x'); self.w.models.scan()
        while self.w.jobs.active: QTest.qWait(10)
        rows=self.w.catalog.rows('model',str(self.models.resolve()),limit=20)
        enriched=[{**rows[0],'sha256':'A'*64,'hash_size':1,'hash_mtime':model.stat().st_mtime_ns,
            'civitai_status':'matched','civitai_model_id':11,'civitai_version_id':22,'base_model':'Illustrious',
            'creator':'maker','civitai':{'provider':'civitai','model_id':11,'version_id':22,'sha256':'A'*64}}]
        with patch('prompt_studio.pages.load_token',return_value=''),patch('prompt_studio.pages.identify_models',return_value=enriched):
            self.w.models.identify_civitai()
            while self.w.jobs.active: QTest.qWait(10)
        saved=self.w.catalog.get(rows[0]['id']); self.assertEqual(saved['civitai_version_id'],22)
        self.assertEqual(self.w.catalog.source_for_hash('A'*64)['model_id'],11)


if __name__=='__main__': unittest.main()
