"""Fresh synthetic documents; timings include queued layout and offscreen paint."""
import argparse
import copy
import cProfile
import json
import os
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
import time

p = argparse.ArgumentParser()
p.add_argument('--source', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
p.add_argument('--profile', action='store_true')
p.add_argument('--large', action='store_true')
a = p.parse_args()
a.source, a.out = a.source.resolve(), a.out.resolve()
assert not a.out.exists()
sys.path[:0] = [str(a.source), str(a.source/'tools')]
os.environ.update(QT_QPA_PLATFORM='offscreen', PROMPT_STUDIO_DATA=str(a.out/'data'))
subprocess.run([sys.executable, str(a.source/'tools/local_workspace_p2_fixture.py'), '--out', str(a.out)], check=True, capture_output=True)
root = a.out/'創作資料（合成）'
if a.large:
    for i in range(30):
        folder = root/'筆記'/f'Folder {i}'
        folder.mkdir()
        for j in range(10): (folder/f'Note {j}.md').write_text('synthetic', encoding='utf-8')
    from prompt_calculus_studio.local_documents import io, workflow
    from prompt_calculus_studio.state_loading import prepare_state
    from prompt_calculus_studio.composition import node
    loaded = io.load(root, '街景.pcs-workflow')
    state = prepare_state(workflow.to_state(loaded.document), multi=True)
    template = next(iter(state['multi_output']['canvases'].values()))
    for i in range(24):
        canvas = copy.deepcopy(template)
        canvas.update(id=f'bench-canvas-{i}', name=f'Canvas {i}', members=[], position=[(i%6)*680, (i//6)*800])
        for j in range(4):
            ident = f'bench-text-{i}-{j}'
            state['uses'][ident] = node(f'Text {i}-{j}', 'synthetic prompt')
            state['text_positions'][ident] = [j%2*220, j//2*180]
            canvas['members'].append(ident)
        state['multi_output']['canvases'][canvas['id']] = canvas
    doc = workflow.from_state(state)
    result = io.create(root, 'Large.pcs-workflow', doc)
    assert result.status == 'saved', result

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFont, QFontDatabase, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.workspace_locations import path_key
app = QApplication([])
assert app.platformName() == 'offscreen'
for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
app.setFont(QFont('Microsoft JhengHei UI', 12))
errors, timing = [], {}
sys.excepthook = lambda kind, value, trace: errors.append(str(value))
w = WorkspaceShell(root, a.out/'data')
w.report_error = errors.append
w.autosave.delay_ms = 60000
w.resize(1440, 900)
w.show()

def drain():
    for _ in range(10): app.processEvents()

def measure(name, action, count=1):
    drain()
    values, prof = [], cProfile.Profile()
    if a.profile: prof.enable()
    for i in range(count):
        start = time.perf_counter()
        action(i)
        drain()
        values.append((time.perf_counter()-start)*1000)
    if a.profile:
        prof.disable()
        with (a.out/(name+'-profile.txt')).open('w', encoding='utf-8') as stream:
            pstats.Stats(prof, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(45)
    timing[name] = dict(samples_ms=values, median_ms=statistics.median(values), max_ms=max(values))
    print(name, round(statistics.median(values), 2), flush=True)

def close_all():
    for pane in list(w.panes):
        while pane in w.panes and pane.strip.bar.count():
            assert w.close_tab(0, decision='discard', make_home=False, pane=pane)
    w.new_tab()
    drain()

measure('manager-first', lambda _: w.open_path(root/'場景.pcs-category'))
close_all()
measure('manager-after-close-all', lambda _: w.open_path(root/'場景.pcs-category'))
measure('workflow-first', lambda _: w.open_path(root/('Large.pcs-workflow' if a.large else '街景.pcs-workflow'), new_tab=True))
canvas_tab = w.tabs_by_key[w.active_key]
canvas, view = canvas_tab.host.canvas, canvas_tab.host.canvas.view
measure('markdown-first', lambda _: w.open_path(root/'筆記/場景筆記.md', new_tab=True))
note = w.tabs_by_key[w.active_key]
measure('tab-switch', lambda i: w.focus_tab(note if i%2 else canvas_tab), 16)
w.focus_tab(canvas_tab)
item = w.path_items[path_key(root/'筆記')]
item.setExpanded(False)
def toggle(_):
    rect = w.tree.visualItemRect(item)
    QTest.mouseClick(w.tree.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(rect.left()+60, rect.center().y()))
measure('folder', toggle, 20)
measure('sidebar', lambda _: w.toggle_sidebar(), 8)

def mouse(kind, point, button, buttons):
    app.sendEvent(view.viewport(), QMouseEvent(kind, QPointF(point), QPointF(view.viewport().mapToGlobal(point)), button, buttons, Qt.KeyboardModifier.NoModifier))

def zoom(i):
    point = QPoint(view.viewport().width()-30, 60)
    delta = 30 if (i//8)%2 == 0 else -30
    app.sendEvent(view.viewport(), QWheelEvent(QPointF(point), QPointF(view.viewport().mapToGlobal(point)), QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))

def gestures(prefix):
    canvas.fit(); drain(); QTest.qWait(140)
    point = QPoint(180, 180)
    mouse(QEvent.Type.MouseButtonPress, point, Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton)
    measure(prefix+'pan', lambda i: mouse(QEvent.Type.MouseMove, point+QPoint((i%16)*3,(i%16)*2),Qt.MouseButton.NoButton,Qt.MouseButton.MiddleButton), 24)
    mouse(QEvent.Type.MouseButtonRelease, point, Qt.MouseButton.MiddleButton, Qt.MouseButton.NoButton)
    measure(prefix+'zoom', zoom, 32)

gestures('')
other = w.duplicate_view(canvas_tab, True)
drain()
gestures('split-')
measure('split-resize', lambda i: w.pane_split.moveSplitter(int(w.pane_split.width()*(.4+i%12*.015)),1), 24)
timing.update(errors=errors, large=a.large, platform=app.platformName(), dpr=w.devicePixelRatioF())
from prompt_calculus_studio.canvas_raster import LAYERS
timing['raster_cache'] = dict(entries=len(LAYERS.entries),bytes=LAYERS.byte_count)
(a.out/'timing.json').write_text(json.dumps(timing, indent=2), encoding='utf-8')
assert not errors, errors
close_all()
w.close(); drain()
