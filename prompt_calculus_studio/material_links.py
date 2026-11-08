"""Library text members follow source edits; placements and frozen runs stay owned.

The saved base permits a three-way merge, preserving local edits and controls.
There is no lookup or mutation during rendering or generation.
"""
import copy
import uuid
from . import composition as comp, prompt_materials as pm

FIELD = 'member_link'
LOCAL = {'id', 'selected', 'enabled', 'weight', 'collapsed', 'grouped', 'preview', *pm.RUNTIME_FIELDS}


def validate(link):
    if (not isinstance(link,dict) or not isinstance(link.get('item'),str) or not link['item']
            or link.get('document') is not None and (not isinstance(link['document'],str) or not link['document'])
            or not isinstance(link.get('base'),dict) or not isinstance(link.get('ids'),dict)
            or not link['ids'] or any(not isinstance(k,str) or not k or not isinstance(v,str) or not v
                                     for k,v in link['ids'].items())):
        raise ValueError('組合素材的同步來源格式無效，原內容保留。')
    base=copy.deepcopy(link['base']);pending=[base];count=0
    while pending:
        value=pending.pop();count+=1
        if count>50000:raise ValueError('組合素材的同步來源過大。')
        if isinstance(value,list):pending.extend(value)
        elif isinstance(value,dict):
            if FIELD in value:raise ValueError('同步來源不可再次包含同步來源。')
            for field in ('variants','sequence'):
                seq=value.get(field)
                if isinstance(seq,dict):seq.setdefault('selected',seq.get('start'))
            pending.extend(value.values())
    comp.validate_node(base)
    if pm.kind(base)!='text':raise ValueError('組合成員只能同步文字素材。')


def definition(value):
    if isinstance(value, list):return [definition(v) for v in value]
    if not isinstance(value, dict):return value
    return {k:definition(v) for k,v in value.items()
            if k not in pm.RUNTIME_FIELDS and k not in (FIELD,'selected') and not k.startswith('source_')}


def remap(value, mapping):
    if isinstance(value,list):return [remap(v,mapping) for v in value]
    if not isinstance(value,dict):return value
    return {k:mapping.get(v,v) if k in ('id','start','selected') and isinstance(v,str)
            else remap(v,mapping) for k,v in value.items()}


def identifiers(value):
    if isinstance(value,dict):
        if isinstance(value.get('id'),str):yield value['id']
        for item in value.values():yield from identifiers(item)
    elif isinstance(value,list):
        for item in value:yield from identifiers(item)


def bind(member,item,document=None,*,base=None):
    """Attach only an explicitly chosen or uniquely identified text member."""
    source=definition(comp.prototype(item));base=definition(base) if base else source
    mapping={base['id']:member['id'],source['id']:member['id']}
    # Fresh clones retain the structure and group IDs; legacy members only
    # match unchanged, unique subtrees. Never pair reordered children by index.
    def shape(node):
        value=definition(node)
        def strip(v):
            if isinstance(v,list):return [strip(x) for x in v]
            if not isinstance(v,dict):return v
            return {k:strip(x) for k,x in v.items() if k not in LOCAL}
        return strip(value)
    for old in comp.walk(base):
        if old is base:continue
        matches=[part for part in comp.walk(member) if shape(part)==shape(old)]
        if len(matches)==1:mapping[old['id']]=matches[0]['id']
    for ident in identifiers(base):mapping.setdefault(ident,ident)
    if base!=source:
        for part in comp.walk(source):
            matches=[node for node in comp.walk(member) if shape(node)==shape(part)]
            if len(matches)==1:mapping[part['id']]=matches[0]['id']
    member[FIELD]=dict(item=item['id'],document=document,base=base,ids=mapping)


def linked_copy(item,document=None):
    root=pm.reset(comp.clone_node(comp.prototype(item)))
    bind(root,item,document)
    root[FIELD]['ids'].update({source['id']:target['id']
        for source,target in zip(comp.walk(comp.prototype(item)),comp.walk(root))})
    return root


def merge(base,current,updated):
    if all(isinstance(v,dict) for v in (base,current,updated)):
        result=copy.deepcopy(current)
        for key in base.keys()|updated.keys():
            if key in LOCAL:continue
            if key not in updated:
                if result.get(key)==base[key]:result.pop(key,None)
            elif key not in base:
                result.setdefault(key,copy.deepcopy(updated[key]))
            elif key in current:
                result[key]=merge(base[key],current[key],updated[key])
        return result
    if all(isinstance(v,list) and all(isinstance(x,dict) and 'id' in x for x in v) for v in (base,current,updated)):
        old={v['id']:v for v in base};new={v['id']:v for v in updated};now={v['id']:v for v in current}
        order=[v['id'] for v in updated] if [v['id'] for v in current]==[v['id'] for v in base] else list(now)+[k for k in new if k not in old and k not in now]
        result=[]
        for ident in order:
            if ident not in now:
                if ident not in old:result.append(copy.deepcopy(new[ident]))
            elif ident not in new:
                if ident not in old or definition(now[ident])!=definition(old[ident]):result.append(copy.deepcopy(now[ident]))
            elif ident in old:result.append(merge(old[ident],now[ident],new[ident]))
            else:result.append(copy.deepcopy(now[ident]))
        return result
    if current==base:return copy.deepcopy(updated)
    return copy.deepcopy(current)


