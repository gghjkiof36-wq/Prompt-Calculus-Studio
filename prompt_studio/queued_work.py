"""Immutable work records. Source snapshots retain the existing PNG schema."""
import copy
import hashlib
import json
from .generation import api_graph
from .snapshots import validate_snapshot
from .native_graph import graph_matches

PROTOCOL = 1
MAX_BYTES = 3 * 1024 * 1024
ACTIVE = ('submitting', 'queued', 'running', 'unconfirmed', 'results_pending')


def image_count(graph, key, seen=None):
    """Cardinality only for standard nodes known to preserve image batch size."""
    seen=set() if seen is None else set(seen)
    if key in seen:return None
    seen.add(key);node=graph.get(key,{})
    kind=node.get('class_type');inputs=node.get('inputs',{})
    if kind in ('EmptyLatentImage','EmptySD3LatentImage'):
        count=inputs.get('batch_size')
        return count if type(count) is int and count>0 else None
    # LoadImage may decode several frames (for example animated PNG/WebP).
    # Its terminal history, not the filename, defines the output collection.
    if kind=='LoadImage':return None
    field={'SaveImage':'images','PreviewImage':'images','VAEDecode':'samples','VAEDecodeTiled':'samples',
           'KSampler':'latent_image','KSamplerAdvanced':'latent_image','VAEEncode':'pixels',
           'ImageScale':'image','ImageScaleBy':'image','ImageUpscaleWithModel':'image'}.get(kind)
    link=inputs.get(field) if field else None
    if isinstance(link,list) and len(link)==2 and link[1]==0:return image_count(graph,str(link[0]),seen)
    return None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def validate(work):
    if not isinstance(work, dict) or work.get('protocol') != PROTOCOL:
        raise ValueError('不支援的 Queue 工作版本。')
    if len(json.dumps(work, ensure_ascii=False, allow_nan=False).encode()) > MAX_BYTES:
        raise ValueError('Queue 工作快照過大。')
    source = validate_snapshot(work.get('snapshot'))
    if work.get('input_snapshot') is not None:validate_snapshot(work['input_snapshot'])
    graph = api_graph(work.get('graph'))
    scope = work.get('scope', {})
    if (not isinstance(scope, dict) or any(not isinstance(scope.get(k), str) or not scope[k]
            for k in ('server', 'workspace', 'workflow', 'frontend_id'))
            or scope['workspace'] != source['state']['workspace']):
        raise ValueError('Queue 工作歸屬不符。')
    if not isinstance(work.get('visual'), dict) or work['visual'].get('id') != scope['frontend_id']:
        raise ValueError('Queue 可視工作流身分不符。')
    outputs = work.get('outputs')
    if not isinstance(outputs, list) or not outputs or any(n not in graph for n in outputs):
        raise ValueError('請使用至少一個可確認圖片結果的輸出節點。')
    for binding in work.get('texts', []):
        if graph.get(binding['node'], {}).get('inputs', {}).get(binding['field']) != binding['text']:
            raise ValueError('Queue 實際文字與快照不符。')
    expected = work.get('sha256')
    if expected != digest({k: v for k, v in work.items() if k != 'sha256'}):
        raise ValueError('Queue 工作內容已變更。')
    return work


def seal(work):
    work = copy.deepcopy(work)
    work.pop('sha256', None)
    work['sha256'] = digest(work)
    return validate(work)


def envelope(work, job_id, attempt_id, prompt_id):
    validate(work)
    first = next(iter(work['texts']), {})
    result=dict(schema_version=1, submission_id=prompt_id, problems=[],
                texts=copy.deepcopy(work['texts']), source_texts=copy.deepcopy(work['source_texts']),
                generation=dict(work['generation'], queue_job=job_id, queue_attempt=attempt_id,
                                queue_revision=work['sha256'], assets=copy.deepcopy(work['assets'])),
                bindings=[dict(node_id=first.get('node', ''), field=first.get('field', ''),
                               node_title='Queue 工作快照', snapshot=copy.deepcopy(work['snapshot']))])
    if work.get('input_snapshot'):
        result['bindings'].append(dict(node_id='',field='',node_title='來源圖片組合',snapshot=copy.deepcopy(work['input_snapshot'])))
    if len(json.dumps(result,ensure_ascii=False).encode())>2*1024*1024:
        raise ValueError('圖片 Metadata 超過可還原大小，未派送。')
    return result


def result_state(work, prompt_id, entry):
    """Only this job's terminal history with all declared outputs can advance."""
    prompt = entry.get('prompt', [])
    if len(prompt) < 4 or prompt[1] != prompt_id:
        return 'unconfirmed', '任務識別不符，仍待核對。'
    status = entry.get('status', {})
    terminal = status.get('completed') is True or status.get('status_str') == 'error'
    if not graph_matches(work['graph'], prompt[2], work.get('input_types')):
        return ('failed' if terminal else 'unconfirmed'), '工作已結束但輸入內容不符，請核對此項紀錄。' if terminal else '實際工作流與提交內容不符。'
    if status.get('status_str') == 'error' or any(
            isinstance(m, (list, tuple)) and m and m[0] in ('execution_error', 'execution_interrupted')
            for m in status.get('messages', [])):
        return 'failed', 'ComfyUI 回報執行失敗或已中止。'
    if status.get('completed') is not True:
        return 'running', ''
    outputs = entry.get('outputs', {})
    missing = [n for n in work['outputs'] if not isinstance(outputs.get(n), dict)
               or not outputs[n].get('images') or image_count(work['graph'],n) is not None and
               len(outputs[n]['images'])!=image_count(work['graph'],n)]
    if missing:
        return 'failed', '工作已結束，但指定節點未輸出完整圖片：' + ', '.join(missing)
    return 'complete', ''
