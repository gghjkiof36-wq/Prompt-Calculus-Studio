"""Read-only image-input candidates for reuse and explicit legacy rewiring."""


def output_workflows(state, output):
    data=state['multi_output']; profiles={p['id']:p for p in state.get('generation',{}).get('profiles',[])}
    workflows=set()
    for edge in data['connections']:
        if edge['source']!=output or edge['kind']!='clip':continue
        clip=data['clip_inputs'].get(edge['destination'])
        if not clip:continue
        workflow=clip.get('workflow');profile=profiles.get(workflow)
        bindings=[b for b in data['bindings'] if b['clip']==edge['destination'] and b['workflow']==workflow]
        if profile and len(bindings)==1:
            binding=bindings[0];node=profile['graph'].get(binding['node'],{})
            if isinstance(node.get('inputs',{}).get(binding['field']),str):workflows.add(workflow)
    return workflows


def valid_image_binding(profile, node):
    value=(profile or {}).get('graph',{}).get(node,{})
    return value.get('class_type')=='LoadImage' and isinstance(value.get('inputs',{}).get('image'),str)


def candidates(state, output=None):
    data=state['multi_output']
    if output is not None and output not in data['outputs']:raise ValueError('Prompt 輸出已移除。')
    context=output if output is not None else data.get('current_output')
    workflows=output_workflows(state,context)
    # A Prompt with no direct binding or several workflows has no unambiguous
    # workflow owner. Let the setup dialog ask explicitly; never borrow a card
    # from a neighbouring workflow or infer ownership from a Stage group.
    if len(workflows)!=1:return []
    profiles={p['id']:p for p in state.get('generation',{}).get('profiles',[])}
    result=[]
    for number,(key,item) in enumerate(data['image_inputs'].items(),1):
        workflow=item.get('workflow');node=item.get('node');profile=profiles.get(workflow)
        if workflow not in workflows:continue
        occupied=any(c['kind']=='image' and c['destination']==key for c in data['connections'])
        valid=valid_image_binding(profile,node) and sum(
            v.get('workflow')==workflow and v.get('node')==node for v in data['image_inputs'].values())==1
        result.append(dict(key=key,number=number,workflow=workflow,node=node,profile=profile,
                           occupied=occupied,valid=valid,available=valid and not occupied))
    return result


def rewire_candidate(state, edge, target):
    """An explicit old-wire destination must be bound, empty and in its workflow."""
    workflows=output_workflows(state,edge['destination'])
    if len(workflows)!=1:return False
    row=next((r for r in candidates(state,edge['destination']) if r['key']==target),None)
    if not row or not row['available']:return False
    pending=[target];seen=set()
    while pending:
        current=pending.pop()
        if current==edge['source'].split('::')[0]:return False
        if current in seen:continue
        seen.add(current)
        pending.extend(c['destination'].split('::')[0] for c in state['multi_output']['connections']
                       if c['id']!=edge['id'] and c['source'].split('::')[0]==current)
    return True
