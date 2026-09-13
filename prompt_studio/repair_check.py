"""Native Alpha regression checks on a new, disposable database."""
import json,time,traceback
from PySide6.QtCore import QTimer,Qt,QPoint,QPointF,QObject,QEvent,QMimeData
from PySide6.QtGui import QImage,QPainter,QColor,QPen,QWheelEvent,QDragEnterEvent,QDragMoveEvent,QDropEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from .widgets import RoundMenu
from .error_dialog import ImportErrorToast


class PaintCounter(QObject):
    def __init__(self,widget): super().__init__(widget); self.count=0; widget.installEventFilter(self)
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Paint: self.count+=1
        return False


class RepairCheck:
    def __init__(self,window):
        self.w=window; self.data=window.store.directory; self.result={}; self.material_index=0
        self.watchdog=QTimer(window); self.watchdog.setSingleShot(True); self.watchdog.timeout.connect(lambda:self.finish('Diagnostic timeout')); self.watchdog.start(20000)
    def later(self,fn,delay=180):
        def call():
            try: fn()
            except Exception: self.finish(traceback.format_exc())
        QTimer.singleShot(delay,call)
    def start(self):
        w=self.w; w.state['settings'].update(online=False,material='solid'); w.apply_theme(); w.set_interface_mode('canvas')
        w.canvas.add_tag('ceramic vase, afternoon light')
        self.path=self.data/'detail-fixture.png'; image=QImage(832,1216,QImage.Format.Format_RGB32); image.fill(QColor('#e5e0d5'))
        painter=QPainter(image)
        for y in range(0,1216,8): painter.fillRect(0,y,832,4,QColor('#bdb7ab'))
        painter.setBrush(QColor('#35637d')); painter.setPen(QPen(QColor('#173e52'),3)); painter.drawEllipse(196,320,440,640)
        painter.setPen(QColor('white')); font=painter.font(); font.setPixelSize(32); painter.setFont(font); painter.drawText(260,570,'832 × 1216'); painter.end(); image.save(str(self.path))
        self.key=w.canvas.functions.add_image(path=self.path,attached=True); self.card=w.canvas.functions.cards[self.key]; self.source=self.card.panel.preview
        w.canvas.fit(); self.later(self.preview)
    def preview(self):
        assert self.source.image.width()==832 and self.source.image.height()==1216
        key=self.source.image.cacheKey(); started=time.perf_counter()
        for _ in range(100): self.card.panel.refresh()
        self.result['refresh_100_ms']=round((time.perf_counter()-started)*1000,2); assert self.source.image.cacheKey()==key
        started=time.perf_counter()
        for i in range(30):
            self.card.requested_size=[350+i*5,430+i*5]; self.card.layout_card(); QApplication.processEvents()
        self.result['resize_30_ms']=round((time.perf_counter()-started)*1000,2)
        self.result['source_pixel_bytes']=self.source.image.sizeInBytes(); self.result['source_cached_across_resize']=self.source.image.cacheKey()==key
        self.w.canvas.fit(); self.later(self.begin_idle)
    def begin_idle(self):
        self.w.grab().save(str(self.data/'source-preview.png'))
        self.counter=PaintCounter(self.source); self.cpu=time.process_time(); self.later(self.after_idle,600)
    def after_idle(self):
        self.result['idle_600ms_source_paints']=self.counter.count; self.result['idle_600ms_cpu_ms']=round((time.process_time()-self.cpu)*1000,2)
        assert self.counter.count<=2
        self.w.settings('appearance'); self.later(self.material)
    def material(self):
        material=('solid','mica','acrylic')[self.material_index]
        self.w.settings_page.preferences.material.setCurrentIndex(self.w.settings_page.preferences.material.findData(material))
        self.later(self.material_capture)
    def material_capture(self):
        material=('solid','mica','acrylic')[self.material_index]
        self.result['material_'+material]=bool(self.w.native_material)
        assert self.w.settings_page.preferences.material.width()<=960
        page=self.w.settings_page; reading=page.reading
        assert reading.mapTo(page,reading.rect().bottomRight()).y()==page.height()-1
        self.w.grab().save(str(self.data/('settings-'+material+'.png')))
        self.material_index+=1
        if self.material_index<3: self.later(self.material)
        else: self.later(self.import_error)
    def import_error(self):
        self.w.settings('workflows'); invalid=self.data/'invalid.json'; invalid.write_text('{invalid',encoding='utf-8')
        self.w.generation_panel.import_workflow(invalid); self.error=self.w.findChild(ImportErrorToast)
        assert self.error is not None; self.later(self.close_error)
    def close_error(self):
        assert self.error.isVisible() and self.w.settings_page.workflows.feedback.isHidden()
        assert self.error.geometry().bottom()==self.w.height()-25; assert QApplication.activeModalWidget() is None
        self.w.grab().save(str(self.data/'import-error.png'))
        self.later(self.menu,2200)
    def menu(self):
        self.result['automatic_import_notice']=self.w.findChild(ImportErrorToast) is None
        assert self.result['automatic_import_notice']; self.w.settings_page.cancel()
        self.root=RoundMenu(self.w); self.root.addAction('加入 Tag 與素材'); self.sub=self.w.canvas.functions.menu(self.root,QPoint(0,0)); self.action=self.root.addMenu(self.sub); self.root.addAction('新增模組')
        self.root.open_at(self.w.mapToGlobal(QPoint(80,100))); self.later(self.enter_sub,100)
    def enter_sub(self):
        self.root.setActiveAction(self.action); QTest.keyClick(self.root,Qt.Key.Key_Right); self.later(self.hide_sub,350)
    def hide_sub(self):
        assert self.sub.isVisible()
        QTest.keyClick(self.sub,Qt.Key.Key_Left); self.later(self.reopen_sub,350)
    def reopen_sub(self):
        assert self.action in self.root.actions(); self.root.setActiveAction(self.action); QTest.keyClick(self.root,Qt.Key.Key_Right); self.later(self.done,350)
    def done(self):
        assert self.sub.isVisible() and len(self.sub.actions())==3; self.result['submenu_reentry']=True
        self.root.close(); self.later(self.gestures)
    def gestures(self):
        from . import composition as comp
        from .core import build_prompt
        w=self.w; c=w.canvas; tree=w.selected; view=c.view
        c.commit(lambda state:[c.add_root(state,comp.node(f'fixture {i}',f'fixture {i}')) for i in range(12)])
        w.builder_fold.set_expanded(True,animated=False); tree.setFixedHeight(180)
        view.resetTransform(); view.scale(.8,.8); QApplication.processEvents(); view.centerOn(c.output); QApplication.processEvents()
        def map_point(point):
            return view.mapFromScene(c.output.proxy.mapToScene(QPointF(tree.viewport().mapTo(w.builder_panel,point))))
        bar=tree.verticalScrollBar(); assert bar.maximum()>0
        point=map_point(tree.viewport().rect().center()); scale=view.transform().m11()
        for start,delta in ((0,-120),(bar.maximum(),-120),(0,120)):
            bar.setValue(start)
            event=QWheelEvent(QPointF(point),QPointF(view.viewport().mapToGlobal(point)),QPoint(),QPoint(0,delta),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
            QApplication.sendEvent(view.viewport(),event); assert view.transform().m11()==scale
            assert bar.value()>0 if delta<0 and start==0 else bar.value()==start
        self.result['canvas_list_wheel']=True
        before=build_prompt(w.state); source=tree.topLevelItem(2); target=tree.topLevelItem(0)
        tree.setCurrentItem(source); tree._drag_source=tree.key(source); tree._drag_token=b'native-diagnostic'
        rect=tree.visualItemRect(source); press=rect.center(); QTest.mousePress(tree.viewport(),Qt.MouseButton.LeftButton,pos=press)
        for zoom in (.5,1.,1.65):
            view.resetTransform(); view.scale(zoom,zoom)
            pixmap,hotspot=tree.drag_preview(); offset=press-rect.topLeft()
            assert hotspot==QPoint(round(offset.x()*zoom),round(offset.y()*zoom))
            assert abs(pixmap.deviceIndependentSize().width()-rect.width()*zoom)<=1
        QTest.mouseRelease(tree.viewport(),Qt.MouseButton.LeftButton,pos=press); self.result['scaled_drag_grab_point']=True
        view.resetTransform(); view.scale(.8,.8); view.centerOn(c.output)
        mime=QMimeData(); mime.setData('application/x-prompt-studio-order',tree._drag_token)
        point=map_point(tree.visualItemRect(target).topLeft()+QPoint(50,4)); assert view.viewport().rect().contains(point)
        for event in (QDragEnterEvent(point,Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier),QDragMoveEvent(point,Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier),QDropEvent(QPointF(point),Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)):
            QApplication.sendEvent(view.viewport(),event)
        tree._drag_source=None; tree._drag_token=b''; tree._scroll_timer.stop()
        after=build_prompt(w.state); assert after!=before and after==w.final.toPlainText()
        c.undo(); assert build_prompt(w.state)==before
        c.redo(); assert build_prompt(w.state)==after
        assert w.persist() and build_prompt(w.store.load())==after
        self.result['canvas_proxy_reorder_undo_persist']=True; self.later(self.extract)
    def extract(self):
        from .core import build_prompt
        c=self.w.canvas; ids=list(self.w.state['uses']); self.extract_parent=ids[0]
        c.nest(ids[1],ids[0]); c.move_cards({ids[0]:[-600,0]})
        self.extract_before=build_prompt(self.w.state)
        key=next(k for k in c.cards if k.startswith(ids[0]+':')); card=c.cards[key]
        self.extract_prompt=card.value['prompt']; view=c.view; view.resetTransform(); view.scale(.6,.6)
        destination=c.cards[ids[0]].sceneBoundingRect().topLeft()-QPointF(220,160)
        view.centerOn((card.scenePos()+destination)/2); QApplication.processEvents()
        start=view.mapFromScene(card.mapToScene(QPointF(80,18))); finish=view.mapFromScene(destination)
        self.extract_count=len(self.w.state['uses'])
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=start)
        QTest.mouseMove(view.viewport(),finish,30); QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=finish)
        self.later(self.extracted)
    def extracted(self):
        from .core import build_prompt
        c=self.w.canvas; state=self.w.state
        assert len(state['uses'])==self.extract_count+1
        assert not state['uses'][self.extract_parent]['children']
        assert any(root['prompt']==self.extract_prompt for root in state['uses'].values())
        after=build_prompt(state); assert after==self.w.final.toPlainText()
        c.undo(); assert build_prompt(self.w.state)==self.extract_before
        c.redo(); assert build_prompt(self.w.state)==after
        assert self.w.persist() and build_prompt(self.w.store.load())==after
        self.result['nested_module_drag_out']=True; self.finish()
    def finish(self,error=None):
        self.watchdog.stop(); self.result.update(ok=not error)
        if error: self.result['error']=error
        (self.data/'repair-result.json').write_text(json.dumps(self.result,ensure_ascii=False,indent=2),encoding='utf-8'); self.w.close()


def start(window):
    window.repair_check=RepairCheck(window); window.repair_check.later(window.repair_check.start,400)
