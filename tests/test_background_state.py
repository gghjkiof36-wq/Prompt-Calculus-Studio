"""Synthetic SQLite handoff tests. No ComfyUI imports, network, UI or GPU."""
import copy
import importlib.util
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location('pcs_background_state', Path(__file__).resolve().parents[1] / 'comfyui_prompt_calculus_studio/background_state.py')
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)
BackgroundState, prepare_seed = module.BackgroundState, module.prepare_seed


class FakeService:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()


def graphs():
    kinds = ['KSampler', 'EmptyLatentImage', 'VAEDecode', 'CLIPTextEncode', 'PreviewImage',
             'CLIPTextEncode', 'CheckpointLoaderSimple', 'LoraLoader']
    widgets = [[10, 'increment', 28, 7.0, 'euler', 'normal', 1.0], [1216, 832, 2], [], ['',], [],
               ['literal positive'], ['fixture.safetensors'], ['fixture-lora.safetensors', 0.5, 0.6]]
    nodes = [dict(id=i + 1, type=kind, mode=0, widgets_values=widgets[i], inputs=[])
             for i, kind in enumerate(kinds)]
    links = []
    for origin, slot, dest, name, kind in [
        (8, 0, 1, 'model', 'MODEL'), (6, 0, 1, 'positive', 'CONDITIONING'),
        (4, 0, 1, 'negative', 'CONDITIONING'), (2, 0, 1, 'latent_image', 'LATENT'),
        (1, 0, 3, 'samples', 'LATENT'), (7, 2, 3, 'vae', 'VAE'),
        (8, 1, 4, 'clip', 'CLIP'), (3, 0, 5, 'images', 'IMAGE'),
        (8, 1, 6, 'clip', 'CLIP'), (7, 0, 8, 'model', 'MODEL'), (7, 1, 8, 'clip', 'CLIP'),
    ]:
        inputs = nodes[dest - 1]['inputs']; link_id = len(links) + 1
        links.append([link_id, origin, slot, dest, len(inputs), kind])
        inputs.append(dict(name=name, type=kind, link=link_id))
    visual = dict(id='native-fixture', version=0.4, nodes=nodes, links=links)
    output = {}
    for node in nodes:
        fields = {name: node['widgets_values'][i] for i, name in enumerate(module.WIDGETS[node['type']]) if name}
        for item in node['inputs']:
            link = links[item['link'] - 1]; fields[item['name']] = [str(link[1]), link[2]]
        output[str(node['id'])] = dict(class_type=node['type'], inputs=fields)
    seed = dict(node_id='1', timing='after', hasExecuted=False, policy='increment', seed=10, min=0, max=100, step2=1)
    return visual, output, seed


class BackgroundStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.service = FakeService(Path(self.tmp.name) / 'integration.sqlite3')
        self.core = BackgroundState(self.service, draw=lambda: 0.5)
        self.key = dict(library_id='library', workspace_id='workspace', workflow_id='workflow',
                        frontend_id='native-fixture', path='folder/fixture.json')
        self.serial = 0

    def value(self, **kwargs):
        self.serial += 1
        return dict(key=copy.deepcopy(self.key), server_epoch=self.core.server_epoch,
                    op_id=f'op-{self.serial}', source_revision='a' * 64, **kwargs)

    def capture(self, mutate=None, base=0):
        lease = self.core.lease(dict(key=self.key, session='tab', base_revision=base))
        visual, output, seed = graphs()
        value = self.value(lease_id=lease['lease_id'], lease_epoch=lease['lease_epoch'], base_revision=base,
                           edit_seq=0, visual=visual, output=output, seed=seed, capability=copy.deepcopy(module.CAPABILITY))
        if mutate:
            mutate(value)
        receipt = self.core.capture(value)
        return value, receipt

    def released(self, mutate=None):
        capture, receipt = self.capture(mutate)
        release = self.value(lease_id=capture['lease_id'], lease_epoch=capture['lease_epoch'],
                             edit_seq=receipt['edit_seq'], revision=receipt['revision'], digest=receipt['digest'])
        self.core.release(release)
        return capture, receipt, release

    def start(self, mutate=None):
        capture, receipt, release = self.released(mutate)
        request = self.value(revision=receipt['revision'], digest=receipt['digest'])
        return capture, receipt, request, self.core.start(request)

    def snapshot(self):
        return self.core.snapshot(dict(key=self.key))

    def recapture(self, mutate=None):
        current = self.snapshot()
        lease = self.core.lease(dict(key=self.key, session='tab', base_revision=current['revision']))
        content = copy.deepcopy(current['content'])
        if mutate:
            mutate(content)
        value = self.value(lease_id=lease['lease_id'], lease_epoch=lease['lease_epoch'],
                           base_revision=current['revision'], edit_seq=lease['edit_seq'] + 1)
        value.update(content)
        return value, self.core.capture(value)

    def test_complete_handoff_freezes_actual_graph_and_advances_only_after_ack(self):
        capture, receipt, request, operation = self.start()
        self.assertTrue(operation['submit_allowed'])
        self.assertEqual(operation['payload']['prompt']['2']['inputs'], dict(width=1216, height=832, batch_size=2))
        self.assertEqual(operation['payload']['prompt']['4']['inputs']['text'], '')
        self.assertEqual(operation['payload']['prompt']['1']['inputs']['seed'], 10)
        self.assertEqual(self.snapshot()['revision'], 1)
        bound = self.core.bind_prompt(request | {'prompt_id': 'real-prompt'})
        self.assertEqual(bound['next_revision'], 2)
        self.assertEqual(self.snapshot()['content']['seed']['seed'], 11)
        self.assertEqual(bound['payload'], operation['payload'])
        self.assertEqual(bound['revision'], receipt['revision'])
        self.assertEqual(capture['seed']['seed'], 10)
        attached = self.core.attach_snapshot(self.value(prompt_id='real-prompt', revision=1, digest=receipt['digest'], new_client_id='new-web-sid'))
        self.assertEqual(attached['current']['content']['seed']['hasExecuted'], True)
        self.assertEqual(attached['payload'], operation['payload'])
        self.assertNotIn('client_id', attached['payload'])

    def test_capture_receipt_retry_is_idempotent_but_same_id_other_body_rejected(self):
        value, receipt = self.capture()
        self.assertEqual(self.core.capture(value), receipt)
        changed = copy.deepcopy(value); changed['edit_seq'] += 1
        with self.assertRaisesRegex(ValueError, 'operation_conflict'):
            self.core.capture(changed)
        self.assertEqual(self.snapshot()['revision'], 1)

    def test_late_edit_sequence_and_stale_revision_cannot_overwrite(self):
        value, receipt = self.capture()
        stale = copy.deepcopy(value); stale['op_id'] = 'another'
        with self.assertRaisesRegex(ValueError, 'revision_conflict'):
            self.core.capture(stale)
        stale['base_revision'] = receipt['revision']
        with self.assertRaisesRegex(ValueError, 'edit_sequence'):
            self.core.capture(stale)

    def test_writer_silence_and_unreceived_release_do_not_enable_start(self):
        _, receipt = self.capture()
        with self.assertRaisesRegex(ValueError, 'not_released'):
            self.core.start(self.value(revision=receipt['revision'], digest=receipt['digest']))
        with self.assertRaisesRegex(ValueError, 'writer_conflict'):
            self.core.lease(dict(key=self.key, session='different-tab', base_revision=1))

    def test_release_must_match_latest_ack_including_sequence_and_digest(self):
        capture, receipt = self.capture()
        for field, bad in [('revision', 0), ('digest', 'old'), ('edit_seq', 1)]:
            value = self.value(lease_id=capture['lease_id'], lease_epoch=capture['lease_epoch'],
                               revision=1, digest=receipt['digest'], edit_seq=0)
            value[field] = bad
            with self.assertRaisesRegex(ValueError, 'release_uncommitted'):
                self.core.release(value)
        self.assertEqual(self.snapshot()['phase'], 'editing')

    def test_released_old_lease_cannot_capture_or_release_new_writer(self):
        old, receipt, _ = self.released()
        lease = self.core.lease(dict(key=self.key, session='new-tab', base_revision=1))
        self.assertGreater(lease['lease_epoch'], old['lease_epoch'])
        value = self.value(lease_id=old['lease_id'], lease_epoch=old['lease_epoch'], revision=1,
                           digest=receipt['digest'], edit_seq=0)
        with self.assertRaisesRegex(ValueError, 'lease_conflict'):
            self.core.release(value)

    def test_restart_marks_released_state_unknown_and_requires_explicit_recovery(self):
        _, receipt, release = self.released()
        prior_epoch = self.core.server_epoch
        self.core = BackgroundState(self.service)
        self.assertNotEqual(self.core.server_epoch, prior_epoch)
        self.assertEqual(self.snapshot()['phase'], 'unknown')
        with self.assertRaisesRegex(ValueError, 'not_released'):
            self.core.start(self.value(revision=1, digest=receipt['digest']))
        with self.assertRaisesRegex(ValueError, 'server_epoch'):
            self.core.release(release)
        with self.assertRaisesRegex(ValueError, 'recovery_required'):
            self.capture(base=1)
        self.assertEqual(self.snapshot()['revision'], 1)

    def test_duplicate_start_never_grants_a_second_submit_and_random_is_reserved_once(self):
        calls = []
        self.core.draw = lambda: calls.append(1) or 0.5
        def randomize(value):
            value['seed']['policy'] = 'randomize'; value['visual']['nodes'][0]['widgets_values'][1] = 'randomize'
        _, _, request, first = self.start(randomize)
        second = self.core.start(request)
        self.assertFalse(second['submit_allowed']); self.assertEqual(len(calls), 1)
        self.assertEqual(first['seed_transition'], second['seed_transition'])
        with self.assertRaisesRegex(ValueError, 'submission_unresolved'):
            self.core.start(request | {'op_id': 'different'})

    def test_failed_submission_does_not_advance_and_retry_same_id_does_not_resubmit(self):
        _, _, request, _ = self.start()
        failed = self.core.submission_failed(request | {'uncertain': False})
        self.assertEqual(failed['state'], 'failed'); self.assertEqual(self.snapshot()['revision'], 1)
        self.assertFalse(self.core.start(request)['submit_allowed'])
        self.assertTrue(self.core.start(request | {'op_id': 'explicit-new-attempt'})['submit_allowed'])

    def test_uncertain_submission_blocks_retry_and_can_bind_only_real_later_ack(self):
        _, _, request, _ = self.start()
        self.core.submission_failed(request | {'uncertain': True})
        self.assertEqual(self.snapshot()['revision'], 1)
        self.assertEqual(self.snapshot()['phase'], 'uncertain')
        with self.assertRaises(ValueError):
            self.core.start(request | {'op_id': 'new'})
        bound = self.core.bind_prompt(request | {'prompt_id': 'verified-late-ack'})
        self.assertEqual(bound['next_revision'], 2)
        self.assertEqual(self.core.bind_prompt(request | {'prompt_id': 'verified-late-ack'}), bound)
        with self.assertRaisesRegex(ValueError, 'prompt_conflict'):
            self.core.bind_prompt(request | {'prompt_id': 'other'})

    def test_pending_submission_survives_restart_as_unresolved_without_resubmission(self):
        _, _, request, _ = self.start()
        self.core = BackgroundState(self.service)
        with self.assertRaisesRegex(ValueError, 'server_epoch'):
            self.core.start(request)
        with self.assertRaisesRegex(ValueError, 'submission_unresolved'):
            self.core.lease(dict(key=self.key, session='new', base_revision=1))

    def test_capture_transaction_rolls_back_if_receipt_write_fails(self):
        with patch.object(self.core, '_store_receipt', side_effect=sqlite3.OperationalError('fixture')):
            with self.assertRaises(sqlite3.OperationalError):
                self.capture()
        self.assertEqual(self.snapshot()['revision'], 0)
        self.assertIsNone(self.snapshot()['content'])

    def test_bind_transaction_rolls_back_state_and_receipt_together(self):
        _, _, request, _ = self.start()
        with patch.object(self.core, '_save_operation', side_effect=sqlite3.OperationalError('fixture')):
            with self.assertRaises(sqlite3.OperationalError):
                self.core.bind_prompt(request | {'prompt_id': 'real'})
        self.assertEqual(self.snapshot()['revision'], 1)
        self.assertEqual(self.core.start(request)['state'], 'prepared')
        self.assertEqual(self.core.bind_prompt(request | {'prompt_id': 'real'})['next_revision'], 2)

    def test_two_concurrent_starts_get_only_one_submit_permit(self):
        _, receipt, _ = self.released()
        request = self.value(revision=1, digest=receipt['digest'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.core.start, [request, request]))
        self.assertEqual(sum(r['submit_allowed'] for r in results), 1)

    def test_graph_or_capability_mismatch_rejected_before_persistence(self):
        mutations = [
            lambda v: v['output']['2']['inputs'].update(width=832),
            lambda v: v['visual'].update(id='copy'),
            lambda v: v['visual']['nodes'][1].update(mode=4),
            lambda v: v['visual']['nodes'][1].update(type='CustomLatent'),
            lambda v: v['visual'].update(definitions={'subgraphs': [{}]}),
            lambda v: v['visual']['links'][0].__setitem__(5, 'IMAGE'),
            lambda v: v['capability'].update(hooks_verified=False),
            lambda v: v['seed'].update(hasExecuted=None),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.capture(mutate)
        self.assertEqual(self.snapshot()['revision'], 0)

    def test_literal_empty_unicode_text_supported_dynamic_expression_rejected(self):
        def literal(value):
            value['visual']['nodes'][5]['widgets_values'][0] = '光與影'
            value['output']['6']['inputs']['text'] = '光與影'
        value, _ = self.capture(literal)
        self.assertEqual(self.snapshot()['content']['output']['4']['inputs']['text'], '')
        value['op_id'] = 'dynamic'; value['base_revision'] = 1; value['edit_seq'] = 1
        value['visual']['nodes'][5]['widgets_values'][0] = '{light|shadow}'
        value['output']['6']['inputs']['text'] = '{light|shadow}'
        with self.assertRaisesRegex(ValueError, 'dynamic_text_unsupported'):
            self.core.capture(value)

    def test_changed_links_are_accepted_when_visual_and_api_match_not_pinned_to_old_topology(self):
        def rewire(value):
            # Same output type: swap positive/negative CLIP source links.
            value['visual']['links'][1][1] = 4; value['visual']['links'][2][1] = 6
            value['output']['1']['inputs']['positive'] = ['4', 0]
            value['output']['1']['inputs']['negative'] = ['6', 0]
        _, _, _, op = self.start(rewire)
        self.assertEqual(op['payload']['prompt']['1']['inputs']['positive'], ['4', 0])

    def test_attach_wrong_prompt_revision_or_newer_edit_cannot_show_old_job_as_current(self):
        _, receipt, request, _ = self.start()
        self.core.bind_prompt(request | {'prompt_id': 'real'})
        value = self.value(prompt_id='real', revision=1, digest=receipt['digest'], new_client_id='new-sid')
        for changed in ({'prompt_id': 'wrong'}, {'digest': 'wrong'}, {'revision': 2}):
            with self.assertRaises(ValueError):
                self.core.attach_snapshot(value | changed)
        self.capture(base=2)
        with self.assertRaisesRegex(ValueError, 'attach_current_changed'):
            self.core.attach_snapshot(value)

    def test_repeated_reopen_and_layout_capture_keep_the_original_job_and_frozen_payload(self):
        _, receipt, request, _ = self.start()
        original = self.core.bind_prompt(request | {'prompt_id': 'real'})
        attach = self.value(prompt_id='real', revision=1, digest=receipt['digest'], new_client_id='new-tab')
        for index in range(3):
            previous = self.snapshot()
            def layout(content):
                content['visual']['nodes'][0]['pos'] = [index * 100, 200]
                content['visual']['nodes'].reverse()
                content['visual']['extra'] = {'ds': {'scale': 1 + index, 'offset': [0, 20]}}
            capture, accepted = self.recapture(layout if index else None)
            # Lost capture replies may be retried without duplicating evidence.
            self.assertEqual(self.core.capture(capture), accepted)
            self.core.release(self.value(lease_id=capture['lease_id'], lease_epoch=capture['lease_epoch'],
                revision=accepted['revision'], digest=accepted['digest'], edit_seq=accepted['edit_seq']))
            attached = self.core.attach_snapshot(attach)
            self.assertEqual(attached['current']['revision'], index + 3)
            self.assertEqual(attached['payload'], original['payload'])
            latest = self.core.start(request)
            self.assertFalse(latest['submit_allowed'])
            for field in ('prompt_id', 'revision', 'digest', 'payload', 'after', 'seed_transition', 'source_revision'):
                self.assertEqual(latest[field], original[field])
            self.assertEqual((latest['next_revision'], latest['next_digest']), (accepted['revision'], accepted['digest']))
            evidence = latest['equivalent_revisions']
            self.assertEqual(len(evidence), index + 1)
            self.assertEqual(evidence[-1]['capture_op_id'], capture['op_id'])
            self.assertEqual((evidence[-1]['from_revision'], evidence[-1]['from_digest']),
                             (previous['revision'], previous['digest']))
            self.assertEqual((evidence[-1]['to_revision'], evidence[-1]['to_digest']),
                             (accepted['revision'], accepted['digest']))
            self.assertEqual(evidence[-1]['execution_digest'], evidence[0]['execution_digest'])
        for changed in ({'revision': 4}, {'digest': 'wrong'}, {'prompt_id': 'other'}):
            with self.assertRaises(ValueError):
                self.core.attach_snapshot(attach | changed)

    def test_execution_or_seed_lifecycle_changes_break_equivalent_attachment(self):
        def widget(content, node, index, field, value):
            content['visual']['nodes'][node - 1]['widgets_values'][index] = value
            content['output'][str(node)]['inputs'][field] = value
        def seed_value(content):
            content['seed']['seed'] += 1
            widget(content, 1, 0, 'seed', content['seed']['seed'])
        def policy(content):
            content['seed']['policy'] = 'fixed'
            content['visual']['nodes'][0]['widgets_values'][1] = 'fixed'
        def rewire(content):
            content['visual']['links'][1][1] = 4; content['visual']['links'][2][1] = 6
            content['output']['1']['inputs'].update(positive=['4', 0], negative=['6', 0])
        mutations = {
            'text': lambda c: widget(c, 6, 0, 'text', 'new manual text'),
            'dimensions': lambda c: widget(c, 2, 0, 'width', 832),
            'batch': lambda c: widget(c, 2, 2, 'batch_size', 3),
            'seed': seed_value, 'policy': policy, 'topology': rewire,
            'timing': lambda c: c['seed'].update(timing='before'),
            'lifecycle': lambda c: c['seed'].update(hasExecuted=False),
            'minimum': lambda c: c['seed'].update(min=1),
            'maximum': lambda c: c['seed'].update(max=101),
            'step': lambda c: c['seed'].update(step2=2),
            'source': lambda c: c.update(source_revision='b' * 64),
            'output-metadata': lambda c: c['output']['1'].update(_meta={'title': 'changed'}),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.key['workspace_id'] = name
                _, receipt, request, _ = self.start()
                original = self.core.bind_prompt(request | {'prompt_id': 'real-' + name})
                self.recapture(mutate)
                with self.assertRaisesRegex(ValueError, 'attach_current_changed'):
                    self.core.attach_snapshot(self.value(prompt_id=original['prompt_id'], revision=1,
                        digest=receipt['digest'], new_client_id='new-tab'))
                operation = self.core.start(request)
                self.assertEqual(operation['next_revision'], 2)
                self.assertNotIn('equivalent_revisions', operation)

    def test_invalid_identity_or_capability_cannot_advance_job_association(self):
        _, _, request, _ = self.start()
        original = self.core.bind_prompt(request | {'prompt_id': 'real'})
        for mutate in (lambda c: c['visual'].update(id='other'),
                       lambda c: c['capability'].update(hooks_verified=False)):
            with self.assertRaises(ValueError):
                self.recapture(mutate)
            self.assertEqual(self.snapshot()['revision'], 2)
            self.assertEqual(self.core.start(request)['next_digest'], original['next_digest'])

    def test_restoring_old_content_does_not_resurrect_detached_job(self):
        _, receipt, request, _ = self.start()
        original = self.core.bind_prompt(request | {'prompt_id': 'real'})
        def width(content):
            content['visual']['nodes'][1]['widgets_values'][0] = 832
            content['output']['2']['inputs']['width'] = 832
        self.recapture(width)
        self.recapture(lambda c: c.update(copy.deepcopy(original['after'])))
        self.recapture()  # Later no-op captures cannot bridge the broken chain.
        with self.assertRaisesRegex(ValueError, 'attach_current_changed'):
            self.core.attach_snapshot(self.value(prompt_id='real', revision=1, digest=receipt['digest'], new_client_id='new-tab'))
        self.assertEqual(self.core.start(request)['next_revision'], 2)

    def test_newer_job_even_with_identical_content_does_not_extend_old_job(self):
        def fixed(value):
            value['seed'].update(policy='fixed', hasExecuted=True)
            value['visual']['nodes'][0]['widgets_values'][1] = 'fixed'
        _, _, first, _ = self.start(fixed)
        old = self.core.bind_prompt(first | {'prompt_id': 'first'})
        second = self.value(revision=2, digest=old['next_digest'])
        self.core.start(second)
        current = self.core.bind_prompt(second | {'prompt_id': 'second'})
        self.assertEqual(old['next_digest'], current['next_digest'])
        self.recapture()
        self.assertEqual(self.core.start(first)['next_revision'], 2)
        self.assertEqual(self.core.start(second)['next_revision'], 4)

    def test_equivalent_capture_rolls_back_job_state_and_evidence_with_receipt(self):
        _, _, request, _ = self.start()
        original = self.core.bind_prompt(request | {'prompt_id': 'real'})
        with patch.object(self.core, '_store_receipt', side_effect=sqlite3.OperationalError('fixture')):
            with self.assertRaises(sqlite3.OperationalError):
                self.recapture(lambda c: c['visual'].update(extra={'layout': 'new'}))
        self.assertEqual(self.snapshot()['revision'], 2)
        operation = self.core.start(request)
        self.assertEqual(operation['next_digest'], original['next_digest'])
        self.assertNotIn('equivalent_revisions', operation)
        _, receipt = self.recapture()
        self.assertEqual(receipt['revision'], 3)
        self.assertEqual(len(self.core.start(request)['equivalent_revisions']), 1)

    def test_equivalent_capture_never_adopts_foreign_or_mismatched_job(self):
        mutations = {
            'digest': lambda op: op.update(next_digest='b' * 64),
            'epoch': lambda op: op.update(server_epoch='old-server'),
            'key-hash': lambda op: op.update(key_hash='other-key'),
            'key': lambda op: op['key'].update(workspace_id='other-workspace'),
            'failed': lambda op: op.update(state='failed'),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                self.key['workspace_id'] = name
                _, _, request, _ = self.start()
                original = self.core.bind_prompt(request | {'prompt_id': 'real-' + name})
                mutate(original)
                with self.service.connect() as db:
                    db.execute('UPDATE background_operations SET body=? WHERE id=?',
                               (module._json(original), original['op_id']))
                self.recapture()
                with self.service.connect() as db:
                    stored, = db.execute('SELECT body FROM background_operations WHERE id=?',
                                         (original['op_id'],)).fetchone()
                self.assertEqual(stored, module._json(original))

    def test_bool_revision_is_not_a_revision_and_multiuser_is_not_supported(self):
        _, receipt, _ = self.released()
        with self.assertRaisesRegex(ValueError, 'invalid_integer'):
            self.core.start(self.value(revision=True, digest=receipt['digest']))
        with self.assertRaisesRegex(ValueError, 'multi_user_unsupported'):
            BackgroundState(self.service, multi_user=True)

    def test_server_source_revision_is_preserved_and_changed_desktop_text_refuses_start(self):
        _, receipt, _ = self.released()
        self.assertEqual(receipt['source_revision'], 'a' * 64)
        self.assertEqual(self.snapshot()['content']['source_revision'], 'a' * 64)
        value = self.value(revision=1, digest=receipt['digest'])
        for invalid in ('', 'not-a-hash', 'B' * 64):
            with self.assertRaisesRegex(ValueError, 'source_revision'):
                self.core.start(value | {'source_revision': invalid})
        with self.assertRaisesRegex(ValueError, 'source_changed'):
            self.core.start(value | {'source_revision': 'b' * 64})
        operation = self.core.start(value)
        self.assertEqual(operation['payload']['extra_data']['extra_pnginfo']['pcs_background']['source_revision'], 'a' * 64)

    def test_invalid_source_revision_capture_never_acknowledged(self):
        with self.assertRaisesRegex(ValueError, 'source_revision'):
            self.capture(lambda value: value.update(source_revision='browser-assertion'))
        self.assertEqual(self.snapshot()['revision'], 0)


class SeedTests(unittest.TestCase):
    def spec(self, **changes):
        seed = graphs()[2]; seed.update(changes); return seed

    def test_before_first_skip_then_increment_and_after_has_no_before_change(self):
        self.assertEqual(prepare_seed(self.spec(timing='before'))['execution_seed'], 10)
        self.assertEqual(prepare_seed(self.spec(timing='before', hasExecuted=True))['execution_seed'], 11)
        result = prepare_seed(self.spec(timing='after'))
        self.assertEqual((result['execution_seed'], result['after_seed']), (10, 11))

    def test_random_range_cap_step_and_clamp_follow_native_numeric_rule(self):
        seed = self.spec(policy='randomize', max=2**64-1)
        result = prepare_seed(seed, lambda: 0.5)
        self.assertEqual(result['after_seed'], 2**49)
        self.assertEqual(prepare_seed(self.spec(seed=100))['after_seed'], 100)
        self.assertEqual(prepare_seed(self.spec(seed=0, policy='decrement'))['after_seed'], 0)
        self.assertEqual(prepare_seed(self.spec(policy='randomize', min=2, max=10, step2=2), lambda: 0.5)['after_seed'], 6)

    def test_invalid_lifecycle_precision_and_draw_rejected_without_mutating_seed(self):
        seed = self.spec(policy='randomize'); before = copy.deepcopy(seed)
        for sample in (-0.1, 1, float('nan')):
            with self.assertRaises(ValueError):
                prepare_seed(seed, lambda: sample)
        self.assertEqual(seed, before)
        for changes in ({'hasExecuted': None}, {'seed': 2**53}, {'step2': 0}, {'min': -1}):
            with self.assertRaises(ValueError):
                prepare_seed(self.spec(**changes))


if __name__ == '__main__':
    unittest.main()
