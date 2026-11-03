"""Second-stage local file, revision and selection contracts on isolated sources."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase, skipUnless
from unittest.mock import patch

from prompt_calculus_studio.conversation_archive import canonical, Cancelled
from prompt_calculus_studio.work_resources import bundles, source_io
from prompt_calculus_studio.work_resources.catalog import Catalog


def example():
    def node(key,parent,children,text):
        return dict(id=key,parent=parent,children=children,message=dict(id='m-'+key,
            author=dict(role='user' if parent is None else 'assistant'),content=dict(parts=[text])))
    return dict(title='河岸會議',id='synthetic',current_node='east',mapping={
        'root':node('root',None,['east','west'],'這一輪要去哪裡？'),
        'east':node('east','root',[],'選擇東岸'),
        'west':node('west','root',[],'排除西岸')},
        opaque_cache={'sibling':'排除西岸'})


@skipUnless(os.name=='nt','Windows pinned roots')
class WorkResourcesTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.data=self.base/'data'; self.data.mkdir()
        self.folder=self.base/'中文資料';self.folder.mkdir()
        self.store=Catalog(self.data)
        self.key=self.store.register(self.folder)

    def test_scope_and_exact_selected_bytes_are_independent(self):
        (self.folder/'甲').mkdir(); (self.folder/'乙').mkdir()
        raw=b'\xef\xbb\xbf'+'前🙂\r\n選段\r\n後'.encode()
        (self.folder/'甲/同名.md').write_bytes(raw)
        (self.folder/'乙/同名.md').write_text('另一份',encoding='utf8')
        (self.folder/'空白.md').write_bytes(b'')
        (self.folder/'圖片.png').write_bytes(b'example')
        self.assertIn('1 個格式未支援',self.store.rebuild(self.key))
        rows=self.store.results(self.key)
        self.assertEqual(len(rows),3)
        row=next(r for r in rows if r['title'].startswith('甲'))
        selected=bundles.select_document(self.store,row,(1,5))
        self.assertEqual(selected.content,'🙂\r\n選段'.encode())
        self.assertEqual(raw[slice(*selected.ref['range'])],selected.content)
        empty=next(r for r in rows if r['title']=='空白.md')
        other=bundles.select_document(self.store,empty)
        key=bundles.save(self.store,[selected,other],'部分原文')
        (self.folder/row['title']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'已變更'):
            bundles.save(self.store,[selected],'過期選取')
        self.store.remove(self.key)
        manifest,files=bundles.open_bundle(Catalog(self.data),key)
        self.assertEqual(files,[selected.content,b''])
        self.assertEqual(manifest['model_budget'],'unchecked')
        self.assertEqual(bundles.current_state(self.store,selected.ref),'來源目前不可用')

    def test_archive_preview_preserves_only_chosen_nodes(self):
        archive=self.folder/'會議.json'
        archive.write_bytes(canonical([example()]))
        sid=self.store.register(archive,'archive'); sha=self.store.rebuild(sid)
        row=self.store.results(sid,'東岸')[0]
        story=bundles.load_story(self.store,row)
        self.assertEqual(row['node'],'east')
        entry=bundles.select_branch(row,story,story.branch_for(row['node']))
        self.assertNotIn(b'west',entry.content+entry.reference)
        self.assertNotIn('排除西岸'.encode(),entry.content+entry.reference)
        self.assertEqual(self.store.original(sid,sha)[0],archive.read_bytes())
        destination=self.base/'匯出';destination.mkdir()
        from prompt_calculus_studio.retained_files import directory_identity
        exported=bundles.export_original(self.store,row,destination,directory_identity(destination))
        self.assertEqual(exported.read_bytes(),archive.read_bytes())
        with self.assertRaises(ValueError):
            bundles.select_branch(dict(row,source='另一個來源'),story,story.current)
        with self.assertRaisesRegex(ValueError,'分成多包'):
            bundles.save(self.store,[entry],'過量',capacity=10)
        key=bundles.save(self.store,[entry],'所選分支')
        self.assertEqual(bundles.open_bundle(self.store,key)[1][0],entry.content)

    def test_cancel_relink_and_rename_do_not_replace_old_snapshot(self):
        file=self.folder/'原名.md';file.write_text('第一版',encoding='utf8')
        self.store.rebuild(self.key)
        entry=bundles.select_document(self.store,self.store.results(self.key)[0])
        file.rename(self.folder/'新名.md');self.store.rebuild(self.key)
        self.assertEqual(bundles.current_state(self.store,entry.ref),'來源已改名')
        flag=Event();flag.set()
        with self.assertRaises(Cancelled):self.store.rebuild(self.key,flag)
        self.assertEqual(len(self.store.results(self.key,'第一版')),1)
        target=self.base/'新來源';target.mkdir();self.store.relink(self.key,target)
        self.assertEqual(self.store.results(self.key,'第一版'),[])
        (target/'新名.md').write_text('第二版',encoding='utf8')
        self.store.rebuild(self.key)
        self.assertEqual(len(self.store.results(self.key,'第二版')),1)

    def test_late_publication_and_failed_package_do_not_commit(self):
        for i in range(66):
            (self.folder/f'{i}.md').write_text(str(i),encoding='utf8')
        self.store.rebuild(self.key)
        row=self.store.results(self.key)[0]
        entry=bundles.select_document(self.store,row)
        with patch('prompt_calculus_studio.retained_files._write',side_effect=OSError('合成寫入失敗')):
            with self.assertRaises(OSError):bundles.save(self.store,[entry],'未完成')
        self.assertEqual(bundles.saved(self.store),[])
        target=self.base/'其他';target.mkdir()
        with self.assertRaisesRegex(ValueError,'重新指定'):
            self.store.rebuild(self.key,progress=lambda _:self.store.relink(self.key,target))
        self.assertEqual(self.store.results(self.key),[])

    def test_no_hard_link_and_no_source_guard(self):
        outside=self.base/'outer.txt';outside.write_text('outside',encoding='utf8')
        os.link(outside,self.folder/'連結.md')
        (self.folder/'普通.md').write_text('ordinary',encoding='utf8')
        with source_io.verified_stream(self.folder,'普通.md') as (stream,_):
            self.assertFalse(any(p.name.endswith('.guard') for p in self.folder.iterdir()))
            self.assertEqual(stream.read(),b'ordinary')
        self.store.rebuild(self.key)
        self.assertEqual([r['title'] for r in self.store.results(self.key)],['普通.md'])