def update(member,item):
    link=member[FIELD];source=definition(comp.prototype(item))
    if source==link['base']:return False
    existing={part['id'] for part in comp.walk(member)}
    valid_cursors={part['id'] for part in comp.walk(member)
        if part.get('selection_cursor',{}).get('signature')==pm.signature(part)}
    mapping=copy.deepcopy(link['ids'])
    for ident in identifiers(source):mapping.setdefault(ident,uuid.uuid4().hex)
    before=remap(link['base'],mapping);after=remap(source,mapping)
    result=merge(before,member,after)
    result[FIELD]=dict(link,base=source,ids=mapping)
    from .materials import fingerprint
    result['source_revision']=fingerprint(item)
    source_nodes={mapping[part['id']]:part for part in comp.walk(comp.prototype(item))}
    for part in comp.walk(result):
        if part['id'] not in existing and part['id'] in source_nodes:
            original=source_nodes[part['id']]
            for key,value in original.items():
                if key.startswith('source_'):part[key]=copy.deepcopy(value)
            if original.get(FIELD):
                part[FIELD]=copy.deepcopy(original[FIELD])
                part[FIELD]['ids']={key:mapping.get(value,value) for key,value in part[FIELD]['ids'].items()}
                if not part[FIELD].get('document'):part[FIELD]['document']=link.get('document')
            elif part.get('source_id') and link.get('document'):
                part.setdefault('source_document',link['document'])
            # Preview assets belong to the destination document. Text sync
            # cannot introduce an unresolved path from another document.
            if link.get('document'):part.pop('preview',None)
        seq=pm.sequence(part)
        if not seq:continue
        valid={g['id']:g for g in seq['groups']}
        if seq['start'] not in valid:seq['start']=seq['groups'][0]['id']
        if seq.get('selected') not in valid:seq['selected']=seq['start']
        cursor=part.get('selection_cursor')
        if cursor:
            position=cursor['position']
            if part['id'] not in valid_cursors or position['group'] not in valid:part.pop('selection_cursor')
            else:
                position['repeat']=min(position['repeat'],valid[position['group']]['repeat']-1)
                if 'bag' in position:
                    position['bag']=[key for key in position['bag'] if key in valid]
                    if position['group'] not in position['bag']:
                        part.pop('selection_cursor');continue
                    position['bag_index']=position['bag'].index(position['group'])
                cursor['signature']=pm.signature(part)
    comp.validate_node(result)
    member.clear();member.update(result)
    return True


def members(root,inside=False):
    if inside and pm.kind(root)=='text' and (FIELD in root or root.get('source_id')):
        yield root
    nested=inside or pm.kind(root)=='combination'
    for child in root['children']+root['overlays']:yield from members(child,nested)
    for child in root.get('combination',{}).get('modules',[]):yield from members(child,True)
    for group in root.get('sequence',{}).get('groups',[]):
        for child in group['modules']:yield from members(child,True)


def roots(state):
    for owner in [state,*state.get('workspaces',[]),*state.get('workspace_scenes',{}).get('items',{}).values()]:
        yield from owner.get('uses',{}).values()
        yield from owner.get('instances',{}).values()
        for item in owner.get('items',[]):
            if item.get('composition'):yield item['composition']


def sync(state,lookup,previous=None,*,dependencies=None):
    changed=False;updated_roots=set()
    cycle_cache={}
    def cyclic(item,document,trail=()):
        key=(document,item['id'])
        if key in trail or len(trail)>=32:return True
        if key in cycle_cache:return cycle_cache[key]
        source=comp.prototype(item)
        for child in comp.walk(source):
            if child is source or pm.kind(child)!='text' or not (FIELD in child or child.get('source_id')):continue
            target=child
            if document and not child.get(FIELD,{}).get('document') and not child.get('source_document'):
                target=copy.deepcopy(child)
                if FIELD in target:target[FIELD]['document']=document
                else:target['source_document']=document
            nested,owner=(dependencies or lookup)(target)
            if nested and cyclic(nested,owner,(*trail,key)):
                cycle_cache[key]=True;return True
        cycle_cache[key]=False;return False
    for root in roots(state):
        for member in members(root):
            link=member.get(FIELD)
            item,document=lookup(member)
            if not item or pm.kind(item)!='text':continue
            if cyclic(item,document):continue
            if not link:
                # A provenance match establishes identity, not permission to
                # overwrite old local edits. Only a known old source is a base.
                old=previous.get(item['id']) if previous else None
                bind(member,item,document,base=comp.prototype(old) if old else None)
                changed=True;updated_roots.add(id(root))
            if update(member,item):changed=True;updated_roots.add(id(root))
    for item in state.get('items',[]):
        if id(item.get('composition')) in updated_roots:item['prompt']=comp.render(item['composition'])
    return changed


def sync_local(state,previous):
    catalog={item['id']:item for item in state.get('items',[])}
    def lookup(member):
        link=member.get(FIELD,{})
        if link.get('document') is not None:return None,None
        item=catalog.get(link.get('item',member.get('source_id')))
        if item and not link and member.get('source_module') not in (None,item['module']):item=None
        return item,None
    return sync(state,lookup,previous)
