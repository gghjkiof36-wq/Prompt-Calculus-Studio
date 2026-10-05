"""Freeze the loaded build identity; disk replacement is not a loaded update."""
import json
from pathlib import Path


def read_identity():
    root = Path(__file__).resolve().parent
    try:
        value = json.loads((root / 'BUILD_INFO.json').read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('Invalid build identity')
        return dict(root=str(root), **{key: value[key] for key in ('version', 'release', 'git_head') if isinstance(value.get(key), str)})
    except (OSError, ValueError, TypeError):
        return dict(root=str(root), version='開發版')


LOADED_BUILD = read_identity()
