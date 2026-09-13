"""Prompt Studio: frontend extension only; no extra graph nodes or Qt."""
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
from .service import Service
from .bridge import DesktopBridge
from .shared.snapshots import make_snapshot

WEB_DIRECTORY = './web'
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
_bootstrap = Path(__file__).with_name('local_library.json')
_default_library = json.loads(_bootstrap.read_text(encoding='utf-8')).get('library', '') if _bootstrap.exists() else ''
service = Service(Path(folder_paths.get_user_directory()) / 'prompt_studio',
                  folder_paths.get_output_directory(), folder_paths.get_temp_directory(), _default_library)
_token = secrets.token_urlsafe(32)
routes = PromptServer.instance.routes
bridge = DesktopBridge()


def local_route(function):
    @functools.wraps(function)
    async def wrapped(request):
        try:
            peer = ipaddress.ip_address(request.remote or '')
            if not (peer.is_loopback or (getattr(peer, 'ipv4_mapped', None) and peer.ipv4_mapped.is_loopback)):
                raise web.HTTPForbidden(text='Prompt Studio 第一版只接受本機連線。')
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
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return web.json_response({'error': str(exc)}, status=400)
        except Exception:
            logging.exception('Prompt Studio request failed')
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
    status.update(capabilities=['direct_generation_v1','multi_text_v1','flow_connections_v2','workflow_transfer_v1'],snapshot_versions=[1,2,3,4],running_ids=[item[1] for item in running],queued_ids=[item[1] for item in queued])
    return web.json_response(status)


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
    records=service.results(PromptServer.instance.prompt_queue.get_history(max_items=50))
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
    if data.get('clear_pending'):
        bridge.cancel_runs()
        PromptServer.instance.prompt_queue.wipe_queue()
        PromptServer.instance.send_sync('prompt_studio_cancel',{})
    nodes.interrupt_processing()
    return web.json_response({'ok':True})
