"""Prompt Calculus Studio: frontend extension only; no extra graph nodes or Qt."""
import asyncio
import functools
import ipaddress
import json
import logging
import secrets
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web
import folder_paths
from server import PromptServer
from comfy.cli_args import args as comfy_args
from .service import Service
from .bridge import DesktopBridge
from .shared.snapshots import make_snapshot

WEB_DIRECTORY = './web'
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
_bootstrap = Path(__file__).with_name('local_library.json')
_default_library = json.loads(_bootstrap.read_text(encoding='utf-8')).get('library', '') if _bootstrap.exists() else ''
service = Service(Path(folder_paths.get_user_directory()) / 'prompt_studio',
                  folder_paths.get_output_directory(), folder_paths.get_temp_directory(), _default_library,
                  native_multi_user=lambda:getattr(comfy_args,'multi_user',None), input_root=folder_paths.get_input_directory())
_token = secrets.token_urlsafe(32)
routes = PromptServer.instance.routes
bridge = DesktopBridge()
from .node_images import NodeImages
node_images=NodeImages(dict(input=folder_paths.get_input_directory(),output=folder_paths.get_output_directory(),temp=folder_paths.get_temp_directory()))
from .background_events import BackgroundEvents
from .background_execution import BackgroundExecution,CaptureRejected
background_events=BackgroundEvents(PromptServer.instance.send_sync)
PromptServer.instance.send_sync=background_events.observe
background_execution=BackgroundExecution(service,background_events)


def local_route(function):
    @functools.wraps(function)
    async def wrapped(request):
        try:
            peer = ipaddress.ip_address(request.remote or '')
            if not (peer.is_loopback or (getattr(peer, 'ipv4_mapped', None) and peer.ipv4_mapped.is_loopback)):
                raise web.HTTPForbidden(text='Prompt Calculus Studio 第一版只接受本機連線。')
            if urlsplit('//'+request.host).hostname not in ('localhost', '127.0.0.1', '::1'):
                raise web.HTTPForbidden(text='請使用 localhost 或 127.0.0.1 開啟 ComfyUI。')
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc != request.host:
                raise web.HTTPForbidden(text='不接受跨來源請求。')
            if request.headers.get('Sec-Fetch-Site') == 'cross-site':
                raise web.HTTPForbidden()
            if request.method != 'GET' and not secrets.compare_digest(request.headers.get('X-Prompt-Studio', ''), _token):
                raise web.HTTPForbidden(text='請重新整理頁面後再試。')
            return await function(request)
        except web.HTTPException:
            raise
        except CaptureRejected as exc:
            return web.json_response({'error':str(exc),'capture_uncommitted':exc.operation_id},status=400)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return web.json_response({'error': str(exc)}, status=400)
        except Exception:
            logging.exception('Prompt Calculus Studio request failed')
            return web.json_response({'error': '操作失敗，請查看 ComfyUI 終端紀錄。'}, status=500)
    return wrapped


async def body(request):
    raw = bytearray()
    async for block in request.content.iter_chunked(65536):
        raw.extend(block)
        if len(raw) > 3 * 1024 * 1024:
            raise ValueError('請求過大。')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('資料格式無效。')
    return value


@routes.get('/prompt_studio/config')
@local_route
async def config(request):
    return web.json_response(dict(settings=await asyncio.to_thread(service.settings), token=_token))


@routes.post('/prompt_studio/config')
@local_route
async def configure(request):
    data = await body(request)
    return web.json_response(await asyncio.to_thread(service.configure,
        data.get('library'), data.get('workspace'), data.get('destination')))


@routes.get('/prompt_studio/library')
@local_route
async def library(request):
    return web.json_response(await asyncio.to_thread(service.read_library))


@routes.post('/prompt_studio/compose')
@local_route
async def compose(request):
    data = await body(request)
    return web.json_response(await asyncio.to_thread(make_snapshot, data['state'],
        data.get('library_id', ''), data.get('library_revision', '')))


@routes.get('/prompt_studio/folders')
@local_route
async def folders(request):
    return web.json_response(await asyncio.to_thread(service.folders, request.query.get('path', '')))


@routes.get('/prompt_studio/results')
@local_route
async def results(request):
    history = PromptServer.instance.prompt_queue.get_history(max_items=50)
    return web.json_response(service.results(history))


