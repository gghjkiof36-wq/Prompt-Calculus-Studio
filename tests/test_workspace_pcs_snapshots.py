"""Qt-free saved PCS document snapshots; uses the existing guarded fixtures."""
from local_document_fixtures import DocumentsCase
from test_local_chat_contracts import fixture
from prompt_calculus_studio.local_documents import schema
from prompt_calculus_studio.workspace_chat_attachments import prepare_pcs_attachment


class PCSSnapshots(DocumentsCase):
    def save(self, name, document):
        path = self.source / name; path.write_bytes(schema.encode(document)); return path

    def test_markdown_snapshot_keeps_saved_unicode_and_newlines(self):
        path = self.source / '筆記.md'; raw = b'\xef\xbb\xbf' + '# 已保存\r\n正文🙂'.encode()
        path.write_bytes(raw); result = prepare_pcs_attachment(path, self.source)
        self.assertEqual(result['text'], '# 已保存\r\n正文🙂'); self.assertEqual(path.read_bytes(), raw)
        self.assertIn('Markdown', result['source']); self.assertNotIn(str(self.source), result['source'])

    def test_manager_uses_named_materials_and_all_variant_groups(self):
        document = self.category(); path = self.save('素材.pcs-category', document); raw = path.read_bytes()
        result = prepare_pcs_attachment(path, self.source)
        self.assertIn('Manager', result['source']); self.assertNotIn('"file_schema_version"', result['text'])
        for item in document['payload']['items']:
            self.assertIn(item['name'], result['text'])
            for group in item.get('composition', {}).get('variants', {}).get('groups', []):
                self.assertIn(group['name'], result['text'])
        self.assertEqual(path.read_bytes(), raw)

    def test_canvas_uses_saved_outputs_and_materials_without_graph_json(self):
        document = self.workflow(); path = self.save('畫布.pcs-workflow', document)
        result = prepare_pcs_attachment(path, self.source)
        self.assertIn('Canvas', result['source']); self.assertNotIn('"profiles"', result['text'])
        for output in document['payload']['state']['multi_output']['outputs'].values():
            self.assertIn(output['name'], result['text'])

    def test_chat_uses_existing_markdown_export(self):
        document = fixture(); path = self.save('對話.pcs-chat', document)
        text = prepare_pcs_attachment(path, self.source)['text']
        self.assertIn('第一個問題。', text); self.assertIn('第一則回答。', text)
        self.assertIn('未送出草稿', text); self.assertNotIn('synthetic-test-model', text)

    def test_mismatched_native_kind_or_pcs_disguised_as_md_rejected(self):
        for name in ('錯誤.pcs-workflow', '錯誤.md'):
            path = self.save(name, self.category())
            with self.assertRaises(ValueError): prepare_pcs_attachment(path, self.source)

    def test_outside_internal_binary_and_oversized_text_rejected(self):
        outside = self.destination / '外部.md'; outside.write_text('outside', encoding='utf-8')
        binary = self.source / 'binary.md'; binary.write_bytes(b'a\x00b')
        large = self.source / 'large.md'; large.write_text('字' * 200001, encoding='utf-8')
        hidden = self.source / 'assets' / 'hidden.md'; hidden.write_text('hidden', encoding='utf-8')
        for path in (outside, binary, large, hidden):
            with self.subTest(path=path), self.assertRaises(ValueError): prepare_pcs_attachment(path, self.source)
