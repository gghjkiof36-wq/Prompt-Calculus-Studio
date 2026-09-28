"""Compare native execution inputs without confusing JSON numeric spelling with edits."""
import math

# Old receipts predate input schemas. Only audited standard FLOAT fields receive
# numeric compatibility in those receipts; seeds/links/text never use float casts.
FLOAT_FIELDS = {
    'KSampler': ('cfg', 'denoise'), 'KSamplerAdvanced': ('cfg',),
    'LoraLoader': ('strength_model', 'strength_clip'),
    'LoraLoaderModelOnly': ('strength_model',), 'ImageScaleBy': ('scale_by',),
}


def input_schema(graph, classes):
    result = {}
    for key, node in graph.items():
        cls = classes.get(node.get('class_type'))
        if cls is None:
            continue
        try:
            fields = cls.INPUT_TYPES()
            result[key] = {name: spec[0] for group in ('required', 'optional')
                           for name, spec in fields.get(group, {}).items()
                           if isinstance(spec, (tuple, list)) and spec and
                           spec[0] in ('INT', 'FLOAT', 'STRING', 'BOOLEAN')}
        except (TypeError, ValueError, AttributeError):
            continue
    return result


def exact(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(exact(v, right[k]) for k, v in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(exact(a, b) for a, b in zip(left, right))
    return left == right


def graph_matches(expected, actual, schema=None):
    if not isinstance(expected, dict) or not isinstance(actual, dict) or expected.keys() != actual.keys():
        return False
    for key, node in expected.items():
        other = actual[key]
        if not isinstance(node, dict) or not isinstance(other, dict):
            return False
        if not exact({k: v for k, v in node.items() if k != 'inputs'},
                     {k: v for k, v in other.items() if k != 'inputs'}):
            return False
        inputs, values = node.get('inputs', {}), other.get('inputs', {})
        if not isinstance(values, dict) or inputs.keys() != values.keys():
            return False
        types = (schema or {}).get(key, {})
        floats = FLOAT_FIELDS.get(node.get('class_type'), ())
        for field, value in inputs.items():
            received = values[field]
            if exact(value, received):
                continue
            # The backend's declared FLOAT normalization is exact only when the
            # value is representable; bool == 1 and large rounded seeds are edits.
            numeric = type(value) in (int, float) and type(received) in (int, float)
            if (types.get(field) in ('FLOAT','INT') or not schema and field in floats) and numeric:
                if (type(value) is int or math.isfinite(value)) and (type(received) is int or math.isfinite(received)) and value == received:
                    continue
            return False
    return True