@routes.post('/prompt_studio/collect')
@local_route
async def collect(request):
    data = await body(request)
    # A displayed result must still belong to the identified submission.
    prompt_id = str(data.get('prompt_id', ''))
    history = PromptServer.instance.prompt_queue.get_history(prompt_id=prompt_id)
    valid = any(r['image'] == data.get('image') for r in service.results(history))
    if not valid:
        raise ValueError('此結果已不在生成歷史，請重新整理結果清單。')
    return web.json_response(await asyncio.to_thread(service.collect,
        data['image'], data.get('workspace', ''), prompt_id))


PromptServer.instance.add_on_prompt_handler(service.prepare_prompt)


@routes.get('/prompt_studio/desktop/status')
@local_route
async def desktop_status(request):
    status=bridge.status()
    running,queued=PromptServer.instance.prompt_queue.get_current_queue_volatile()
    status.update(running=len(running),pending=len(queued))
    status.update(capabilities=['direct_generation_v1','multi_text_v1','flow_connections_v2','clip_inputs_v3','workflow_images_v1','workflow_transfer_v1'],snapshot_versions=[1,2,3,4],running_ids=[item[1] for item in running],queued_ids=[item[1] for item in queued])
    status['executing_node']=str(PromptServer.instance.last_node_id) if running and PromptServer.instance.last_node_id is not None else None
    status['capabilities'].append('workflow_sync_v1')
    if service.native_queue.available():status['capabilities'].extend(['native_queue_v1','native_open_v1','native_bindings_v1','frozen_queue_v1','typed_inputs_v1','typed_inputs_v2','targeted_cancel_v1','input_run_bridge_v1'])
    else:status['native_unavailable_reason']='此原生入口目前只支援 ComfyUI 單一使用者模式。'
    status['pcs_version']='0.83 direct execution repair candidate (0928)'
    if service.native_queue.available():status['capabilities'].append('native_recovery_v1')
    return web.json_response(status)


@routes.post('/prompt_studio/workflow/native/poll')
@local_route
async def native_poll(request):
    return web.json_response(service.native_queue.poll(await body(request)))


@routes.post('/prompt_studio/workflow/inputs/publish')
@local_route
async def input_publish(request):
    service.native_queue.require_supported()
    return web.json_response(service.native_queue.inputs.publish(await body(request)))


@routes.post('/prompt_studio/workflow/inputs/claim')
@local_route
async def input_claim(request):
    service.native_queue.require_supported()
    return web.json_response(service.native_queue.inputs.claim(await body(request)))


@routes.post('/prompt_studio/workflow/native/start')
@local_route
async def native_start(request):
    value=await body(request);origin=str(request.url.origin())
    # This candidate requires the actual open Web workflow. Do not turn a
    # missing frontend into a submission of an older saved/captured graph.
    result=service.native_queue.start(value,origin)
    return web.json_response(result)


@routes.post('/prompt_studio/workflow/background/context')
@local_route
async def background_context(request):
    service.native_queue.require_supported()
    return web.json_response(background_execution.context(await body(request),str(request.url.origin())))


@routes.post('/prompt_studio/workflow/queue/capture')
@local_route
async def queue_capture(request):
    return web.json_response(service.native_queue.start(await body(request),str(request.url.origin()),'capture'))


def native_unresolved():
    queue=PromptServer.instance.prompt_queue
    running,queued=queue.get_current_queue_volatile()
    return service.native_queue.occupied(running,queued,lambda prompt:queue.get_history(prompt_id=prompt))


@routes.post('/prompt_studio/workflow/queue/submit')
@local_route
async def queue_submit(request):
    class PreparedRequest:
        def __init__(self,payload):self.payload=payload
        async def json(self):return self.payload
        def __getattr__(self,name):return getattr(request,name)
    handler=next((r.handler for r in routes if getattr(r,'method',None)=='POST' and getattr(r,'path',None)=='/prompt'),None)
    if handler is None:raise ValueError('ComfyUI 未提供可核對的標準提交入口。')
    async def post(payload):
        response=await handler(PreparedRequest(payload))
        return response.status,json.loads(response.text)
    result=await service.work_queue.submit(await body(request),str(request.url.origin()),post,native_unresolved())
    return web.json_response(result)


