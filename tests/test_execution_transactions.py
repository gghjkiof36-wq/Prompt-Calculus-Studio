"""Execution write registration, actual failure boundaries and unchanged Undo."""
import ast
import copy
from contextlib import ExitStack, contextmanager
import inspect
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from prompt_calculus_studio import stage_context as context, prompt_sources as supply
from prompt_calculus_studio import stage_batches as batches, schedule_batch_actions as actions
from prompt_calculus_studio import stage_model
from prompt_calculus_studio.stage_runner import StageRunner, CAPACITY
from prompt_calculus_studio.stage_store import StageStore
from test_stage_enqueue_progress import scenario
import test_084_stages as stages


class Injected(RuntimeError): pass


def database(store):
    return {name: list(store.db.execute('SELECT * FROM "'+name+'" ORDER BY rowid'))
            for name, in store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def result_canvas(state, stage):
    from prompt_calculus_studio.multi_output import new_canvas,connect
    state['multi_output']['canvases']['result-test']=new_canvas('Result')
    connect(state,stage,'result-test','clip')


class TransactionContractTests(unittest.TestCase):
    def test_nested_first_write_missing_overlap_and_identity(self):
        state = {'root': {'items': [{'value': 1}], 'other': 2}}
        root, items, item = state['root'], state['root']['items'], state['root']['items'][0]
        before = copy.deepcopy(state)
        db = sqlite3.connect(':memory:');self.addCleanup(db.close)
        runner = NS(store=StageStore(db), window=NS(state=state))
        with self.assertRaises(Injected):
            with StageRunner.accepting(runner) as outer:
                outer.register('root', 'items')
                item['value'] = 2
                with StageRunner.accepting(runner) as inner:
                    self.assertIs(inner, outer)
                    inner.register('root', 'items')
                    item['value'] = 3
                    inner.register('root')
                    root['other'] = 4
                    inner.register('new', 'child')
                    state['new'] = {'child': [1]}
                runner.store.add('entry', 'w', status='waiting')
                raise Injected()
        self.assertEqual(state, before)
        self.assertIs(state['root'], root)
        self.assertIs(root['items'], items)
        self.assertIs(items[0], item)
        self.assertEqual(runner.store.rows('entry'), [])
        self.assertEqual(outer.changes, frozenset())

    def test_caught_inner_exception_and_callback_error_keep_committed_writes(self):
        state = {'items': []}
        db = sqlite3.connect(':memory:');self.addCleanup(db.close)
        store = StageStore(db);runner = NS(store=store, window=NS(state=state))
        with self.assertRaisesRegex(Injected, 'callback'):
            with StageRunner.accepting(runner) as transaction:
                try:
                    with StageRunner.accepting(runner) as inner:
                        inner.register('items');state['items'].append(1)
                        store.add('entry', 'w', status='waiting')
                        raise Injected('caught')
                except Injected:pass
                def failed_callback():raise Injected('callback')
                store._transactions.after_commit(failed_callback)
        self.assertEqual(state, {'items': [1]})
        self.assertEqual(len(store.rows('entry')), 1)
        self.assertTrue(transaction.committed)
        self.assertEqual(transaction.changes, frozenset({('items',)}))
        self.assertIsNone(store._transactions.execution)

    def test_removed_speculative_backup_api_ast(self):
        root = Path(__file__).resolve().parents[1]/'prompt_calculus_studio'
        for name in ('stage_runner.py', 'stage_context.py', 'stage_batches.py', 'schedule_batch_actions.py'):
            tree = ast.parse((root/name).read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Name, ast.Attribute)):
                    self.assertNotEqual(getattr(node, 'id', getattr(node, 'attr', '')), 'progress_backup')
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr=='accepting':
                    self.assertFalse({'suppliers', 'document'} & {k.arg for k in node.keywords})
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name=='accepting':
                    self.assertEqual([a.arg for a in node.args.args], ['self'])
                    self.assertEqual(node.args.kwonlyargs, [])
                    self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                         and n.func.attr=='deepcopy' for n in ast.walk(node)))

    def test_registration_changes_and_undo_contents(self):
        with scenario(False) as (case, config):
            history = copy.deepcopy(case.c.undo_stack)
            with case.runner.accepting() as enqueue:case.runner.start(2)
            self.assertIn(supply.source_path(config['reader']), enqueue.changes)
            self.assertEqual(case.c.undo_stack, history)
            with case.runner.accepting() as expansion:batches.expand(case.runner, CAPACITY)
            self.assertEqual(expansion.changes, frozenset())
            self.assertEqual(case.c.undo_stack, history)
            # A real Stage text result changes the connected Canvas projection.
            state=case.w.state
            self.assertTrue(case.c.commit(lambda s:result_canvas(s,config['stage'])))
            history=copy.deepcopy(case.c.undo_stack)
            case.runner.results[config['stage']]={'text':'accepted result','texts':[],'images':[]}
            with case.runner.accepting() as result:case.runner.refresh_results()
            self.assertIn(('uses',), result.changes)
            self.assertIn(('multi_output','canvases'), result.changes)
            self.assertEqual(case.c.undo_stack, history)
            control=case.runner.store.rows('batch_control')[0]
            actions.cancel(case.runner,config['scheduler'],control['group'])
            self.assertEqual(case.c.undo_stack, history)

    def test_manual_text_has_no_document_registration(self):
        case=stages.StageTests()
        try:
            case.setUp()
            from prompt_calculus_studio.flow_data import add_scheduler
            from prompt_calculus_studio.multi_output import connect
            key=case.stages()[0]
            def scheduled(state):connect(state,key,add_scheduler(state)+'::flow','flow')
            self.assertTrue(case.c.commit(scheduled))
            with case.runner.accepting() as transaction:case.runner.start(10)
            self.assertEqual(transaction.changes,frozenset())
        finally:case.tearDown();case.doCleanups()

    def test_nested_cancel_defers_every_external_action_until_outer_commit(self):
        with scenario(False) as (case, config):
            case.runner.start(2)
            control=case.runner.store.rows('batch_control')[0]
            before=copy.deepcopy(case.w.state);rows=database(case.w.store)
            with patch.object(case.runner.timer,'start') as timer,patch.object(case.w,'notice') as notice, \
                 patch.object(case.w,'changed') as changed:
                with self.assertRaises(Injected):
                    with case.runner.accepting():
                        actions.cancel(case.runner,control['owner'],control['group'])
                        timer.assert_not_called();notice.assert_not_called();changed.assert_not_called()
                        raise Injected()
                timer.assert_not_called();notice.assert_not_called();changed.assert_not_called()
            self.assertEqual(case.w.state,before)
            self.assertEqual(database(case.w.store),rows)

    def test_route_checks_are_pure_and_abandon_is_once(self):
        with scenario(False) as (case, config):
            case.runner.start(1)
            callbacks=[]
            def prepare(client,state,workflow,valid,submit,failed,**kwargs):callbacks.append(valid)
            with patch('prompt_calculus_studio.native_submission.prepare',side_effect=prepare):
                case.runner.pump()
            self.assertTrue(callbacks)
            valid=callbacks[-1]
            pure=inspect.getclosurevars(valid).nonlocals['still_current']
            before=database(case.w.store)
            for _ in range(20):self.assertTrue(pure())
            self.assertEqual(database(case.w.store),before)
            with patch.object(case.runner,'check_route',side_effect=ValueError('route changed')), \
                 patch.object(case.runner,'pause',wraps=case.runner.pause) as pause:
                for _ in range(20):self.assertFalse(pure())
                self.assertEqual(database(case.w.store),before)
                for _ in range(20):self.assertFalse(valid())
                self.assertEqual(pause.call_count,1)
            attempts=case.runner.store.rows('attempt')
            self.assertEqual([a['status'] for a in attempts],['failed'])

    def test_input_route_checks_are_pure_and_abandon_is_once(self):
        with scenario(False) as (case, config):
            callbacks=[];current=[True];failed=Mock()
            def prepare(client,state,workflow,valid,submit,error,**kwargs):callbacks.append(valid)
            with patch('prompt_calculus_studio.native_submission.prepare',side_effect=prepare):
                case.runner.apply_bindings({'flow':[]},Mock(),failed,valid=lambda:current[0])
            self.assertEqual(len(callbacks),1)
            continuation=callbacks[0]
            pure=inspect.getclosurevars(continuation).nonlocals['still_current']
            before=database(case.w.store)
            for _ in range(20):self.assertTrue(pure())
            self.assertEqual(database(case.w.store),before)
            current[0]=False
            for _ in range(20):self.assertFalse(pure())
            self.assertEqual(database(case.w.store),before)
            with patch.object(case.runner,'cancel_apply',wraps=case.runner.cancel_apply) as cancel:
                for _ in range(20):self.assertFalse(continuation())
                self.assertEqual(cancel.call_count,1)
            failed.assert_called_once()
            self.assertEqual([r['status'] for r in case.runner.store.rows('input')],['failed'])

    def test_successful_background_image_supply_preserves_undo_contents(self):
        with image_case() as (case, config):
            run,frame=legacy_feed(case)
            before=copy.deepcopy(case.c.undo_stack)
            case.runner.fill_supply(run,frame)
            self.assertGreater(frame['feed']['next'],0)
            self.assertEqual(case.c.undo_stack,before)

    def test_workflow_import_matches_previous_replacement_refresh_and_undo(self):
        from prompt_calculus_studio.comfy_client import ComfyClient
        from prompt_calculus_studio.window import Window
        from prompt_calculus_studio.workflow_transfer import apply_transfer
        from test_multi_output import workspace,workflow
        state,_,_=workspace()
        value=dict(format='prompt_studio_workflow',version=1,id='flow',name='imported',
                   graph=workflow()['graph'],bindings=copy.deepcopy(state['multi_output']['bindings']))
        # Compare the former inline sequence with the new owner entry, using
        # the same frozen result and fake transport; count ordered refreshes.
        candidate,_=apply_transfer(state,value)
        traces=[]
        undo=[('before','after')]
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE workflow_receipts(id TEXT PRIMARY KEY,error TEXT)')
        window=NS(state=copy.deepcopy(state),store=NS(db=db,directory=Path('.'),backup=Mock(),save=Mock()),
                  canvas=NS(last_state='old',refresh=lambda:traces.append('canvas'),undo_stack=undo),
                  generation_panel=NS(changed=lambda:traces.append('generation')),notice=Mock())
        window.replace_execution_document=lambda s:Window.replace_execution_document(window,s)
        window.adopt_document=lambda s,**kw:Window.adopt_document(window,s,**kw)
        window.document_changed=Mock()
        owner=object();client=NS(window=window,fetching_workflow=False,workflow_import_owner=owner)
        acknowledgements=[]
        def request(route,data=None,done=None,**kwargs):
            if route=='desktop/workflows':done(dict(id='transfer',value=value))
            else:acknowledgements.append(data)
        client.request=request
        with patch('prompt_calculus_studio.workflow_transfer.apply_transfer',return_value=(candidate,{'name':'imported'})):
            ComfyClient.receive_workflow(client)
        self.assertEqual(window.state,candidate)
        self.assertIsNot(window.state,candidate)
        self.assertEqual(traces,['generation','canvas'])
        self.assertIsNone(window.canvas.last_state)
        self.assertEqual(undo,[('before','after')])
        window.store.save.assert_called_once_with(candidate)
        self.assertEqual(acknowledgements[0]['error'],'')

    def test_legacy_input_selection_failure_restores_document_and_database(self):
        import test_input_runner as legacy
        from prompt_calculus_studio.core import Storage
        from prompt_calculus_studio.input_runner import InputRunner
        case=legacy.InputRunnerTests()
        try:
            case.setUp();case.images(2)
            store=Storage(case.directory)
            try:
                store.save(case.state)
                case.window.store=store
                case.window.canvas=NS(refresh=Mock())
                case.window.changed=Mock()
                source=case.state['canvas_functions']['images']['__source_images']
                runner=case.runner
                runner.current=dict(live=True,token='token',operation='operation',
                    route={'workspace':case.state['workspace']},
                    source={'key':'__source_images','batch':source['batch_id'],'index':1})
                before=copy.deepcopy(case.state);rows=database(store)
                original=context.select_live_image
                def failed(*args,**kwargs):
                    original(*args,**kwargs)
                    raise Injected('legacy input')
                with patch.object(context,'select_live_image',side_effect=failed),self.assertRaises(Injected):
                    InputRunner.submit(runner,{'state':{}},'flow',[],'token')
                self.assertEqual(case.state,before)
                self.assertEqual(database(store),rows)
                case.window.canvas.refresh.assert_not_called()
                case.window.changed.assert_not_called()
            finally:store.close()
        finally:case.doCleanups()


