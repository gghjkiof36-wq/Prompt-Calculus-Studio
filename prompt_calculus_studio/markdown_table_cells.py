"""Qt-free GFM cell escaping shared by paste conversion and the table editor."""
import re
from .markdown_math import math_ranges


def transform_pipes(value, encode):
    # Inline code and TeX keep slashes literally after the table layer; ordinary
    # Markdown consumes another escape layer.
    code = [m.span(2) for m in re.finditer(r'(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)', value, re.S)
            if (len(value[:m.start()])-len(value[:m.start()].rstrip('\\')))%2 == 0]
    code.extend(math_ranges(value))
    def replace(match):
        count = len(match.group(1))
        literal = any(a <= match.start() < b for a, b in code)
        count = (count+1 if literal else 2*count+1) if encode else (max(0, count-1) if literal else max(0, (count-1)//2))
        return '\\'*count+'|'
    return re.sub(r'(\\*)\|', replace, value)


def decode_cell(raw):
    value = re.sub(r'<br\s*/?>', '\n', raw.strip(), flags=re.I)
    return transform_pipes(value, False)


def encode_cell(value):
    value = value.replace('\r\n', '\n').replace('\r', '\n').replace('\u2029', '\n')
    return transform_pipes(value, True).replace('\n', '<br>')
