"""Per-message version lineage and full-path copies; synthetic data only."""
import copy
import unittest
from unittest.mock import patch

from prompt_calculus_studio.local_documents import chat, identity, schema
from prompt_calculus_studio.local_documents.chat_versions import MessageVersionIndex
from test_local_chat_attachments import image_item, text_item
from test_local_chat_branch import conversation, RAW
from test_local_chat_contracts import attempt, uid
from test_local_chat_edits import RevisionOwnerContracts


def edit(payload, position, text):
    source = payload['turns'][position]
    chat.archive_revision(payload, reason='edit', turn_id=source['id'])
    value = copy.deepcopy(source)
    value.update(id=uid(), text=text, attempts=[])
    for item in value.get('attachments', []):
        item['id'] = uid()
    payload['turns'] = payload['turns'][:position] + [value]
    value['attempts'].append(attempt(RAW, context=chat.context_references(payload, retry=True)))
    return value['id']


def append(payload, text):
    value = dict(id=uid(), text=text, attempts=[attempt('後續回答', context=chat.context_references(payload))])
    payload['turns'].append(value)
    return value['id']


def history():
    value = conversation(); payload = value['payload']
    payload['turns'][0]['text'] = 'A'
    payload['turns'][0]['attachments'] = [text_item(), image_item()]
    original = payload['turns'][0]['id']
    edited = edit(payload, 0, 'B')
    other = append(payload, '別則訊息')
    other_edit = edit(payload, 1, '別則訊息的新版本')
    old = copy.deepcopy(payload['revisions'][0])
    chat.archive_revision(payload, reason='restore')
    payload.update(turns=old['turns'], system_prompt=old['system_prompt'])
    current = edit(payload, 0, 'C')
    append(payload, 'C 的後續問題')
    chat.sync_features(value)
    schema.validate(value)
    return value, dict(A=original, B=edited, C=current, other=other, other_edit=other_edit)


class MessageVersionContracts(unittest.TestCase):
    def test_edit_restore_edit_tracks_only_the_selected_message_lineage(self):
        value, ids = history(); payload = value['payload']; before = copy.deepcopy(payload)
        index = MessageVersionIndex(payload)
        versions = index.versions(ids['C'])
        self.assertEqual([v['text'] for v in versions], ['C', 'B', 'A'])
        self.assertEqual([v['current'] for v in versions], [True, False, False])
        self.assertEqual(index.count(ids['C']), 3)
        self.assertEqual(index.count(payload['turns'][1]['id']), 1)
        self.assertEqual(payload, before)
        versions[0]['attachments'][0]['text'] = 'caller cannot mutate the original'
        self.assertEqual(payload, before)

    def test_same_words_are_not_evidence_of_a_version(self):
        value = conversation(); payload = value['payload']
        for turn in payload['turns']:
            turn['text'] = '相同文字'
        index = MessageVersionIndex(payload)
        for turn in payload['turns']:
            self.assertEqual(index.count(turn['id']), 1)
        with self.assertRaises(ValueError):
            index.branch_payload(payload['turns'][0]['id'], payload['turns'][1]['id'])

    def test_restore_old_version_keeps_its_number_separate_from_current_flag(self):
        value, ids = history(); payload = value['payload']
        old = copy.deepcopy(payload['revisions'][0])
        chat.archive_revision(payload, reason='restore')
        payload.update(turns=old['turns'], system_prompt=old['system_prompt'])
        versions = MessageVersionIndex(payload).versions(ids['A'])
        self.assertEqual([(v['text'], v['number'], v['current']) for v in versions],
                         [('A', 1, True), ('C', 3, False), ('B', 2, False)])

    def test_selected_old_version_copies_its_whole_continuation_and_prior_history(self):
        value, ids = history(); index = MessageVersionIndex(value['payload'])
        selected, revision_id = index.branch_payload(ids['C'], ids['B'])
        self.assertEqual([t['text'] for t in selected['turns']], ['B', '別則訊息的新版本'])
        self.assertEqual(selected['turns'][0]['attempts'][0]['text'], RAW)
        self.assertEqual(len(selected['turns'][0]['attachments']), 2)
        self.assertEqual(selected['draft'], '')
        self.assertNotIn('draft_attachments', selected)
        self.assertEqual(revision_id, value['payload']['revisions'][2]['id'])
        self.assertEqual(selected['revisions'], value['payload']['revisions'][:2])
        self.assertEqual(MessageVersionIndex(selected).count(ids['B']), 2)

    def test_duplicate_roundtrip_preserves_lineage_and_rekeys_all_owned_ids(self):
        value, ids = history(); original = copy.deepcopy(value)
        cloned = schema.decode(schema.encode(identity.duplicate(value).document))
        current = cloned['payload']['turns'][0]
        self.assertNotIn(current['id'], ids.values())
        versions = MessageVersionIndex(cloned['payload']).versions(current['id'])
        self.assertEqual([v['text'] for v in versions], ['C', 'B', 'A'])
        self.assertFalse(set(ids.values()) & {v['id'] for v in versions})
        self.assertEqual(cloned['payload']['revisions'][0]['turns'][0]['id'],
                         cloned['payload']['revisions'][3]['turns'][0]['id'])
        self.assertEqual(value, original)

    def test_lost_prefix_identity_is_not_reconstructed_from_matching_content(self):
        value = conversation(); payload = value['payload']
        edit(payload, 1, '已修改第二題')
        # Simulate the old independently remapped archive: the preceding ID no
        # longer proves this edited message belongs to the active prefix.
        archived = payload['revisions'][0]
        archived.update(turns=chat.clone_payload(dict(version=1, draft='', system_prompt='',
                                                      turns=archived['turns']))['turns'])
        archived['turn_id'] = archived['turns'][1]['id']
        chat.validate_payload(payload)
        index = MessageVersionIndex(payload)
        self.assertEqual(index.count(payload['turns'][1]['id']), 1)


