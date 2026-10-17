import copy
import json
from unittest.mock import patch

from local_document_fixtures import DocumentsCase
from prompt_calculus_studio.local_documents import schema, category


class SchemaTests(DocumentsCase):
    def reject(self, document, code):
        with self.assertRaises(schema.DocumentError) as error:
            schema.encode(document)
        self.assertEqual(error.exception.code, code)

    def test_full_roundtrip_and_opaque_optional_extensions(self):
        document = self.category()
        document['extensions']['org.example'] = {'label': '完整資料', 'empty': '', 'value': None}
        document['payload']['items'][0]['optional_annotation'] = {'color': 'blue'}
        self.assertEqual(schema.decode(schema.encode(document)), document)
        self.assertEqual(self.state, self.before)

    def test_unknown_file_version_is_separate_from_legacy_state(self):
        document = self.category()
        self.assertEqual(document['file_schema_version'], 1)
        self.assertEqual(document['payload']['state_version'], 7)
        for version in (0, 2, True, '1'):
            document['file_schema_version'] = version
            self.reject(document, 'unsupported')

    def test_unknown_required_semantics_and_kind_fail_closed(self):
        document = self.category()
        document['required_features'] = ['new-reference-type']
        self.reject(document, 'unsupported')
        document['required_features'] = []
        document['kind'] = 'executable'
        self.reject(document, 'unsupported')

    def test_unknown_outer_field_is_not_dropped(self):
        document = self.category()
        document['future_graph'] = {'meaning': 'unknown'}
        self.reject(document, 'unsupported')

    def test_invalid_uuid_and_self_parent(self):
        document = self.category()
        original = copy.deepcopy(document)
        document['document_id'] = 'name-as-id'
        self.reject(document, 'invalid')
        document = original
        document['revision']['parent'] = document['revision']['id']
        self.reject(document, 'invalid')

    def test_duplicate_json_fields_and_invalid_utf8(self):
        for raw in (b'{"kind":1,"kind":2}', b'\xff', b'{', b'{"a": NaN}'):
            with self.subTest(raw=raw), self.assertRaises(schema.DocumentError):
                schema.decode(raw)

    def test_repeated_item_id_and_wrong_owner_rejected(self):
        document = self.category()
        original = copy.deepcopy(document)
        document['payload']['items'].append(copy.deepcopy(document['payload']['items'][0]))
        self.reject(document, 'invalid')
        original['payload']['items'][0]['module'] = 'missing-category'
        self.reject(original, 'invalid')

    def test_cycles_non_json_nonfinite_depth_and_bytes_limits(self):
        for value in (set(), float('nan'), float('inf')):
            document = self.category()
            document['extensions']['test.value'] = value
            self.reject(document, 'invalid')
        document = self.category()
        document['extensions']['test.cycle'] = document
        self.reject(document, 'invalid')
        document = self.category()
        child = document['extensions']
        for _ in range(schema.MAX_DEPTH + 1):
            child['test.deep'] = {}
            child = child['test.deep']
        self.reject(document, 'limit')
        with patch.object(schema, 'MAX_BYTES', 10):
            self.reject(self.category(), 'limit')
            with self.assertRaises(schema.DocumentError):
                schema.decode(b' ' * 11)

    def test_core_type_validation_not_replaced_by_envelope(self):
        document = self.category()
        document['payload']['items'][0]['pinned'] = 'yes'
        self.reject(document, 'invalid')

    def test_truncated_or_unsupported_input_is_never_rewritten(self):
        path = self.destination / 'unknown.pcs-category'
        value = self.category()
        value['file_schema_version'] = 98
        raw = json.dumps(value).encode()
        path.write_bytes(raw)
        from prompt_calculus_studio.local_documents.io import load
        with self.assertRaises(schema.DocumentError):
            load(self.destination, path.name)
        self.assertEqual(path.read_bytes(), raw)

    def test_empty_category_is_valid_and_source_order_preserved(self):
        document = self.category()
        expected = [i['id'] for i in self.state['items'] if i['module'] == self.ids['category_a']]
        self.assertEqual([i['id'] for i in document['payload']['items']], expected)
        document['payload']['items'] = []
        self.assertEqual(category.to_state(schema.decode(schema.encode(document)))['items'], [])

    def test_core_supported_deep_flow_is_not_cut_by_envelope_limit(self):
        from prompt_calculus_studio import prompt_materials
        root = prompt_materials.new('text', 'leaf')
        root['prompt'] = 'retained'
        for _ in range(31):
            parent = prompt_materials.new('flow', 'nested')
            parent['sequence']['groups'][0]['modules'] = [root]
            root = parent
        document = self.category()
        item = document['payload']['items'][0]
        item.update(material_type='flow', composition=root)
        self.assertEqual(schema.decode(schema.encode(document)), document)