@contextmanager
def image_case():
    case=stages.StageTests()
    try:
        case.setUp();key,items,config=case.image_batch(count=3)
        yield case,dict(reader=key,items=items,config=config)
    finally:case.tearDown();case.doCleanups()


def legacy_feed(case):
    plan=stage_model.compile_plan(case.w.state)
    with case.runner.accepting():run=case.runner._create_legacy_run(plan,1,execution_group='failure-feed')
    node=next(n for n in plan['tree'] if n.get('auto'))
    frame=dict(kind='loop',node=node,ctx=run['stack'][0]['ctx'],owner=run['id']+':feed',produced={},
               active=None,initialized=False,auto=node['auto'],feed=None,done=0)
    run['stack'].append(frame)
    with case.runner.accepting():case.runner.initialize_loop(run,frame)
    return run,frame


class WriteFailureTests(unittest.TestCase):
    """Each generated method injects after a real write, before outer commit."""
    def check_point(self, point):
        image=point in ('reserve_image','refresh_image','feed_v2','dispatch_supply','advance_image',
                        'restore_image','release_image','cancel_run','legacy_release')
        with (image_case() if image else scenario(False)) as (case,config):
            runner=case.runner;store=runner.store;state=case.w.state
            target,name,predicate=None,None,lambda a,k:True
            # Setup precedes the snapshot and the injected failure.
            if point in ('reserve_text','reserve_image','refresh_image'):
                frozen=supply.build(state,[config['reader']],count=1)
                def operation():
                    with runner.accepting():runner.reserve_supply(frozen)
                target,name=(runner,'refresh_reserved_images') if point=='refresh_image' else (supply,'reserve')
            elif point in ('feed_v2','dispatch_supply','legacy_release'):
                run,frame=legacy_feed(case)
                if point=='feed_v2':
                    operation=lambda:runner.fill_supply(run,frame)
                    target,name=supply,'reserve'
                elif point=='legacy_release':
                    runner.fill_supply(run,frame)
                    runner.save(run)
                    operation=lambda:runner.cancel(run['id'])
                    target,name=case.w.store,'save'
                else:
                    key=frame['node']['stage'];stage=run['plan']['stages'][key]
                    # Use the real direct supply variant/dispatch reservation.
                    projected=runner.context()
                    saved=runner.capture_scope(run['plan'],frame['node'],projected,per_round=True)
                    source_plan=frame['feed']['plan']
                    stage_frame=dict(kind='stage',key=key,ctx=dict(results={},sources={},saved=saved),index=0,produced={},job=None,
                        variants=[dict(values={},sources={},results={},supply_round=0)],supply_plan=source_plan)
                    run['stack'].append(stage_frame)
                    operation=lambda:runner.dispatch(run,stage_frame,stage)
                    target,name=supply,'reserve'
            elif point=='advance_image':
                image_value=state['canvas_functions']['images'][config['reader']]
                operation=lambda:runner.advance_sources({'sources':{config['reader']:{'batch':image_value['batch_id'],'index':0}}})
                target,name=context,'select_live_image'
            elif point=='advance_text':
                root=supply.source(state,config['reader'])['root']
                root['supply_cursor']={'plan':'legacy-plan'}
                position=dict(kind='text',plan='legacy-plan',signature=supply.signature(root),group=root['sequence']['groups'][1]['id'])
                operation=lambda:runner.advance_sources({'sources':{config['reader']:position}})
                target,name=runner,'refresh_results'
            elif point in ('result_projection','save_output','save_scene'):
                if point=='result_projection':
                    self.assertTrue(case.c.commit(lambda s:result_canvas(s,config['stage'])))
                    state=case.w.state
                    runner.results[config['stage']]={'text':'result','texts':[],'images':[]}
                    operation=runner.refresh_results;target,name=context,'sync_result_view'
                else:
                    if point=='save_output':state['draft']='pending output capture'
                    else:state['workspace_scenes']['items'][state['workspace']]={'stale':True}
                    def operation():
                        with runner.accepting():case.w.store.save(state)
                    if point=='save_output':
                        from prompt_calculus_studio import multi_output
                        target,name=multi_output,'capture_current'
                    else:
                        from prompt_calculus_studio import workspace_scene
                        target,name=workspace_scene,'capture'
            elif point=='accept_batch':
                operation=lambda:runner.start(2);target,name=batches,'commit_batches'
            else:
                runner.start(2)
                run=store.rows('run')[0];control=store.rows('batch_control')[0]
                if point=='expand_batch':
                    operation=lambda:batches.expand(runner,CAPACITY)
                    target,name=store,'patch'
                    predicate=lambda a,k:a[0]==store.batch_heads(state['workspace'])[0]['id']
                elif point=='activate_entry':
                    batches.expand(runner,CAPACITY)
                    node=run['plan']['tree'][0]
                    frame=dict(kind='loop',node=node,owner=control['entry_owner'],ctx=run['stack'][0]['ctx'],active=None)
                    run['stack'].append(frame)
                    item=store.rows('entry')[0]
                    operation=lambda:runner.activate_entry(run,frame,item)
                    target,name=runner,'save'
                elif point in ('withdraw_text','restore_image','release_image'):
                    if point=='release_image':store.patch(control['id'],started=True)
                    operation=lambda:actions.cancel(runner,control['owner'],control['group'])
                    target,name=batches,('release_images' if point=='release_image' else 'restore_cursors')
                elif point=='stop_feed':
                    operation=lambda:runner.stop_feed(run['id'],config['reader'])
                    target,name=runner,'pause'
                elif point=='resolve_deferred':
                    head=store.batch_heads(state['workspace'])[0]
                    batch=store.read(head['id'])
                    for segment in batch.get('segments',[]):segment['deferred']=True
                    store.patch(head['id'],deferred=True,segments=batch['segments'])
                    frame=dict(owner=head['owner'],ctx=dict(results={},sources={}))
                    operation=lambda:batches.resolve_deferred(runner,run,frame)
                    target,name=store,'set_batch_control'
                    predicate=lambda a,k:'cursor_after' in k
                elif point=='cancel_run':
                    operation=lambda:runner.cancel(run['id']);target,name=batches,'release_images'
                elif point=='promote_waiting':
                    store.patch(run['id'],status='waiting');run=store.read(run['id'])
                    operation=lambda:actions.promote_waiting(runner,[run]);target,name=store,'patch'
                else:raise AssertionError(point)
            case.w.save_timer.stop();runner.timer.stop()
            before=copy.deepcopy(state);rows=database(case.w.store);undo=copy.deepcopy(case.c.undo_stack)
            original=getattr(target,name);hits=[]
            def injected(*a,**k):
                result=original(*a,**k)
                if predicate(a,k):
                    self.assertIsNotNone(getattr(store._transactions,'execution',None))
                    if point in ('advance_text','save_output','save_scene'):
                        self.assertNotEqual(case.w.state,before,'fixture must exercise a real live change')
                    hits.append(point);raise Injected(point)
                return result
            with ExitStack() as stack:
                external=[stack.enter_context(patch.object(obj,method)) for obj,method in
                          ((runner.timer,'start'),(case.w,'notice'),(case.w,'changed'),(case.c,'refresh'))]
                external.append(stack.enter_context(patch('prompt_calculus_studio.stage_queue_actions.request_cancel')))
                stack.enter_context(patch.object(target,name,side_effect=injected))
                with self.assertRaisesRegex(Injected,point):operation()
                for callback in external:callback.assert_not_called()
            self.assertEqual(hits,[point])
            self.assertEqual(case.w.state,before)
            self.assertEqual(database(case.w.store),rows)
            self.assertEqual(case.c.undo_stack,undo)


for _point in ('reserve_text','reserve_image','refresh_image','feed_v2','dispatch_supply','advance_image','advance_text',
               'result_projection','save_output','save_scene','accept_batch','expand_batch','activate_entry',
               'withdraw_text','restore_image','release_image','stop_feed','cancel_run','legacy_release','promote_waiting','resolve_deferred'):
    def _test(self, point=_point):self.check_point(point)
    setattr(WriteFailureTests,'test_failure_after_'+_point,_test)


if __name__=='__main__':unittest.main()
