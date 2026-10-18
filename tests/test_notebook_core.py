import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from prompt_calculus_studio.notebook_index import parse_note,Query,inventory,relations
from prompt_calculus_studio.notebook_store import NotebookStore
from prompt_calculus_studio.notebook_actions import expand_template,template_edits,create_note
from prompt_calculus_studio.notebook_composer import apply_text


class NotebookCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_query_conditions_and_protected_tags(self):
        text='---\nstatus: draft\ncount: 3\nchecked: false\ntags: [小說/初稿]\naliases: [雨夜]\n---\n# 開始\n雨停 地圖 #場景/街道\n```\n#不是標籤\n```\n[^a]: 註釋\n'
        note=parse_note(self.root,self.root/'雨停.md',text)
        for query in ('雨停 地圖','tag:小說','[status:draft] [count:>=3] [checked:false]',
                      'path:雨停 (不存在 OR 地圖)','-不存在 file:雨停','"雨停 地圖"'):
            self.assertTrue(Query(query).matches(note),query)
        for query in ('tag:不是標籤','[missing]','[count:<2]','雨停 -地圖'):self.assertFalse(Query(query).matches(note),query)
        self.assertEqual(note.footnotes[0][0],'a')
        self.assertFalse(Query('"OR"').matches(note))
        for query in ('(',')','word OR','[missing','word unknown:x','"never','AND word','word AND','word AND AND x','-'*40+'word'):
            with self.assertRaises(ValueError,msg=query):Query(query)
    def test_index_root_cache_aliases_and_relations(self):
        create_note(self.root,'主稿','---\naliases: [雨夜]\n---\n# 夜晚\n')
        create_note(self.root,'引用','[[主稿#夜晚]]\n雨夜。\n[[雨夜]]')
        internal=self.root/'.pcs';internal.mkdir();(internal/'秘密.md').write_text('hidden')
        records,errors=inventory(self.root);self.assertFalse(errors);self.assertEqual(len(records),2)
        again,_=inventory(self.root,records);self.assertIs(again[str(self.root/'主稿.md')],records[str(self.root/'主稿.md')])
        incoming,outgoing,mentions=relations(self.root,records,self.root/'主稿.md')
        self.assertEqual(len(incoming),2);self.assertEqual(len(mentions),1)
        overrides={str(self.root/'主稿.md'):'draft'};draft,_=inventory(self.root,records,overrides)
        self.assertEqual(draft[str(self.root/'主稿.md')].text,'draft')
        disk,_=inventory(self.root,draft);self.assertNotEqual(disk[str(self.root/'主稿.md')].text,'draft')
    def test_templates_preserve_existing_and_opaque_yaml(self):
        source='---\nstatus: draft # keep\ncustom:\n  source: notebook\n---\n\n原文。'
        template='---\nstatus: new\ntags: [小說]\n---\n\n{{title}} {{date:YYYY/MM/DD}} {{time}}'
        expanded=expand_template(template,'標題',datetime(2026,10,19,3,4))
        result=apply_text(source,template_edits(source,expanded,len(source),len(source)))
        self.assertIn('status: draft # keep',result);self.assertIn('custom:\n  source: notebook',result)
        self.assertIn('tags: [小說]',result);self.assertIn('標題 2026/10/19 03:04',result)
        self.assertEqual(apply_text('',template_edits('',expanded,0,0)),expanded)
    def test_create_exclusive_root_boundary(self):
        path=create_note(self.root,'日誌/2026-10-19','原文')
        self.assertEqual(path.read_text(encoding='utf-8'),'原文')
        for value in ('../escape','assets/a','.obsidian/a','CON','日誌/2026-10-19'):
            with self.assertRaises((ValueError,OSError)):create_note(self.root,value,'no')
    def test_version_dedup_interval_integrity_and_retention(self):
        store=NotebookStore(self.root/'data');path=self.root/'Note.md'
        self.assertTrue(store.capture(path,b'one',now=100))
        self.assertFalse(store.capture(path,b'one',force=True,now=110))
        self.assertFalse(store.capture(path,b'two',now=120))
        self.assertTrue(store.capture(path,b'two',now=450))
        self.assertEqual(store.raw(store.versions(path)[0][0]),b'two')
        store.capture(path,b'three',force=True,now=900000)
        self.assertEqual(len(store.versions(path)),1)
        store.put('bookmarks',[{'title':'雨夜'}]);self.assertEqual(store.get('bookmarks')[0]['title'],'雨夜')
