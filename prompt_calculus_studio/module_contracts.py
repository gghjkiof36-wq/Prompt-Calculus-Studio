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


def module_kind(state, key):
    parent = key.split('::')[0]
    if parent == '__result_preview__': return 'preview'
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
        item = next((c for c in state['multi_output']['schedulers'][parent]['channels'] if c['id'] == channel), None)
        return (item['type'],) if item else ()
    if separator: return ()
    return MODULES[kind].outputs if output else MODULES[kind].inputs


def can_connect(state, source, destination, kind):
    return (source.split('::')[0] != destination.split('::')[0]
            and kind in port_types(state, source, True)
            and kind in port_types(state, destination, False))
