"""Beta3 selected-scope retention, failure boundaries, and manual round trips."""
import copy
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from test_conversation_import import sample
from prompt_calculus_studio import markdown_demos as demos, conversation_review as review, markdown_portable as portable
from prompt_calculus_studio.conversation_archive import canonical, Cancelled, load_archive
from prompt_calculus_studio.conversation_import import Destination, import_selected
from prompt_calculus_studio.retained_files import Bundle, START, COMPLETE, digest, read_local, write_new, directory_identity


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base/'notes'; self.root.mkdir()
        self.out = self.base/'out'; self.out.mkdir()
        self.document = self.root/'Note.md'; self.document.write_text('draft', encoding='utf-8')

    def source(self, raw=None):
        folder = self.root/'討論'; folder.mkdir(exist_ok=True)
        path = folder/'原始對話.json'; path.write_bytes(canonical(raw or sample()))
        return review.Source.load(path)

    def demo(self):
        folder = self.base/'demo'; folder.mkdir()
        (folder/'index.html').write_text('<script src="app.js"></script><img src="missing.png"><link href="https://example.com/a.css">', encoding='utf-8')
        (folder/'app.js').write_text('throw "not executed";', encoding='utf-8')
        bundle, record = demos.prepare('示意', folder/'index.html', dependencies=folder, source='https://example.com/demo')
        return folder, bundle, record

    def test_demo_static_relationships_missing_and_revision_preserve_original(self):
        original, bundle, record = self.demo()
        folder = demos.save(self.root, bundle)
        self.assertTrue((folder/COMPLETE).exists())
        self.assertTrue(any('missing.png' in w for w in record['warnings']))
        self.assertTrue(any('外部引用' in w for w in record['warnings']))
        self.assertEqual((original/'index.html').read_bytes(), (folder/record['original']).read_bytes())
        record['future'] = {'keep': True}
        (original/'index.html').write_text('second revision', encoding='utf-8')
        second, revised = demos.prepare('示意', original/'index.html', dependencies=original, previous=record)
        other = demos.save(self.root, second)
        self.assertEqual(record['id'], revised['id']); self.assertNotEqual(record['revision'], revised['revision'])
        self.assertEqual(revised['future'], {'keep': True})
        self.assertIn(b'<script', (folder/record['original']).read_bytes())
        self.assertEqual((other/revised['original']).read_text(), 'second revision')

    def test_preview_and_source_only_roundtrip_and_changed_asset_status(self):
        _, bundle, record = self.demo(); folder = demos.save(self.root, bundle)
        (folder/record['original']).write_bytes(b'changed')
        _, rows = demos.inspect(folder/demos.RECORD)
        self.assertIn('內容已變更，需核對', [state for _, state in rows])
        (folder/'original/app.js').unlink()
        self.assertIn('缺失或無法讀取', [state for _, state in demos.inspect(folder/demos.RECORD)[1]])
        source_only, value = demos.prepare('僅網址', source='https://example.com/a)b')
        target = demos.save(self.root, source_only)
        self.assertIn('尚無原檔', demos.markdown(value,target,self.document))
        self.assertIn('%29', demos.markdown(value,target,self.document))
        cover = self.base/'cover.png'; cover.write_bytes(b'\x89PNG\r\n\x1a\n')
        cover_only, value = demos.prepare('封面', preview=cover)
        self.assertIsNone(value['original']); self.assertEqual(len(cover_only.files),2)

    def test_cancelled_and_failed_bundle_never_get_complete_receipt(self):
        event = Event()
        bundle = Bundle((('a.txt', b'a'), ('b.txt', b'b')), {})
        def cancel_after_first(_):event.set()
        with self.assertRaises(Cancelled):write_new(self.out,'cancelled',bundle,event,cancel_after_first)
        self.assertTrue((self.out/'cancelled'/START).exists())
        self.assertFalse((self.out/'cancelled'/COMPLETE).exists())
        before = (self.out/'cancelled'/START).read_bytes()
        with self.assertRaises(FileExistsError):write_new(self.out,'cancelled',bundle)
        self.assertEqual(before,(self.out/'cancelled'/START).read_bytes())
        with patch('prompt_calculus_studio.retained_files._write',side_effect=PermissionError('injected')):
            with self.assertRaises(PermissionError):write_new(self.out,'failed',bundle)
        self.assertFalse((self.out/'failed'/COMPLETE).exists())

    def test_scope_lookup_shared_message_and_original_answer_not_other_branch(self):
        source = self.source()
        hits = source.search('共同歷史')
        self.assertEqual(len(hits),1); self.assertEqual(set(hits[0]['branches']),{'b','c'})
        self.assertIn('回答 A', source.context('a','b'))
        self.assertNotIn('回答 B', source.context('a','b'))
        self.assertIn('回答 B', source.context('a','c'))
        self.assertFalse(source.search('回答 A',branch='c'))
        self.assertEqual(len(source.search(role_filter='user')),1)
        self.assertFalse(source.search(since='2025-01-01'))
        with self.assertRaises(ValueError):source.search(since='tomorrow')
        raw = sample(); raw['mapping']['a']['children']=[]; raw['mapping'].pop('b');raw['mapping'].pop('c');raw['current_node']='a'
        self.assertIn('沒有保存',self.source(raw).context('a','a'))

    def test_chunks_preserve_all_unicode_and_long_single_message(self):
        raw=sample(); raw['mapping']['a']['message']['content']['parts']=['始'+('中文😀'*1600)+'終']
        source=self.source(raw)
        request,chunks,texts=review.packets(source,'b',capacity=1000)
        for key in ('a','b'):
            segments=[row for chunk in chunks for row in chunk if row['node']==key]
            self.assertEqual(''.join(row['text'] for row in segments),review.message_body(raw['mapping'][key]))
            self.assertEqual(segments[0]['start'],0)
            self.assertEqual(segments[-1]['end'],len(review.message_body(raw['mapping'][key])))
        self.assertEqual(request['chunk_count'],len(texts)); self.assertGreater(len(texts),4)
        self.assertFalse(any('回答 B' in text for text in texts))

    def test_manual_reply_binding_invalid_refs_and_stale_source(self):
        source=self.source(); request,chunks,_=review.packets(source,'b',pins=('a',))
        reply=dict(request_id=request['request_id'],chunk=1,items=[dict(category='AI 建議',text='候選',sources=['b'])])
        chunk,items=review.accept_reply(json.dumps(reply),request,chunks,source)
        record=review.candidate(request,items,{chunk})
        text=review.continuation(record,source)
        self.assertIn(review.message_body(source.item.raw['mapping']['a']),text)
        self.assertIn('回答 A',text); self.assertFalse(record['automatic'])
        reply['items'][0]['sources']=['c']
        with self.assertRaises(ValueError):review.accept_reply(json.dumps(reply),request,chunks,source)
        reply['items'][0]['sources']=['b']; reply['request_id']='other'
        with self.assertRaises(ValueError):review.accept_reply(json.dumps(reply),request,chunks,source)
        with self.assertRaises(ValueError):review.continuation(record,source,maximum=10)
        raw=sample(); raw['mapping']['b']['message']['content']['parts']=['new']
        source.path.write_bytes(canonical(raw))
        with self.assertRaises(ValueError):review.accept_reply(json.dumps(reply),request,chunks,source)
        self.assertIn('需重新核對',review.freshness(record,review.Source.load(source.path),'b'))
        reply['request_id']=request['request_id']
        with self.assertRaisesRegex(ValueError,'對話修訂'):
            review.accept_reply(json.dumps(reply),request,chunks,review.Source.load(source.path))
        other=self.source(sample(key='other-chat'))
        with self.assertRaisesRegex(ValueError,'對話修訂'):
            review.accept_reply(json.dumps(reply),request,chunks,other)

    def test_summary_revision_copies_and_scoped_original_reopen(self):
        source=self.source(); request,chunks,_=review.packets(source,'b',pins=('a',))
        record=review.candidate(request,[dict(category='待辦',text='人工修改',sources=['a'],edited=True,checked=True)],{1})
        text=review.continuation(record,source)
        first=review.save_candidate(source,record,text)
        second=review.save_candidate(source,review.candidate(request,record['items'],{1},record),text+'\nmanual')
        self.assertNotEqual(first,second); self.assertEqual((first/'整理.md').read_text(encoding='utf-8'),text)
        saved=json.loads((first/review.RECORD).read_text(encoding='utf-8'))
        scoped=review.Source.load(first/'原始對話.json')
        self.assertNotIn('c',scoped.item.raw['mapping'])
        self.assertTrue(review.freshness(saved,scoped,'b').startswith('來源版本相符'))
        self.assertIn('人工修改',review.continuation(saved,scoped))

    def imported(self):
        raw=sample(); raw['mapping']['b']['message']['content']['parts'].append(dict(asset_pointer='file-service://file-kept'))
        raw['mapping']['c']['message']['content']['parts']=['UNSELECTED_SECRET',dict(asset_pointer='file-service://file-secret')]
        path=self.base/'archive.zip'
        with ZipFile(path,'w') as archive:
            archive.writestr('conversations.json',canonical([raw]))
            archive.writestr('file-kept.png',b'kept'); archive.writestr('file-secret.png',b'PRIVATE_ASSET')
            archive.writestr('account.json',b'ACCOUNT_SECRET')
        result=import_selected(load_archive(path),(0,),Destination.choose(self.root),Event())
        self.assertEqual(len(result.completed),1)
        return review.Source.load(result.completed[0].parent/'原始對話.json')

    def test_single_branch_package_excludes_other_raw_and_assets(self):
        source=self.imported(); document=source.path.parent/'分支-001-001.md'
        text=document.read_text(encoding='utf-8')+'\nunsaved draft'
        package=portable.prepare(self.root,document,text,source,('b',))
        combined=b'\n'.join(data for _,data in package.bundle.files)
        for secret in (b'UNSELECTED_SECRET',b'PRIVATE_ASSET',b'ACCOUNT_SECRET'):
            self.assertNotIn(secret,combined)
        self.assertIn(b'kept',combined); self.assertIn(b'unsaved draft',combined)
        folder=portable.export_folder(self.out,package); zipped=portable.export_zip(self.out,package)
        loaded=portable.load_zip(zipped)
        self.assertEqual(dict(package.bundle.files),dict(loaded.bundle.files))
        first,entry=portable.import_zip(zipped,self.out); second,_=portable.import_zip(zipped,self.out)
        self.assertNotEqual(first,second)
        entry.write_text('independent edit',encoding='utf-8')
        self.assertNotIn('independent edit',document.read_text(encoding='utf-8'))
        moved=folder/'moved-unused'
        self.assertFalse(moved.exists())
        original=source.path.read_bytes(); source.path.rename(source.path.with_suffix('.unavailable'))
        reopened=review.Source.load(folder/source.path.relative_to(self.root))
        self.assertIn('回答 A',reopened.context('a','b'))
        self.assertNotIn('c',reopened.item.raw['mapping'])
        self.assertIn(b'UNSELECTED_SECRET',original)

    def test_portable_demo_and_missing_reference_after_original_unavailable(self):
        original,bundle,record=self.demo(); saved=demos.save(self.root,bundle)
        text=demos.markdown(record,saved,self.document)+'\n[other](absent.md)'
        package=portable.prepare(self.root,self.document,text)
        folder=portable.export_folder(self.out,package)
        copied=folder/saved.relative_to(self.root)/demos.RECORD
        self.assertEqual(len(demos.inspect(copied)[1]),2)
        self.assertTrue(any('absent.md' in w for w in package.warnings))
        self.assertTrue((original/'index.html').exists())

    def test_export_rejects_credentials_and_other_conversation_link(self):
        source=self.source(); (self.root/'auth.json').write_text('{"secret":true}')
        with self.assertRaises(ValueError):portable.prepare(self.root,self.document,'[auth](auth.json)')
        other=source.path.parent/'分支.md'; other.write_text('SECRET')
        package=portable.prepare(self.root,self.document,'![[討論/分支.md]]')
        self.assertNotIn(b'SECRET',b''.join(data for _,data in package.bundle.files))
        self.assertTrue(any('未選取' in warning for warning in package.warnings))

    def test_zip_slip_symlink_duplicate_and_corrupt_payload_fail(self):
        for name,other in (('../escape.md',None),('a.md','A.md')):
            path=self.base/'malicious.zip'
            with ZipFile(path,'w') as archive:
                archive.writestr(name,b'unsafe')
                if other:archive.writestr(other,b'collision')
            with self.assertRaises(ValueError):portable.load_zip(path)
        package=portable.prepare(self.root,self.document,'hello')
        path=portable.export_zip(self.out,package)
        bad=self.base/'changed.zip'
        with ZipFile(path) as archive,ZipFile(bad,'w') as output:
            for name in archive.namelist():output.writestr(name,b'changed' if name==package.entry else archive.read(name))
        with self.assertRaises(ValueError):portable.import_zip(bad,self.out)
        self.assertFalse((self.base/'escape.md').exists())

    def test_source_snapshot_is_fixed_during_export_and_cancel_is_incomplete(self):
        attachment=self.root/'a.txt'; attachment.write_text('before')
        package=portable.prepare(self.root,self.document,'[file](a.txt)\nunsaved')
        attachment.write_text('after')
        folder=portable.export_folder(self.out,package)
        self.assertEqual((folder/'a.txt').read_text(),'before')
        self.assertEqual((folder/'Note.md').read_text(),'[file](a.txt)\nunsaved')
        event=Event()
        with self.assertRaises(Cancelled):portable.export_zip(self.out,package,event,lambda _:event.set())
        incomplete=list(self.out.glob('*.incomplete.zip'))
        self.assertEqual(len(incomplete),1)
        with self.assertRaises(ValueError):portable.load_zip(incomplete[0])

    def test_body_branch_mismatch_is_blocked_before_any_export(self):
        source=self.imported(); other=source.path.parent/'分支-002-001.md'
        with self.assertRaisesRegex(ValueError,'未選分支'):
            portable.prepare(self.root,other,other.read_text(encoding='utf-8'),source,('b',))
        moved=self.root/'renamed.md';moved.write_bytes(other.read_bytes())
        with self.assertRaisesRegex(ValueError,'未選分支'):
            portable.prepare(self.root,moved,moved.read_text(encoding='utf-8'),source,('b',))
        self.assertFalse(list(self.out.iterdir()))

    def test_summary_preserves_selected_resources_through_repeated_copies(self):
        source=self.imported(); request,_,_=review.packets(source,'b')
        record=review.candidate(request,[dict(category='待辦',text='人工候選',sources=['b'])],{1})
        first=review.save_candidate(source,record,review.continuation(record,source))
        old=json.loads((first/review.RECORD).read_text(encoding='utf-8'))
        scoped=review.Source.load(first/'原始對話.json')
        self.assertEqual([p.read_bytes() for p in (first/'assets').iterdir()],[b'kept'])
        second=review.save_candidate(scoped,old,review.continuation(old,scoped))
        new=json.loads((second/review.RECORD).read_text(encoding='utf-8'))
        second_source=review.Source.load(second/'原始對話.json')
        self.assertTrue(review.freshness(new,second_source,'b').startswith('來源版本相符'))
        package=portable.prepare(self.root,first/'整理.md',(first/'整理.md').read_text(encoding='utf-8'),scoped,('b',))
        folder=portable.export_folder(self.out,package)
        path=folder/first.relative_to(self.root)
        exported=json.loads((path/review.RECORD).read_text(encoding='utf-8'))
        self.assertTrue(review.freshness(exported,review.Source.load(path/'原始對話.json'),'b').startswith('來源版本相符'))
        self.assertIn(b'kept',b''.join(data for _,data in package.bundle.files))
        self.assertNotIn(b'PRIVATE_ASSET',b''.join(data for _,data in package.bundle.files))
        next((first/'assets').iterdir()).write_bytes(b'externally changed')
        with self.assertRaisesRegex(ValueError,'已變更'):
            review.save_candidate(scoped,old,review.continuation(old,scoped))

    def test_generated_source_identity_survives_scope_extraction(self):
        raw=sample();raw.pop('id')
        source=self.source(raw); request,_,_=review.packets(source,'b')
        record=review.candidate(request,[dict(category='待核對',text='候選',sources=['b'])],{1})
        folder=review.save_candidate(source,record,review.continuation(record,source))
        saved=json.loads((folder/review.RECORD).read_text(encoding='utf-8'))
        copied=review.Source.load(folder/'原始對話.json')
        self.assertEqual(copied.item.key,source.item.key)
        self.assertTrue(review.freshness(saved,copied,'b').startswith('來源版本相符'))

    def test_destination_replacement_during_zip_read_is_rejected(self):
        package=portable.prepare(self.root,self.document,'hello')
        zipped=portable.export_zip(self.out,package)
        destination=self.base/'selected';destination.mkdir()
        expected=directory_identity(destination)
        original=portable.load_zip
        def replaced(*args,**kwargs):
            result=original(*args,**kwargs)
            destination.rename(self.base/'original-selected');destination.mkdir()
            return result
        with patch.object(portable,'load_zip',side_effect=replaced):
            with self.assertRaisesRegex(ValueError,'已被替換'):
                portable.import_zip(zipped,destination,root_identity=expected)
        self.assertFalse(list(destination.iterdir()))
        self.assertFalse(list((self.base/'original-selected').iterdir()))

    def test_whole_prepare_rechecks_earlier_files(self):
        folder=self.base/'changing';folder.mkdir()
        a,b=folder/'a.txt',folder/'b.txt';a.write_text('A1');b.write_text('B1')
        def mutate(message):
            if message.endswith('b.txt'):a.write_text('A2');b.write_text('B2')
        with self.assertRaisesRegex(ValueError,'來源在預覽期間已變更'):
            demos.prepare('demo',a,dependencies=folder,progress=mutate)


if __name__=='__main__':unittest.main()