@routes.post('/prompt_studio/workflow/native/cancel')
@local_route
async def native_cancel(request):
    import nodes
    value=await body(request)
    return web.json_response(service.native_queue.cancel(value.get('id'),PromptServer.instance.prompt_queue,nodes.interrupt_processing))


@routes.post('/prompt_studio/workflow/queue/variant')
@local_route
async def queue_variant(request):
    service.native_queue.require_supported()
    return web.json_response(service.work_queue.variant(await body(request),str(request.url.origin())))


@routes.post('/prompt_studio/workflow/queue/status')
@local_route
async def queue_status(request):
    value=await body(request);ident=value['attempt']
    saved=service.work_queue.read('queue_attempts',ident)
    queue=PromptServer.instance.prompt_queue
    running,queued=queue.get_current_queue_volatile()
    return web.json_response(service.work_queue.reconcile(ident,{p[1] for p in running},{p[1] for p in queued},
        queue.get_history(prompt_id=saved['prompt_id'])))


@routes.post('/prompt_studio/workflow/background/lease')
@routes.post('/prompt_studio/workflow/background/capture')
@routes.post('/prompt_studio/workflow/background/release')
@local_route
async def background_edit(request):
    service.native_queue.require_supported()
    return web.json_response(background_execution.edit(request.path.rsplit('/',1)[-1],await body(request),str(request.url.origin())))


@routes.post('/prompt_studio/workflow/background/attach')
@local_route
async def background_attach(request):
    service.native_queue.require_supported()
    return web.json_response(background_execution.attach(await body(request),str(request.url.origin()),PromptServer.instance.sockets))


@routes.post('/prompt_studio/workflow/native/open')
@local_route
async def native_open(request):
    service.native_queue.require_supported()
    running,queued=PromptServer.instance.prompt_queue.get_current_queue_volatile()
    if running or queued:raise ValueError('ComfyUI 仍有執行中的任務，請完成或取消後再切換工作流。')
    return web.json_response(service.native_queue.start(await body(request),str(request.url.origin()),'open'))


@routes.post('/prompt_studio/workflow/native/prepare')
@local_route
async def native_prepare(request):
    return web.json_response(service.native_queue.prepare(await body(request)))


@routes.post('/prompt_studio/workflow/native/activate')
@local_route
async def native_activate(request):
    return web.json_response(service.native_queue.activate(await body(request)))


@routes.post('/prompt_studio/workflow/native/reply')
@local_route
async def native_reply(request):
    return web.json_response(service.native_queue.reply(await body(request)))


@routes.post('/prompt_studio/workflow/native/status')
@local_route
async def native_status(request):
    service.native_queue.require_supported()
    ident=(await body(request))['id']; operation=service.native_queue.read(ident)
    running,queued=PromptServer.instance.prompt_queue.get_current_queue_volatile()
    known={p[1] for p in (*running,*queued)}
    if operation.get('prompt_id'):
        known.update(PromptServer.instance.prompt_queue.get_history(prompt_id=operation['prompt_id']))
    return web.json_response(service.native_queue.reconcile(ident,known))


@routes.post('/prompt_studio/workflow/native/abandon')
@local_route
async def native_abandon(request):
    return web.json_response(service.native_queue.abandon((await body(request))['id'],PromptServer.instance.prompt_queue))


@routes.post('/prompt_studio/workflow/native/recovery')
@local_route
async def native_recovery(request):
    return web.json_response(service.native_queue.recovery((await body(request))['id'],PromptServer.instance.prompt_queue))


@routes.post('/prompt_studio/workflow/images')
@local_route
async def workflow_images(request):
    return web.json_response(node_images.publish(await body(request)))


@routes.post('/prompt_studio/workflow/run-state')
@local_route
async def workflow_run_state(request):
    from .workflow_state import latest_run,live_state
    query=await body(request)
    running,queued=PromptServer.instance.prompt_queue.get_current_queue_volatile()
    history=PromptServer.instance.prompt_queue.get_history(max_items=100)
    result=latest_run(query,history,running,queued)
    result['live']=None
    try:
        library=await asyncio.to_thread(service.read_library,include_connection=True)
        result['live']=live_state(query,library,str(request.url.origin()))
    except (ValueError,OSError) as exc:
        result['live_error']=str(exc)
    return web.json_response(result)