class MessageVersionOwnerContracts(unittest.TestCase):
    setUpClass = classmethod(RevisionOwnerContracts.setUpClass.__func__)
    setUp = RevisionOwnerContracts.setUp
    tearDown = RevisionOwnerContracts.tearDown

    def install_history(self):
        value, ids = history()
        self.owner.payload = copy.deepcopy(value['payload'])
        self.owner.document['payload'] = copy.deepcopy(value['payload'])
        chat.sync_features(self.owner.document)
        return ids

    def test_queries_are_read_only_and_stream_refresh_reuses_the_small_index(self):
        ids = self.install_history()
        before = copy.deepcopy(self.owner.payload); raw = self.path.read_bytes()
        with patch('prompt_calculus_studio.local_documents.chat_versions.MessageVersionIndex',
                   wraps=MessageVersionIndex) as build:
            for _ in range(200):
                for turn in self.owner.payload['turns']:
                    self.owner.message_version_count(turn['id'])
            self.assertEqual(build.call_count, 1)
            self.owner.payload['turns'][-1]['attempts'][-1]['text'] += ' delta'
            self.owner.message_version_count(ids['C'])
            self.assertEqual(build.call_count, 1)
            self.owner.payload['turns'][-1]['attempts'][-1]['text'] = before['turns'][-1]['attempts'][-1]['text']
        self.assertEqual([v['text'] for v in self.owner.message_versions(ids['C'])], ['C', 'B', 'A'])
        self.assertEqual(self.owner.payload, before)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.service.submitted, [])

    def test_version_branch_preserves_source_and_new_identity_after_reopen(self):
        ids = self.install_history()
        before = copy.deepcopy(self.owner.payload); raw = self.path.read_bytes()
        candidate = self.owner.branch_message_version_document(ids['C'], ids['B'])
        reopened = schema.decode(schema.encode(candidate))
        self.assertNotEqual(reopened['document_id'], self.owner.key)
        self.assertEqual([t['text'] for t in reopened['payload']['turns']], ['B', '別則訊息的新版本'])
        current = reopened['payload']['turns'][0]['id']
        self.assertEqual(MessageVersionIndex(reopened['payload']).count(current), 2)
        self.assertEqual(reopened['extensions']['pcs.chat_message_version']['version_id'], ids['B'])
        self.assertEqual(self.owner.payload, before)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.service.submitted, [])

    def test_current_branch_interrupts_a_copy_without_cancelling_source_or_accepting_late_events(self):
        ids = self.install_history()
        turn = self.owner.payload['turns'][-1]
        turn['attempts'][-1].update(status='receiving', text='partial')
        candidate = self.owner.branch_message_version_document(ids['C'], ids['C'])
        self.assertEqual(candidate['payload']['turns'][-1]['attempts'][-1]['status'], 'interrupted')
        self.assertEqual(turn['attempts'][-1]['status'], 'receiving')
        self.assertNotEqual(candidate['payload']['turns'][-1]['attempts'][-1]['request'], turn['attempts'][-1]['request'])
        self.assertEqual(self.service.cancelled, [])
        self.assertEqual(self.service.submitted, [])
        turn['attempts'][-1]['status'] = 'complete'

    def test_branch_rejects_other_message_version_and_stale_owner(self):
        ids = self.install_history()
        with self.assertRaises(ValueError):
            self.owner.branch_message_version_document(ids['C'], ids['other'])
        with self.assertRaises(ValueError):
            self.owner.message_versions(uid())
        self.owner._disposed = True
        self.assertEqual(self.owner.message_version_count(ids['C']), 0)
        with self.assertRaises(ValueError):
            self.owner.branch_message_version_document(ids['C'], ids['B'])
        self.owner._disposed = False


if __name__ == '__main__':
    unittest.main()
