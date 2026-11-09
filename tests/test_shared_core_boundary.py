"""The ComfyUI copy must be closed over package imports and Qt-free at any depth."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

from prompt_calculus_studio.releases import SHARED_MODULES

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'prompt_calculus_studio'


def qt_imports(source):
    """Return every forbidden import, including imports inside callable bodies."""
    found = []
    for node in ast.walk(ast.parse(source)):
        names = ([item.name for item in node.names] if isinstance(node, ast.Import)
                 else [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
        for name in names:
            root = name.split('.')[0]
            if root in ('PySide6', 'shiboken6') or root.startswith('PyQt'):
                found.append((node.lineno, name))
    return sorted(found)


def package_imports(source, module):
    """Resolve relative and absolute imports to names in the shared manifest."""
    found = []
    package = (PACKAGE + '.' + module).rpartition('.')[0]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ''
            if node.level:
                name = importlib.util.resolve_name('.' * node.level + name, package)
            # `from . import core` imports a sibling; `from .core import f`
            # depends on core, with f resolved by that module.
            names = ([name + '.' + item.name for item in node.names]
                     if name == PACKAGE else [name])
        else:
            continue
        found.extend((node.lineno, name[len(PACKAGE) + 1:])
                     for name in names if name.startswith(PACKAGE + '.'))
    return sorted(found)


def inspect_shared_sources(sources, shared_modules):
    """Also usable on git-show sources to prove the lock rejects an old tree."""
    qt, outside = [], []
    allowed = set(shared_modules)
    for module in shared_modules:
        source = sources[module]
        qt.extend(dict(module=module, line=line, dependency=name)
                  for line, name in qt_imports(source))
        outside.extend(dict(module=module, line=line, dependency=name)
                       for line, name in package_imports(source, module) if name not in allowed)
    return dict(qt_imports=qt, outside_shared=outside)


NO_QT_PROBE = r'''
import copy, hashlib, importlib, json, struct, sys, tempfile, zlib
from pathlib import Path

class NoQt:
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PySide6', 'shiboken6', 'PyQt')):
            raise ImportError('Qt blocked: ' + fullname)

sys.meta_path.insert(0, NoQt())
sys.path.insert(0, sys.argv[1])
from prompt_calculus_studio.releases import SHARED_MODULES
for module in SHARED_MODULES:
    importlib.import_module('prompt_calculus_studio.' + module)
from prompt_calculus_studio import composition_image as ci

def chunk(kind, data):
    return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    raw = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 2, 1, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0\0\0\xff')) + chunk(b'IEND', b''))
    source = dict(name='source.png', sha256=hashlib.sha256(raw).hexdigest(),
                  width=2, height=1, relative='originals/generation/source.png')
    path = root / source['relative']
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    doc = ci.document(8, 8)
    shape = ci.layer('stroke', width=2, height=2, points=[[0, 0], [2, 2]])
    ci.resize_layer(shape, 4, 6)
    assert shape['points'] == [[0, 0], [4, 6]]
    doc['layers'].append(shape)
    assert ci.validate_image(doc) is doc
    state = dict(multi_output=dict(canvases={'canvas': dict(image=doc)}, connections=[
        dict(kind='image', source='photo', destination='canvas')]),
        canvas_functions=dict(images={'photo': dict(source=source)}))
    original = copy.deepcopy(state)
    combined = ci.canvas_document(state, 'canvas')
    assert state == original
    assert ci.validate_image(combined) is combined
    assert len(combined['layers']) == 2
    base = combined['layers'][0]
    assert (base['x'], base['y'], base['width'], base['height']) == (0, 2, 8, 4)
    assert base['source'] == source and base['source'] is not source
    assert ci.source_path(root, source, verify=True) == path.resolve()
    state['multi_output']['canvases']['canvas']['image'] = None
    inferred = ci.canvas_document(state, 'canvas')
    assert (inferred['width'], inferred['height']) == (2, 1)
    assert ci.validate_image(inferred) is inferred

removed = ['PreviewCache', 'render_image', 'freeze_image_source', 'freeze_source']
assert all(not hasattr(ci, name) for name in removed)
loaded_qt = sorted(name for name in sys.modules if name.startswith(('PySide6', 'shiboken6', 'PyQt')))
assert not loaded_qt, loaded_qt
assert 'prompt_calculus_studio.composition_render' not in sys.modules
print(json.dumps(dict(shared_modules=len(SHARED_MODULES), qt_modules=loaded_qt,
                     calls=['validate_image', 'document', 'layer', 'resize_layer', 'canvas_document', 'source_path'],
                     absent_from_shared=removed)))
'''


class SharedCoreBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sources = {name: (ROOT / PACKAGE / (name + '.py')).read_text(encoding='utf-8')
                   for name in SHARED_MODULES}
        cls.scan = inspect_shared_sources(sources, SHARED_MODULES)

    def test_no_qt_import_at_any_depth(self):
        self.assertEqual(self.scan['qt_imports'], [])

    def test_shared_imports_are_closed(self):
        self.assertNotIn('composition_render', SHARED_MODULES)
        self.assertIn('composition_image', SHARED_MODULES)
        self.assertEqual(self.scan['outside_shared'], [])

    def test_shared_calls_without_qt(self):
        run = subprocess.run([sys.executable, '-I', '-c', NO_QT_PROBE, str(ROOT)],
                             cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result['shared_modules'], len(SHARED_MODULES))
        self.assertEqual(result['qt_modules'], [])
        self.assertIn('render_image', result['absent_from_shared'])

    def test_nested_qt_import_is_rejected(self):
        source = 'def render():\n    from PySide6.QtGui import QImage\n'
        self.assertEqual(qt_imports(source), [(2, 'PySide6.QtGui')])
        scan = inspect_shared_sources({'synthetic': source}, ['synthetic'])
        self.assertEqual(scan['qt_imports'], [dict(module='synthetic', line=2, dependency='PySide6.QtGui')])

    def test_qt_import_spellings_are_rejected(self):
        for name in ('PySide6', 'shiboken6', 'PyQt5', 'PyQt6.QtCore'):
            with self.subTest(name=name):
                self.assertEqual(qt_imports('def f():\n    import ' + name + ' as qt\n'), [(2, name)])
                self.assertEqual(qt_imports('class C:\n    from ' + name + ' import value\n'), [(2, name)])

    def test_closure_catches_nested_relative_and_absolute_imports(self):
        source = textwrap.dedent('''\
            def f():
                from .composition_render import render_image
                from . import composition_render as renderer
                import prompt_calculus_studio.composition_render as renderer
                from prompt_calculus_studio.composition_render import PreviewCache
                from prompt_calculus_studio import composition_render
                from .core import initial_state
        ''')
        scan = inspect_shared_sources({'synthetic': source, 'core': ''}, ['synthetic', 'core'])
        self.assertEqual(scan['outside_shared'], [dict(module='synthetic', line=line, dependency='composition_render')
                                                 for line in range(2, 7)])


if __name__ == '__main__':
    unittest.main()
