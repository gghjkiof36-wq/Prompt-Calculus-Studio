"""Project editable native state without opening views or execution owners."""
import copy

from .local_documents import assets, category, schema, workflow


def project_document(document, runtime_document, state, opening_settings, *, identity_maps=None, remap=True):
    kind = document['kind']
    if kind == 'category':
        fresh = category.from_state(state, runtime_document['payload']['category']['id'])
        settings = fresh['payload']['settings']
        original = runtime_document['payload']['settings']
    else:
        fresh = workflow.from_state(state)
        settings = fresh['payload']['state']['settings']
        original = runtime_document['payload']['state']['settings']
    for key in list(settings):
        if key not in original and settings[key] == (opening_settings or state['settings']).get(key):
            settings.pop(key)
    result = copy.deepcopy(document)
    result['payload'] = fresh['payload']
    result['required_features'] = fresh['required_features']
    referenced = {ref for ref, _, _ in assets.references(result)}
    result['assets'] = [entry for entry in result['assets'] if entry['reference'] in referenced]
    if remap and identity_maps is not None:
        from .identity_projection import project_copy
        result = project_copy(result, identity_maps)
    schema.validate(result)
    return result
