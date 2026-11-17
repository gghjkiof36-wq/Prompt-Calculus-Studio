"""Compare native execution inputs without confusing JSON numeric spelling with edits."""
import math

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


def _execution_value_matches(left, right):
    # Compare directly, never via float(int): a rounded large seed is a change.
    # bool is deliberately excluded, and even identical infinities are invalid.
    if type(left) in (int, float) and type(right) in (int, float):
        return ((type(left) is int or math.isfinite(left)) and
                (type(right) is int or math.isfinite(right)) and left == right)
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return (left.keys() == right.keys() and all(type(k) is str for k in right) and
                all(type(k) is str and _execution_value_matches(v, right[k]) for k, v in left.items()))
    if type(left) is list:
        return len(left) == len(right) and all(_execution_value_matches(a, b) for a, b in zip(left, right))
    return (left is None or type(left) in (str, bool)) and left == right


def graph_matches(expected, actual, schema=None):
    """Match submitted JSON to ComfyUI queue/history, not to a new submission.

    Finite numbers compare by exact value; every other JSON type and structure
    stays exact. Old receipts need no input schema, including custom nodes.
    The schema argument remains compatible with existing callers/stored data.
    O(n) comparison, O(depth) stack; no normalization copy or digest changes.
    """
    return (type(expected) is dict and type(actual) is dict and
            all(type(node) is dict for node in expected.values()) and
            _execution_value_matches(expected, actual))
