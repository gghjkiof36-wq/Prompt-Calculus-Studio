"""Fresh-process Qt route timings. Profiling is separate from formal timings."""
import argparse
import cProfile
import functools
import json
import os
from pathlib import Path
import pstats
import subprocess
import sys
import time
from collections import defaultdict

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--first', choices=('gallery', 'explore', 'export'), required=True)
parser.add_argument('--profile', action='store_true')
parser.add_argument('--images', type=int, default=0,
                    help='Seed a synthetic media catalog outside timed work, before the first click.')
parser.add_argument('--no-thumbnails', action='store_true', help='Measure fallback decoding of original images.')
args = parser.parse_args()
source, out = args.source.resolve(), args.out.resolve()
assert not out.exists(), out
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['PROMPT_STUDIO_DATA'] = str(out / 'data')
sys.path[:0] = [str(source), str(source / 'tools')]
subprocess.run([sys.executable, str(source / 'tools/local_workspace_p2_fixture.py'), '--out', str(out)], check=True, stdout=subprocess.DEVNULL)
root = out / '創作資料（合成）'
import_start = time.perf_counter()
from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QFont, QFontDatabase, QImage
from PySide6.QtWidgets import QApplication, QWidget
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.media import Catalog
from prompt_calculus_studio.pages import GalleryPage
from prompt_calculus_studio.settings_page import SettingsPage
from prompt_calculus_studio.export_page import ExportPage
from prompt_calculus_studio.recent import RecentPage
from prompt_calculus_studio.deferred_page import DeferredToolPage
import_ms = (time.perf_counter() - import_start) * 1000

spans = []
stack = []
def wrap(cls, name):
    original = getattr(cls, name)
    @functools.wraps(original)
    def traced(*a, **kw):
        start = time.perf_counter()
        entry = [f'{cls.__name__}.{name}', start, 0.0]
        stack.append(entry)
        try:
            return original(*a, **kw)
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            stack.pop()
            if stack:
                stack[-1][2] += elapsed
            spans.append(dict(name=entry[0], inclusive_ms=elapsed, exclusive_ms=elapsed-entry[2]))
    setattr(cls, name, traced)

if args.profile:
    for cls, names in (
        (Storage, ('__init__', 'load_current', 'save')),
        (Catalog, ('__init__', 'rows', 'count')),
        (WorkspaceShell, ('ensure_utility', 'style_shell', 'sync_sidebar')),
        (Window, ('__init__', 'make_prompt_page', 'make_canvas_surface', 'apply_theme', 'show_page')),
        (DeferredToolPage, ('load',)),
        (GalleryPage, ('__init__', 'refresh_albums', 'refresh', 'adapt_panels')),
        (SettingsPage, ('__init__', 'make_explore_page', 'make_data_page', 'show_section')),
        (ExportPage, ('__init__',)), (RecentPage, ('__init__',)),
    ):
        for name in names:
            if hasattr(cls, name): wrap(cls, name)

app = QApplication([])
font_start = time.perf_counter()
for filename in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
    assert QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+filename) >= 0
app.setFont(QFont('Microsoft JhengHei UI', 12))
metrics = dict(import_ms=import_ms, source=str(source), scale=os.environ.get('QT_SCALE_FACTOR'), profile=args.profile)
metrics['font_ms'] = (time.perf_counter()-font_start)*1000
errors = []
sys.excepthook = lambda kind, value, trace: errors.append(f'{kind.__name__}: {value}')
def drain():
    # Include queued zero-delay layouts and paint. No fixed sleep is counted.
    for _ in range(20): app.processEvents()

def measure(name, action):
    spans.clear()
    profiler = cProfile.Profile()
    if args.profile: profiler.enable()
    start = time.perf_counter()
    action()
    sync_ms = (time.perf_counter()-start)*1000
    drain()
    elapsed = (time.perf_counter()-start)*1000
    if args.profile:
        profiler.disable()
        with (out/(name+'-profile.txt')).open('w', encoding='utf-8') as f:
            pstats.Stats(profiler, stream=f).strip_dirs().sort_stats('cumulative').print_stats(90)
        aggregate = defaultdict(lambda: dict(calls=0, inclusive_ms=0.0, exclusive_ms=0.0))
        for span in spans:
            dest = aggregate[span['name']]
            dest['calls'] += 1
            for key in ('inclusive_ms', 'exclusive_ms'): dest[key] += span[key]
        (out/(name+'-spans.json')).write_text(json.dumps(aggregate, indent=2), encoding='utf-8')
    metrics[name] = dict(sync_ms=sync_ms, queued_layout_paint_ms=elapsed-sync_ms, total_ms=elapsed)
    print(name, json.dumps(metrics[name]), flush=True)

def start():
    global w
    w = WorkspaceShell(root, out/'data')
    w.report_error = errors.append
    w.autosave.delay_ms = 60000
    w.resize(1440, 900)
    w.show()
    w.open_path(root/'街景.pcs-workflow')
measure('startup', start)
assert w.utility is None, 'No preloading into startup'
if args.images:
    # An existing catalog has its own workload. Preparing fixture bytes is not
    # a product operation and is excluded from both revisions' measurements.
    directory = w.session/'app-services'
    store = Storage(directory)
    catalog = Catalog(store)
    catalog.put('album', dict(id='synthetic', name='合成媒體測試'))
    originals = out/'synthetic-originals'
    originals.mkdir()
    thumbnails = directory/'thumbnails'
    thumbnails.mkdir(exist_ok=True)
    image = QImage(1280, 720, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.darkGreen)
    with catalog.db:
        for i in range(args.images):
            original=originals/f'image-{i:05}.png'
            thumbnail=thumbnails/f'image-{i:05}.png'
            image.save(str(original))
            if not args.no_thumbnails:image.scaled(320, 180).save(str(thumbnail))
            row = dict(id=f'image-{i:05}', name=f'Synthetic {i:05}.png',
                       path=str(original), thumb='' if args.no_thumbnails else thumbnail.relative_to(directory).as_posix(), owned=False,
                       metadata={'raw': {}}, manual=None)
            catalog.db.execute('INSERT INTO resources VALUES (?,?,?,?,?)',
                               (row['id'], 'image', 'synthetic', row['name'], json.dumps(row)))
    store.close()
metrics['catalog_images'] = args.images
metrics['usable_thumbnails'] = args.images > 0 and not args.no_thumbnails
for route in [args.first, *(r for r in ('gallery', 'explore', 'export') if r != args.first)]:
    measure(route, lambda r=route: w.open_route(r))
    assert w.active_route == route
    metrics[route]['widgets'] = len(w.utility.findChildren(QWidget))
    w.grab().save(str(out/(route+'.png')))
    measure(route+'-home', lambda: w.open_route('home'))
for i in range(3):
    for route in ('gallery', 'explore', 'export'):
        measure(f'warm-{route}-{i}', lambda r=route: w.open_route(r))
        w.open_route('home'); drain()
metrics['errors'] = errors
assert not errors, errors
(out/'timing.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
for pane in list(w.panes):
    while pane in w.panes and pane.strip.bar.count():
        w.close_tab(0, decision='discard', make_home=False, pane=pane)
w.close(); drain()
