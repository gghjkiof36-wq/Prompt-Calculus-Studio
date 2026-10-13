"""Display-only slicing of generated images; never edits execution collections."""


def iteration_key(item):
    reference=item.get('reference',{})
    if not isinstance(reference,dict):return None
    if reference.get('attempt'):
        if 'round' in reference and 'parent' in reference:
            return ('iteration',reference.get('run'),reference.get('stage'),reference['round'],reference['parent'])
        # Older results lack iteration provenance. One native attempt includes
        # its whole output batch, so retain every image from that submission.
        return ('attempt',reference.get('run'),reference.get('stage'),reference['attempt'])
    if reference.get('prompt_id'):
        return ('submission',reference.get('workflow'),reference['prompt_id'])
    return None


def current_iteration(items):
    """Preserve same-iteration batches and their order without loading pixels."""
    if not items:return []
    latest=iteration_key(items[-1])
    if latest is None:return list(items)
    return [item for item in items if iteration_key(item)==latest]


def preview_images(state,results,key):
    """Resolve display wiring without materializing text or decoding metadata."""
    from .flow_data import image_list
    projected=dict(state,_stage_results=results)
    images=state.get('canvas_functions',{}).get('images',{})
    if key in images:
        item=dict(images[key]);item.pop('input_index',None)
        projected['canvas_functions']=dict(state['canvas_functions'],images=dict(images,**{key:item}))
    try:return current_iteration(image_list(projected,key))
    except (ValueError,KeyError):return []
