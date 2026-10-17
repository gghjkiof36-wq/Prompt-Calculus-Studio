"""One category owns complete material records in their manual array order."""
import copy

from .. import core
from .schema import fail, new_document, validate
from .policy import CONTENT_SETTINGS, content_settings


def wrapper(payload):
    return dict(version=payload['state_version'], modules=[copy.deepcopy(payload['category'])],
                items=copy.deepcopy(payload['items']), workspaces=[dict(id='__document_validation__',
                name='Document validation', fixed=[], picks={})], workspace='__document_validation__',
                selections={}, draft=None, draft_base='', temporary=[], dictionary={},
                settings=copy.deepcopy(payload['settings']))


def validate_payload(payload):
    if set(payload) != {'state_version', 'category', 'items', 'settings'}:
        fail('unsupported', '分類封裝含未支援欄位。')
    if not isinstance(payload['items'], list) or not isinstance(payload['category'], dict):
        fail('invalid', '分類或素材清單無效。')
    if set(payload['settings']) - CONTENT_SETTINGS:
        fail('unsupported', '分類文件包含非內容設定。')
    core.validate_state(wrapper(payload))


def from_state(state, category_id):
    core.validate_state(state)
    category = next((m for m in state['modules'] if m['id'] == category_id), None)
    if category is None:
        fail('missing', '分類不存在。')
    return new_document('category', dict(state_version=state['version'], category=category,
        items=[item for item in state['items'] if item['module'] == category_id],
        settings=content_settings(state['settings'])))


def to_state(document):
    validate(document)
    if document['kind'] != 'category':
        fail('invalid', '不是分類文件。')
    return core.validate_state(wrapper(document['payload']))
