"""Explicit owned-ID remapping; external targets and provenance are immutable."""
import copy
from dataclasses import dataclass
import uuid

from .. import composition, prompt_materials
from ..prompt_sources import legacy_signature
from .schema import validate


def fresh(prefix=''):
    return prefix + uuid.uuid4().hex


def provenance(document):
    """Locate embedded-copy provenance without opening any other root or DB."""
    from .policy import material_roots
    validate(document)
    if document['kind'] == 'chat':return []
    state = document['payload']['state'] if document['kind'] == 'workflow' else {'items': document['payload']['items']}
    originals = {i['id']: i for i in state.get('items', [])}
    result = []
    for root in material_roots(state):
        for part in composition.walk(root):
            if not part.get('source_id'):
                continue
            record = originals.get(part['source_id'])
            matches = record is not None and record['module'] == part.get('source_module')
            result.append(dict(node=part['id'], source_id=part['source_id'],
                source_module=part.get('source_module'), source_revision=part.get('source_revision'),
                source_document=part.get('source_document'), embedded=True,
                resolution=('owned-record' if document['kind'] == 'category' else 'pinned-dependency')
                           if matches else 'unresolved-provenance'))
    return result


def _root(root, mapping):
    """A material tree has its own node/group ID scope, including inactive data."""
    result = copy.deepcopy(root)
    parts = list(composition.walk(result))
    valid = [(part, field) for part in parts for field, signature in
             (('selection_cursor', prompt_materials.signature), ('supply_cursor', legacy_signature))
             if part.get(field) and (field != 'supply_cursor' or part.get('sequence'))
             and part[field]['signature'] == signature(part)]
    for part in parts:
        mapping.setdefault(part['id'], fresh())
        for name in ('sequence', 'variants'):
            for group in part.get(name, {}).get('groups', []):
                mapping.setdefault(group['id'], fresh())
        if part.get('combination'):
            mapping.setdefault(part['combination']['id'], fresh())
    for part in parts:
        part['id'] = mapping[part['id']]
        for name in ('sequence', 'variants'):
            sequence = part.get(name)
            if sequence:
                sequence['start'] = mapping[sequence['start']]
                sequence['selected'] = mapping[sequence['selected']]
                for group in sequence['groups']:
                    group['id'] = mapping[group['id']]
        if part.get('combination'):
            group = part['combination']
            group['id'] = mapping[group['id']]
        for field in ('selection_cursor', 'supply_cursor'):
            if part.get(field):
                position = part[field]['position']
                position['group'] = mapping.get(position['group'], position['group'])
                if 'bag' in position:
                    position['bag'] = [mapping.get(key, key) for key in position['bag']]
        if 'round_drafts' in part:
            part['round_drafts'] = {mapping.get(key, key): value for key, value in part['round_drafts'].items()}
        if part.get('round_override'):
            override = part['round_override']
            override['group'] = mapping.get(override['group'], override['group'])
    # Only resign previously valid cursors. A stale cursor must remain stale.
    for part, field in reversed(valid):
        signature = prompt_materials.signature if field == 'selection_cursor' else legacy_signature
        part[field]['signature'] = signature(part)
    return result


@dataclass(frozen=True)
class Duplicate:
    document: dict
    id_maps: dict


