"""Visual exclusion state from the same per-canvas rules as Prompt compilation."""
from . import composition as comp
from .exclusions import filter_tags, tag_key


def affected_nodes(state):
    from .core import output_groups
    from .multi_output import canvas_projection
    result = {}
    for canvas_id in state.get('multi_output', {}).get('canvases', {}):
        projection = canvas_projection(state, canvas_id)
        items = {v['id']: v for v in projection['items']}
        roots = []
        for ident in output_groups(projection):
            if ident in projection.get('uses', {}):
                roots.append((ident, projection['uses'][ident]))
            else:
                roots.extend((key, comp.root_for(projection, items[key]))
                             for key in projection['selections'].get(ident, []) if key in items)
        rules = {}
        for _, root in roots:
            for part in comp.effective_nodes(root):
                for tag in part['excludes']:
                    rules.setdefault(tag_key(tag), []).append(part['name'])
        if not rules:
            continue
        for ident, root in roots:
            if root.get('opaque_source'):
                continue
            for part in comp.walk(root):
                removed = []
                def filtered(text):
                    clean, hits = filter_tags(text, rules)
                    removed.extend(hits)
                    return clean
                text = comp.render(part, filtered)
                if removed:
                    key = ident if part is root else ident + ':' + part['id']
                    result[key] = dict(all=not bool(text.strip()), tags=list(dict.fromkeys(removed)),
                        by=list(dict.fromkeys(name for tag in removed for name in rules.get(tag_key(tag), []))))
    return result