@routes.post('/prompt_studio/desktop/images')
@local_route
async def desktop_images(request):
    data=await body(request); queries=data.get('queries',[])
    if not isinstance(queries,list) or len(queries)>100: raise ValueError('圖片查詢數量無效。')
    history=PromptServer.instance.prompt_queue.get_history(max_items=100); result={}
    for query in queries:
        if not isinstance(query,dict) or any(not isinstance(query.get(k),str) for k in ('key','workflow','node','class_type')): raise ValueError('圖片查詢無效。')
        try: result[query['key']]=await asyncio.to_thread(node_images.resolve,query,history,str(request.url.origin()))
        except (ValueError,OSError,KeyError) as exc: result[query['key']]={'error':str(exc)}
    return web.json_response(result)


@routes.post('/prompt_studio/workflow/export')
@local_route
async def workflow_export(request):
    data=await body(request)
    return web.json_response(await asyncio.to_thread(service.publish_workflow,data['id'],data['library'],data['value']))


@routes.get('/prompt_studio/workflow/export/{ident}')
@local_route
async def workflow_export_status(request):
    return web.json_response(await asyncio.to_thread(service.workflow_status,request.match_info['ident']))


@routes.post('/prompt_studio/desktop/workflows')
@local_route
async def desktop_workflows(request):
    data=await body(request)
    return web.json_response(await asyncio.to_thread(service.take_workflow,data['library']))


@routes.post('/prompt_studio/desktop/workflows/ack')
@local_route
async def desktop_workflows_ack(request):
    data=await body(request)
    return web.json_response(await asyncio.to_thread(service.acknowledge_workflow,data['id'],data['library'],data.get('error','')))


@routes.post('/prompt_studio/desktop/claim')
@local_route
async def desktop_claim(request):
    data=await body(request)
    return web.json_response(bridge.claim(data['session'],data['target']))


@routes.post('/prompt_studio/desktop/release')
@local_route
async def desktop_release(request):
    data=await body(request)
    return web.json_response(bridge.release(data['session'],data.get('reason','已停止桌面控制。')))


@routes.post('/prompt_studio/desktop/wait')
@local_route
async def desktop_wait(request):
    data=await body(request)
    return web.json_response(await bridge.take(data['session'],data['lease']))


@routes.post('/prompt_studio/desktop/command')
@local_route
async def desktop_command(request):
    data=await body(request)
    return web.json_response(bridge.publish(data['lease'],data['id'],data['kind'],data['snapshot'],data.get('count',1)))


@routes.post('/prompt_studio/desktop/ack')
@local_route
async def desktop_ack(request):
    data=await body(request)
    return web.json_response(bridge.acknowledge(data['session'],data['lease'],data['id'],data.get('error',''),data.get('prompt_id','')))


@routes.get('/prompt_studio/desktop/command/{ident}')
@local_route
async def desktop_command_status(request):
    return web.json_response(bridge.result(request.match_info['ident']))


@routes.get('/prompt_studio/desktop/results')
@local_route
async def desktop_results(request):
    ident=request.query.get('prompt_id','')
    if ident and (len(ident)>128 or any(ord(c)<32 for c in ident)):raise ValueError('任務識別無效。')
    history=PromptServer.instance.prompt_queue.get_history(prompt_id=ident) if ident else PromptServer.instance.prompt_queue.get_history(max_items=50)
    records=service.results(history)
    for record in records:
        try: record['path']=str(service.source(record['image']))
        except (OSError,ValueError): record['path']=''
    return web.json_response(records)


@routes.post('/prompt_studio/desktop/collect')
@local_route
async def desktop_collect(request):
    data=await body(request)
    history=PromptServer.instance.prompt_queue.get_history(prompt_id=str(data.get('prompt_id','')))
    if not any(r['image']==data.get('image') for r in service.results(history)):
        raise ValueError('結果已不在 ComfyUI 歷史，無法確認來源。')
    return web.json_response(await asyncio.to_thread(service.collect,data['image'],'',data['prompt_id'],data['destination']))


@routes.post('/prompt_studio/desktop/interrupt')
@local_route
async def desktop_interrupt(request):
    import nodes
    data=await body(request)
    service.native_queue.cancel_pending()
    if data.get('clear_pending'):
        bridge.cancel_runs()
        PromptServer.instance.prompt_queue.wipe_queue()
        PromptServer.instance.send_sync('prompt_studio_cancel',{})
    nodes.interrupt_processing()
    return web.json_response({'ok':True})