def duplicate(document, *, id_maps=None, document_id=None):
    """Create an independent content identity, without writing or duplicating jobs."""
    candidate = copy.deepcopy(validate(document))
    candidate['document_id'] = document_id or str(uuid.uuid4())
    candidate['revision'] = dict(id=str(uuid.uuid4()), parent=None)
    candidate['extensions']['pcs.duplicate_source'] = dict(
        document_id=document['document_id'], revision=document['revision']['id'])
    maps = {} if id_maps is None else id_maps

    def mapped(scope, key, prefix=''):
        return maps.setdefault(scope, {}).setdefault(key, fresh(prefix))

    def tree(root, scope):
        return _root(root, maps.setdefault(scope, {}))

    if candidate['kind'] == 'chat':
        from .chat import clone_payload
        candidate['payload'] = clone_payload(candidate['payload'])
        return Duplicate(validate(candidate), maps)
    if candidate['kind'] == 'category':
        payload = candidate['payload']
        category = payload['category']
        owned = maps.setdefault('catalog', {})
        for key in [category['id'], *(item['id'] for item in payload['items'])]:
            owned.setdefault(key, fresh())
        category['id'] = owned[category['id']]
        for index, item in enumerate(payload['items']):
            old_id = item['id']
            item['id'] = owned[item['id']]
            item['module'] = owned[item['module']]
            if item.get('composition'):
                item['composition'] = tree(item['composition'], f'item/{old_id}')
    else:
        state = candidate['payload']['state']
        workspace = state['workspaces'][0]
        old_workspace = workspace['id']
        workspace['id'] = state['workspace'] = mapped('workspace', old_workspace)
        data = state.get('multi_output', {})
        functions = state.get('canvas_functions', {})
        owned = maps.setdefault('scene', {})
        collections = [state.get('uses', {}), workspace.get('uses', {})]
        collections += [data.get(name, {}) for name in ('canvases', 'outputs', 'clip_inputs', 'image_inputs', 'schedulers', 'stages')]
        collections += [functions.get(name, {}) for name in ('images', 'text_sources', 'previews')]
        for collection in collections:
            for key in collection:
                prefix = '__source_' if key in functions.get('images', {}) else 'groups_' if key in functions.get('text_sources', {}) else 'doc_'
                owned.setdefault(key, fresh(prefix))
        for key, scheduler in data.get('schedulers', {}).items():
            for channel in scheduler['channels']:
                old = channel['id']
                channel['id'] = mapped('channels/'+key, old, 'channel_')
                owned[key + '::' + old] = owned[key] + '::' + channel['id']

        def reference(key):
            if not isinstance(key, str):
                return key
            if key in owned:
                return owned[key]
            parent, separator, port = key.partition('::')
            return owned.get(parent, parent) + separator + port

        chain = data.get('workflow_order', {}).get('chain')
        if chain:
            owned.setdefault(chain['id'], fresh())
            chain['id'] = owned[chain['id']]
            for stage in chain['stages']:
                old = stage['id']
                owned.setdefault(old, fresh())
                owned['__workflow_order__::' + old] = '__workflow_order__::' + owned[old]
            chain['source'] = reference(chain.get('source'))
            for stage in chain['stages']:
                stage['id'] = owned[stage['id']]
                if stage.get('control'):
                    stage['control'] = reference(stage['control'])
                if stage.get('upstream'):
                    stage['upstream']['stage'] = owned[stage['upstream']['stage']]
                for field in ('texts', 'images'):
                    for entry in stage[field]:
                        if isinstance(entry.get('source'), str):
                            entry['source'] = reference(entry['source'])
                        elif isinstance(entry.get('source'), dict) and entry['source'].get('stage'):
                            entry['source']['stage'] = owned[entry['source']['stage']]

        def owner_roots(owner, scope):
            for field in ('uses', 'instances'):
                if field in owner:
                    owner[field] = {reference(key) if field == 'uses' else key: tree(root, f'{scope}/{field}/{key}')
                                    for key, root in owner[field].items()}
            for field in ('fixed_uses', 'output_order'):
                if field in owner:
                    owner[field] = [reference(key) for key in owner[field]]
            for field in ('text_positions', 'text_sizes'):
                if field in owner:
                    owner[field] = {reference(key): value for key, value in owner[field].items()}
            if 'canvas_owners' in owner:
                owner['canvas_owners'] = {key: reference(value) for key, value in owner['canvas_owners'].items()}

        owner_roots(state, 'active')
        owner_roots(workspace, 'workspace')
        starter = workspace.get('canvas_starter')
        if starter:
            # This is UI starter ownership, not external graph identity.
            for key, value in starter.items():
                if isinstance(value, str):
                    starter[key] = state['workspace'] if value == old_workspace else reference(value)
        for output_id, output in data.get('outputs', {}).items():
            output['canvas'] = reference(output.get('canvas'))
            for field in ('canvases', 'text_sources'):
                if field in output:
                    output[field] = [reference(key) for key in output[field]]
            owner_roots(output.get('list_state', {}), 'output/' + output_id)
        for canvas_id, canvas in data.get('canvases', {}).items():
            canvas['members'] = [reference(key) for key in canvas['members']]
            for layer in (canvas.get('image') or {}).get('layers', []):
                layer['id'] = mapped('layers/'+canvas_id, layer['id'])
        for binding in data.get('bindings', []):
            for field in ('clip', 'output'):
                if field in binding:
                    binding[field] = reference(binding[field])
            # workflow, node, field and server belong to the external target.
        lines = [*data.get('connections', []), *data.get('workflow_order', {}).get('legacy_control_connections', [])]
        for line in lines:
            line['id'] = mapped('connections', line['id'], 'line_')
            line['source'] = reference(line['source'])
            line['destination'] = reference(line['destination'])
        if 'current_output' in data:
            data['current_output'] = reference(data['current_output'])
        for scheduler in data.get('schedulers', {}).values():
            if 'policies' in scheduler:
                scheduler['policies'] = {reference(key): value for key, value in scheduler['policies'].items()}
        for image in functions.get('images', {}).values():
            if image.get('stage_reference'):
                image['stage_reference'] = reference(image['stage_reference'])
        for key, reader in functions.get('text_sources', {}).items():
            reader['root'] = tree(reader['root'], 'reader/' + key)
        # supply_origin is a PCS Reader reference, source_* remains provenance.
        from .policy import material_roots
        for root in material_roots({**state, 'items': []}):
            for part in composition.walk(root):
                if part.get('supply_origin'):
                    part['supply_origin'] = reference(part['supply_origin'])
        for parent, names in ((data, ('canvases', 'outputs', 'clip_inputs', 'image_inputs', 'schedulers', 'stages')),
                              (functions, ('images', 'text_sources', 'previews'))):
            for name in names:
                if name in parent:
                    parent[name] = {owned[key]: value for key, value in parent[name].items()}
    validate(candidate)
    return Duplicate(candidate, maps)


class Registry:
    """Only identity arbitration; no Qt contexts or task ownership in P1."""
    def __init__(self):
        self.by_id = {}
        self.by_file = {}

    def register(self, receipt):
        physical = (receipt.device, receipt.inode)
        owner = self.by_id.get(receipt.document_id)
        if physical in self.by_file:
            return 'same-file' if self.by_file[physical].document_id == receipt.document_id else 'changed-identity'
        if owner is not None:
            return 'duplicate-id'
        self.by_id[receipt.document_id] = receipt
        self.by_file[physical] = receipt
        return 'registered'
