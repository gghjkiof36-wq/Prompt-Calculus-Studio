"""Synthetic ComfyUI validation: FLOAT inputs become floats in queue/history.

Keep the submitted payload untouched, as with an HTTP JSON decode at the server.
This deliberately does not import the product's comparison or schema helpers.
"""
import copy


def normalized_prompt(graph):
    result = copy.deepcopy(graph)
    fields = {'KSampler': ('cfg', 'denoise'),
              'LoraLoader': ('strength_model', 'strength_clip'),
              'CustomFloat': ('scale',)}
    for node in result.values():
        inputs = node.get('inputs', {})
        for name in fields.get(node.get('class_type'), ()):
            if name in inputs and type(inputs[name]) in (int, float):
                inputs[name] = float(inputs[name])
    return result
