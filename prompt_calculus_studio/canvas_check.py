"""Packaged offscreen smoke check, always using a fresh diagnostic database."""
import json
from pathlib import Path
from PySide6.QtCore import QTimer, QPoint
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QGraphicsView
from .composition import node
from .core import build_prompt
from .snapshots import make_snapshot, validate_snapshot


def start(window):
    window.state['settings']['online']=False
    # Offscreen Qt cannot discover the Windows font database automatically.
    if QApplication.platformName()=='offscreen':
        for name in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
            path=Path('C:/Windows/Fonts')/name
            if path.exists(): QFontDatabase.addApplicationFont(str(path))
        window.state['settings']['font_family']='Microsoft JhengHei'
        window.apply_theme()
    item=window.state['items'][0]
    window.state['selections']={item['module']:[item['id']]}
    window.resize(1440,900)
    window.enter_canvas()
    def check():
        eyes=node('眼睛','red eyes'); eyes['overlays']=[node('眼罩','blindfold')]
        root=node('測試角色','1girl',children=[eyes])
        window.canvas.commit(lambda state:window.canvas.add_root(state,root))
        window.canvas.add_tag('simple background')
        expected='(1girl, blindfold:1.0),\n\n(simple background:1.0)'
        snapshot=make_snapshot(window.state); validate_snapshot(snapshot)
        shared=window.canvas.output.proxy.widget() is window.builder_panel and window.selected.isVisible()
        window.final.setPlainText('smoke manual draft')
        clear_available=window.clear_draft.isEnabled() and window.clear_draft.isVisible()
        window.state['settings']['confirm_clear_draft']=False; window.regenerate()
        origin=window.canvas.view.viewport().mapTo(window.host_shell,QPoint())
        edge_to_edge=(origin.x()==0 and window.canvas.view.viewport().width()==window.host_shell.width()
                      and origin.y()+window.canvas.view.viewport().height()==window.host_shell.height())
        result=dict(ok=window.canvas.isVisible() and not window.module_panel.isVisible()
                    and build_prompt(window.state)==expected and shared and clear_available
                    and window.state['settings']['separate_selections']
                    and not snapshot['state']['selections']
                    and window.state['draft'] is None and edge_to_edge,
                    generated=build_prompt(window.state),schema=snapshot['schema_version'],
                    shared_controls=shared,clear_manual_draft=clear_available,
                    paragraphs=build_prompt(window.state)==expected,
                    isolated=window.state['settings']['separate_selections'],
                    edge_to_edge=edge_to_edge,header_height=window.canvas_header.height(),
                    full_repaint=window.canvas.view.viewportUpdateMode()==QGraphicsView.ViewportUpdateMode.FullViewportUpdate,
                    canvas_width=window.canvas.width(),window_width=window.width())
        window.canvas.fit(); QApplication.processEvents()
        window.grab().save(str(window.store.directory/'canvas-smoke.png'))
        (window.store.directory/'canvas-smoke.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        window.close()
    QTimer.singleShot(600,check)
