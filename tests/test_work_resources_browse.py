"""Large lists and checked selections retain the existing source/snapshot boundaries."""
from threading import Event
import os
from unittest import TestCase, skipUnless
from unittest.mock import patch
import test_work_resources as fixtures
from prompt_calculus_studio.conversation_archive import canonical, Cancelled
from prompt_calculus_studio.work_resources import bundles


@skipUnless(os.name == 'nt', 'Windows pinned roots')
class ResourceBrowseTests(TestCase):
    setUp = fixtures.WorkResourcesTests.setUp
    def test_filters_before_page_and_one_hit_per_conversation(self):
        for index in range(126):
            (self.folder/f'文件-{index:03}.md').write_text('搜尋共同字 ' + str(index), encoding='utf8')
        self.store.rebuild(self.key)
        first, total = self.store.browse('共同字', limit=50)
        last, _ = self.store.browse('共同字', offset=100, limit=50)
        self.assertEqual((len(first), len(last), total), (50, 26, 126))
        self.assertEqual(last[-1]['title'], '文件-125.md')
        archive = self.folder/'討論.json'
        thread = fixtures.example()
        for value in thread['mapping'].values():
            value['message']['content']['parts'] = ['搜尋共同字']
        archive.write_bytes(canonical([thread]))
        sid = self.store.register(archive, 'archive')
        self.store.rebuild(sid)
        rows, total = self.store.browse('共同字', kind='archive')
        self.assertEqual((len(rows), total), (1, 1))
        self.assertTrue(rows[0]['node'])
        self.assertEqual(self.store.browse('共同字', source=self.key)[1], 126)
        self.assertEqual(self.store.browse('%_')[1], 0)
        flag = Event(); flag.set()
        with self.assertRaises(Cancelled):
            self.store.browse(cancel=flag)

    def test_batch_ranges_read_once_and_archive_defaults_to_current_branch(self):
        (self.folder/'片段.md').write_bytes('甲🙂\r\n乙丙丁'.encode())
        self.store.rebuild(self.key)
        doc = self.store.browse()[0][0]
        archive = self.folder/'討論.json'
        archive.write_bytes(canonical([fixtures.example()]))
        sid = self.store.register(archive, 'archive')
        self.store.rebuild(sid)
        row = self.store.browse(kind='archive')[0][0]
        with patch.object(bundles, 'document', wraps=bundles.document) as read:
            selected = bundles.materialize(self.store, [dict(row=doc, spans=[(1, 2), (3, 5)]), dict(row=row)])
            self.assertEqual(read.call_count, 1)
        self.assertEqual(selected[0].content, '🙂'.encode())
        self.assertIn('選擇東岸'.encode(), selected[-1].content)
        self.assertNotIn('排除西岸'.encode(), selected[-1].content)
        key = bundles.save(self.store, selected, '整合')
        (self.folder/'片段.md').write_text('來源已修改', encoding='utf8')
        with self.assertRaisesRegex(ValueError, '來源已變更'):
            bundles.materialize(self.store, [dict(row=doc)])
        self.assertEqual(bundles.open_bundle(self.store, key)[1][0], '🙂'.encode())

    def test_many_saved_groups_read_summaries_without_contents(self):
        (self.folder/'a.md').write_text('保存版本', encoding='utf8')
        self.store.rebuild(self.key)
        selection = bundles.select_document(self.store, self.store.browse()[0][0])
        for index in range(103):
            bundles.save(self.store, [selection], f'組合 {index:03}')
        with patch.object(bundles, 'open_bundle', wraps=bundles.open_bundle) as read:
            rows = bundles.saved_rows(self.store)
        self.assertEqual(len(rows), 103)
        self.assertTrue(all(call.kwargs['contents'] is False for call in read.call_args_list))
        self.assertTrue(all(row['count'] == 1 for row in rows))
