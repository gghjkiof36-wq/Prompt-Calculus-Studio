"""Worker preparation of owned final metadata; no live state or DB writes."""
import copy
import json

from . import journal_codec as codec


def prepare(directory, job, assets, attempt, run, fence, job_fence):
    from .generation import profile_text_fields
    from .stage_runner import merge_results
    results=[];images=[]
    for item,receipt in assets:
        node,index,image=item['node_id'],item['item_index'],item['image']
        source=dict(receipt['source'])
        results.append(dict(prompt_id=job['prompt_id'],node_id=node,image=dict(image),
                            path=str(directory/source['relative'])))
        if attempt:
            source['reference']=dict(run=run['id'],stage=attempt['stage'],attempt=attempt['id'],
                prompt_id=job['prompt_id'],node=node,index=index,round=attempt.get('round'),parent=attempt.get('parent'))
        images.append(source)
    candidate=dict(job,state='complete',error='',results=results)
    prepared=dict(job=candidate,job_json=json.dumps(candidate,ensure_ascii=False),fence=fence,job_fence=job_fence)
    if not attempt:return prepared
    stage=run['plan']['stages'][attempt['stage']];graph=job.get('payload',{}).get('prompt',{})
    field=stage.get('text_output');text=graph.get(field[0],{}).get('inputs',{}).get(field[1]) if field else None
    profile=next((p for p in attempt.get('snapshot_state',{}).get('generation',{}).get('profiles',[]) if p['id']==stage['workflow']),{})
    texts=[dict(node=node,field=field,text=graph[node]['inputs'][field]) for node,field in profile_text_fields(dict(profile,graph=graph))]
    result=dict(run=run['id'],images=images,text=text,texts=texts,attempts=[attempt['id']],records=[dict(attempt=attempt['id'],text=text,texts=texts)])
    frame=next((f for f in run['stack'] if f.get('job')==attempt['id']),None)
    produced=copy.deepcopy(frame['produced']) if frame else None
    if frame and run['status']!='cancelled':merge_results(produced,{attempt['stage']:result})
    from .stage_invariants import advance_result
    from .stage_runner import progress_fields
    advance_result(run,attempt,result,produced=produced)
    prepared.update(result=result,recent=copy.deepcopy(results),produced=produced,
        preview=copy.deepcopy(result),display=copy.deepcopy(produced[attempt['stage']]) if produced and attempt['stage'] in produced else copy.deepcopy(result),
        result_parts=codec.split(None,dict(id=attempt['id'],kind='attempt',status='complete',result=result,error='',cancel_requested=False)),
        run_parts=codec.split(None,dict(id=run['id'],kind='run',**progress_fields(run))),
        evidence_parts=codec.split(None,dict(id=attempt['id'],kind='attempt',operation_evidence=candidate,local_issue='')))
    return prepared
