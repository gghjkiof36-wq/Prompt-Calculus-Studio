"""Qt-free module/port catalogue shared by Canvas validation and presentation.

Ports describe data or control, never execute work. Adding a new value type also
requires an explicit validator/resolver in flow_data; no implicit conversions.
Serialized names (including the historical text/clip distinction) remain stable.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class WireType:
    label: str
    color: str
    role: str


WIRES = {
    'clip': WireType('文字', '#9cbff3', 'data'),
    'image': WireType('圖片', '#e0bb79', 'data'),
    'content': WireType('文字組合', '#a9ceb0', 'data'),
    'text': WireType('文字', '#9cbff3', 'canvas_text'),
    'control': WireType('流程', '#b8a8e6', 'binding'),
    'flow': WireType('流程', '#b8a8e6', 'reference'),
    'done': WireType('流程', '#b8a8e6', 'dependency'),
    'execution': WireType('執行', '#93cbb2', 'legacy'),
    'preview': WireType('預覽', '#baa6e0', 'legacy'),
}
DATA_TYPES = tuple(key for key, value in WIRES.items() if value.role == 'data')


@dataclass(frozen=True)
class ModuleType:
    category: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]


MODULES = {
    'image': ModuleType('source', ('image',), ('image', 'content', 'clip')),
    'image_source': ModuleType('source', ('image',), ('image', 'content', 'clip')),
    'text_reader': ModuleType('source', ('clip',), ('clip',)),
    'group_source': ModuleType('source', (), ('content', 'clip')),
    'canvas': ModuleType('composition', ('image', 'content', 'clip'), ('text', 'image')),
    'prompt': ModuleType('transform', ('text', 'clip', 'image'), ('clip',)),
    'clip_input': ModuleType('binding', ('clip',), ('control',)),
    'image_input': ModuleType('binding', ('image',), ('control',)),
    'stage': ModuleType('execution', ('control', 'done'), ('flow', 'image', 'clip', 'done')),
    'scheduler': ModuleType('scheduling', (), ()),  # Paired dynamic endpoints.
    'preview': ModuleType('view', ('image',), ()),
}
COLLECTIONS = {'canvases': 'canvas', 'outputs': 'prompt', 'clip_inputs': 'clip_input',
               'image_inputs': 'image_input', 'stages': 'stage', 'schedulers': 'scheduler'}
BINDINGS_CHANNEL = 'bindings'


def binding_scheduler(state, key):
    parent, separator, channel = key.partition('::')
    return parent if separator and channel == BINDINGS_CHANNEL and parent in state['multi_output'].get('schedulers', {}) else None


def binding_sources(state, scheduler):
    endpoint = scheduler+'::'+BINDINGS_CHANNEL
    return [c['source'] for c in state['multi_output']['connections']
            if c['destination'] == endpoint and c['kind'] == 'control']


def scheduler_port_specs(state, scheduler):
    """One layout contract for the real card and the add-menu preview."""
    data=state['multi_output'];item=data['schedulers'][scheduler];result=[]
    for index,channel in enumerate(item['channels']):
        result.extend((scheduler+'::'+channel['id'],channel['type'],output,None,index) for output in (False,True))
    index=len(item['channels'])
    if data['version']>=7:
        if item.get('mode','data')=='data':
            endpoint=scheduler+'::'+BINDINGS_CHANNEL;sources=binding_sources(state,scheduler)
            result.extend((endpoint,'control',False,source,index+offset) for offset,source in enumerate([*sources,None]))
            result.append((endpoint,'control',True,None,index));index+=len(sources)+1
        result.extend((scheduler+'::flow','flow',output,None,index) for output in (False,True))
    return result


def module_kind(state, key):
    parent = key.split('::')[0]
    if parent in state.get('canvas_functions', {}).get('text_sources', {}): return 'group_source'
    from .preview_nodes import contains
    if contains(state,parent): return 'preview'
    data = state.get('multi_output', {})
    for collection, kind in COLLECTIONS.items():
        if parent in data.get(collection, {}): return kind
    item = state.get('canvas_functions', {}).get('images', {}).get(parent)
    if item is not None: return 'text_reader' if item.get('reader')=='text' else 'image_source' if item.get('iterate') else 'image'
    return None


def port_types(state, key, output):
    kind = module_kind(state, key)
    if kind is None: return ()
    parent, separator, channel = key.partition('::')
    if kind == 'scheduler':
        if not separator: return ()
        if channel == 'flow': return ('flow',)
        if channel == BINDINGS_CHANNEL:
            return ('control',) if state['multi_output']['schedulers'][parent].get('mode', 'data') == 'data' else ()
        item = next((c for c in state['multi_output']['schedulers'][parent]['channels'] if c['id'] == channel), None)
        return (item['type'],) if item else ()
    if separator: return ()
    return MODULES[kind].outputs if output else MODULES[kind].inputs


def can_connect(state, source, destination, kind):
    if kind == 'flow':
        parent = destination.split('::')[0]
        endpoint = parent+'::'+BINDINGS_CHANNEL
        if any(c['kind'] == 'control' and endpoint in (c['source'], c['destination'])
               for c in state['multi_output'].get('connections', [])):return False
    return (source.split('::')[0] != destination.split('::')[0]
            and kind in port_types(state, source, True)
            and kind in port_types(state, destination, False))
