"""Shared image browser with side navigation and reversible quick edits."""
from pathlib import Path
from sqlite3 import Error as DatabaseError
from PySide6.QtCore import Qt,QSize,QEvent,QEasingCurve
from PySide6.QtGui import QShortcut,QKeySequence
from PySide6.QtWidgets import (QWidget,QSizePolicy,QGraphicsOpacityEffect,QHBoxLayout,
                              QFileDialog,QSpinBox,QPushButton)
from .widgets import StudioDialog,button,row,label,ComboBox,RoundMenu,ask
from .ui_icons import icon
from .elapsed_animation import ElapsedAnimation
from .image_viewport import ImageViewport


def viewer_size(available):
    return QSize(max(1,min(available.width()-40,round(available.width()*.90))),
                 max(1,min(available.height()-40,round(available.height()*.90))))


class NavigationEdge(QWidget):
    """Only this small button fades; the image is never opacity-composited."""
    def __init__(self,parent,title,glyph,callback):
        super().__init__(parent)
        self.available=False
        self.button=button('',callback,'IconButton');self.button.setParent(self)
        self.button.setFixedSize(36,56);self.button.setIcon(icon(glyph));self.button.setProperty('iconName',glyph)
        self.button.setAccessibleName(title);self.button.setToolTip(title)
        self.button.setFocusPolicy(Qt.FocusPolicy.TabFocus);self.button.installEventFilter(self)
        self.opacity=QGraphicsOpacityEffect(self.button);self.opacity.setOpacity(0.)
        self.button.setGraphicsEffect(self.opacity)
        self.motion=ElapsedAnimation(self);self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.valueChanged.connect(self.opacity.setOpacity)

    def reveal(self):
        from .dialog_presentation import reduced_motion
        target=1. if self.available and (self.underMouse() or self.button.hasFocus()) else 0.
        if self.motion.state()==self.motion.State.Running and self.motion.endValue()==target:return
        self.motion.stop()
        if reduced_motion(self) or not self.isVisible():self.opacity.setOpacity(target);return
        if abs(self.opacity.opacity()-target)<.001:return
        self.motion.setDuration(160 if target else 120)
        self.motion.setStartValue(self.opacity.opacity());self.motion.setEndValue(target);self.motion.start()

    def set_available(self,available):
        self.available=available;self.button.setEnabled(available);self.button.setVisible(available);self.reveal()

    def enterEvent(self,event):super().enterEvent(event);self.reveal()
    def leaveEvent(self,event):super().leaveEvent(event);self.reveal()
    def resizeEvent(self,event):
        super().resizeEvent(event);self.button.move((self.width()-self.button.width())//2,(self.height()-self.button.height())//2)
    def hideEvent(self,event):
        self.motion.stop();self.opacity.setOpacity(0.);super().hideEvent(event)
    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.FocusIn,QEvent.Type.FocusOut):self.reveal()
        return super().eventFilter(watched,event)


class ImageViewerDialog(StudioDialog):
    def __init__(self,parent,path,name='',*,records=None,selected_id=None,on_select=None,on_save=None):
        super().__init__(parent)
        self.close_button=self.findChild(QPushButton,'DialogClose')
        self.records=[dict(id=r['id'],path=r['path'],name=r.get('name','')) for r in (records or [])]
        self.index=next((i for i,r in enumerate(self.records) if r['id']==selected_id),0)
        self.on_select,self.on_save=on_select,on_save
        self.editing=False;self._loading=False
        self.setWindowTitle(name or Path(path).name)
        self.heading.setMinimumWidth(0);self.heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        available=parent.screen().availableGeometry().size() if parent else self.screen().availableGeometry().size()
        self.resize(viewer_size(available));self.body.setSpacing(10)
        self.edit_button=button('編輯',self.toggle_edit,'Quiet');self.edit_button.setIcon(icon('pencil'))
        self.edit_button.setCheckable(True)
        self.rotate_button=button('90°',lambda:self.transform_image('right'),'Quiet')
        self.rotate_button.setIcon(icon('update'));self.rotate_button.setToolTip('向右旋轉 90°')
        self.rotate_button.setAccessibleName('向右旋轉90度')
        self.save_button=button('另存副本',self.save_copy,'Primary');self.save_button.hide()
        self.heading_row.takeAt(1)
        self.heading_row.insertWidget(0,self.edit_button);self.heading_row.insertWidget(1,self.rotate_button)
        self.heading_row.setStretch(2,1);self.heading_row.insertWidget(3,self.save_button)

        self.tools=QWidget();tools=QHBoxLayout(self.tools);tools.setContentsMargins(0,0,0,0);tools.setSpacing(8)
        self.crop_button=button('裁切',self.start_crop,'Quiet');self.crop_button.setCheckable(True)
        self.apply_crop=button('套用裁切',self.finish_crop,'Quiet');self.apply_crop.hide();self.apply_crop.setEnabled(False)
        self.rotation=button('旋轉',self.rotation_menu,'Quiet');self.flip=button('翻轉',self.flip_menu,'Quiet')
        self.mark_tool=ComboBox();self.mark_tool.setAccessibleName('標註工具')
        for title,value in (('移動','move'),('畫筆','pen'),('箭頭','arrow'),('框線','rectangle'),('文字','text')):self.mark_tool.addItem(title,value)
        self.mark_tool.currentIndexChanged.connect(self.change_tool)
        self.color_button=button('顏色',self.pick_color,'Quiet')
        self.stroke_width=QSpinBox();self.stroke_width.setRange(1,48);self.stroke_width.setValue(4);self.stroke_width.setFixedWidth(64)
        self.stroke_width.setAccessibleName('筆畫粗細');self.stroke_width.setToolTip('筆畫粗細')
        self.stroke_width.valueChanged.connect(lambda value:setattr(self.display,'stroke',value))
        self.text_size=QSpinBox();self.text_size.setRange(10,240);self.text_size.setValue(32);self.text_size.setFixedWidth(74)
        self.text_size.setAccessibleName('文字大小');self.text_size.setToolTip('文字大小');self.text_size.hide()
        self.text_size.valueChanged.connect(lambda value:self.display.set_font_size(value))
        self.mark_tool.installEventFilter(self)
        self.undo_button=button('',lambda:self.history(-1),'Quiet');self.undo_button.setIcon(icon('edit-undo'))
        self.redo_button=button('',lambda:self.history(1),'Quiet');self.redo_button.setIcon(icon('edit-redo'))
        self.undo_button.setAccessibleName('復原');self.undo_button.setToolTip('復原 · Ctrl+Z')
        self.redo_button.setAccessibleName('重做');self.redo_button.setToolTip('重做 · Ctrl+Y')
        self.reset_button=button('還原',self.reset_edits,'Quiet');self.reset_button.setToolTip('還原原圖')
        for control in (self.crop_button,self.apply_crop,self.rotation,self.flip,self.mark_tool,self.color_button,self.stroke_width,self.text_size):tools.addWidget(control)
        tools.addStretch()
        for control in (self.undo_button,self.redo_button,self.reset_button):tools.addWidget(control)
        self.body.addWidget(self.tools);self.tools.hide()

        self.display=ImageViewport();self.display.installEventFilter(self)
        self.display.changed.connect(self.update_editor);self.display.zoomChanged.connect(self.update_zoom)
        self.display.cropChanged.connect(self.apply_crop.setEnabled)
        self.display.toolChanged.connect(self.sync_tool)
        self.set_color(self.display.color)
        self.display.failed.connect(self.show_message);self.body.addWidget(self.display,1)
        self.left_edge=NavigationEdge(self.display,'上一張','chevron-left',lambda:self.step(-1))
        self.right_edge=NavigationEdge(self.display,'下一張','chevron-right',lambda:self.step(1))
        self.previous,self.next=self.left_edge.button,self.right_edge.button
        self.position=label('','Subtle');self.details=label('','Subtle')
        self.zoom_out=button('',lambda:self.display.zoom(1/1.2),'Quiet');self.zoom_out.setIcon(icon('minus'));self.zoom_out.setToolTip('縮小');self.zoom_out.setAccessibleName('縮小')
        self.zoom_in=button('',lambda:self.display.zoom(1.2),'Quiet');self.zoom_in.setIcon(icon('plus'));self.zoom_in.setToolTip('放大');self.zoom_in.setAccessibleName('放大')
        self.zoom_value=button('100%',self.display.fit,'Quiet');self.zoom_value.setToolTip('符合視窗 · Ctrl+0\n縮放 · Ctrl+滾輪')
        self.zoom_value.setAccessibleName('縮放比例，點擊符合視窗');self.zoom_value.setMinimumWidth(68)
        self.body.addLayout(row(self.details,self.position,None,self.zoom_out,self.zoom_value,self.zoom_in))
        self.shortcuts=[];self.navigation_shortcuts=[]
        for key,callback in (('Left',lambda:self.step(-1)),('Right',lambda:self.step(1)),
                             ('Ctrl+0',self.display.fit),('Ctrl+Z',lambda:self.history(-1)),
                             ('Ctrl+Y',lambda:self.history(1)),('Ctrl+S',self.save_copy),('Ctrl+Shift+S',self.save_copy)):
            shortcut=QShortcut(QKeySequence(key),self);shortcut.activated.connect(callback);self.shortcuts.append(shortcut)
            if key in ('Left','Right'):self.navigation_shortcuts.append(shortcut)
        self.load_image(path,name,strict=not self.records);self.change_tool()

    @property
    def picture(self):return self.display.image

    def load_image(self,path,name='',*,strict=False):
        self._loading=True
        self.setWindowTitle(name or Path(path).name);self.heading.setToolTip(self.windowTitle())
        self.editing=False;self.edit_button.setChecked(False);self.tools.hide()
        self.crop_button.setChecked(False);self.apply_crop.hide();self.mark_tool.setCurrentIndex(0)
        ready=self.display.load(path);self._loading=False
        if strict and not ready:raise ValueError(self.display.message)
        self.update_editor();self.update_navigation()

    def update_editor(self):
        if self._loading:return
        edits=self.display.edits;ready=edits is not None
        self.edit_button.setEnabled(ready);self.rotate_button.setEnabled(ready)
        dirty=ready and (edits.dirty or self.display.pending_text)
        self.save_button.setVisible(ready and (self.editing or dirty));self.save_button.setEnabled(dirty)
        self.undo_button.setEnabled(ready and edits.index>0);self.redo_button.setEnabled(ready and edits.index+1<len(edits.states))
        self.reset_button.setEnabled(ready and edits.state!=edits.states[0])
        self.details.setText(f'{self.display.path.suffix[1:].upper()} · {edits.state.size[0]:,} × {edits.state.size[1]:,}' if ready else '')
        for control in (self.zoom_in,self.zoom_out,self.zoom_value):control.setEnabled(ready)

    def update_zoom(self,value):
        if hasattr(self,'zoom_value'):self.zoom_value.setText(f'{value*100:.0f}%')

    def update_navigation(self):
        self.previous.setEnabled(self.index>0);self.next.setEnabled(self.index<len(self.records)-1)
        for edge,available in ((self.left_edge,self.index>0),(self.right_edge,self.index<len(self.records)-1)):
            edge.setVisible(bool(self.records) and not self.editing);edge.set_available(available)
        self.position.setText(f'{self.index+1} / {len(self.records)}' if self.records else '')
        for shortcut in self.navigation_shortcuts:shortcut.setEnabled(not self.editing)
        self.position_edges()

    def position_edges(self):
        if not hasattr(self,'left_edge'):return
        width=min(72,max(36,self.display.width()//6))
        self.left_edge.setGeometry(0,0,width,self.display.height())
        self.right_edge.setGeometry(self.display.width()-width,0,width,self.display.height())
        self.left_edge.raise_();self.right_edge.raise_()

    def can_leave(self):
        self.display.finish_text()
        return not self.display.edits or not self.display.edits.dirty or ask(self,'放棄修改','修改尚未另存，是否放棄這次修改？')

    def step(self,delta):
        target=self.index+delta
        if self.editing or not 0<=target<len(self.records) or not self.can_leave():return
        self.index=target;record=self.records[target];self.load_image(record['path'],record['name'])
        if self.on_select:self.on_select(record['id'])

    def toggle_edit(self):
        self.editing=self.edit_button.isChecked();self.tools.setVisible(self.editing)
        if not self.editing:self.display.set_tool('move')
        self.update_editor();self.update_navigation();self.display.setFocus()

    def start_crop(self):
        enabled=self.crop_button.isChecked();self.display.set_tool('crop' if enabled else 'move')
        self.apply_crop.setVisible(enabled);self.display.setFocus()

    def finish_crop(self):
        self.display.apply_crop()

    def change_tool(self):
        if not hasattr(self,'display'):return
        self.display.set_tool(self.mark_tool.currentData())

    def sync_tool(self,tool):
        self.crop_button.setChecked(tool=='crop');self.apply_crop.setVisible(tool=='crop')
        self.mark_tool.blockSignals(True)
        self.mark_tool.setCurrentIndex(max(0,self.mark_tool.findData(tool)))
        self.mark_tool.blockSignals(False)
        drawing=tool in ('pen','arrow','rectangle','text')
        self.stroke_width.setVisible(drawing and tool!='text');self.text_size.setVisible(tool=='text');self.color_button.setVisible(drawing)

    def transform_image(self,operation):
        if self.display.edits:
            self.display.edit(lambda:self.display.edits.geometry(operation))
            if self.display.tool=='crop':self.display.set_tool('move')

    def rotation_menu(self):
        menu=RoundMenu(self)
        for title,operation in (('向左 90°','left'),('向右 90°','right'),('旋轉 180°','half')):
            menu.addAction(title,lambda checked=False,value=operation:self.transform_image(value))
        menu.open_for(self.rotation)

    def flip_menu(self):
        menu=RoundMenu(self)
        for title,operation in (('水平翻轉','horizontal'),('垂直翻轉','vertical')):
            menu.addAction(title,lambda checked=False,value=operation:self.transform_image(value))
        menu.open_for(self.flip)

    def pick_color(self):
        from .image_color import AnnotationColors
        self.color_popup=AnnotationColors(self,self.display.color)
        self.color_popup.chosen.connect(self.set_color)
        self.color_popup.finished.connect(self.restore_color_focus)
        self.color_popup.open_for(self.color_button)

    def set_color(self,value):
        from .accent_picker import swatch
        self.display.set_color(value);self.color_button.setIcon(swatch(value))

    def restore_color_focus(self):
        box=self.display.text_box
        (box.entry if box is not None else self.color_button).setFocus(Qt.FocusReason.PopupFocusReason)

    def align_control_heights(self,height=None):
        self.mark_tool.ensurePolished()
        height=height or self.mark_tool.sizeHint().height()
        for control in (self.stroke_width,self.text_size):
            if control.minimumHeight()!=height:control.setFixedHeight(height)

    def history(self,direction):
        if self.display.edits:self.display.edit(self.display.edits.undo if direction<0 else self.display.edits.redo)

    def reset_edits(self):
        if self.display.edits:self.display.edit(self.display.edits.reset)

    def show_message(self,text):self.details.setText(text);self.details.setToolTip(text)

    def save_to(self,destination):
        from .image_edits import save_copy
        self.display.finish_text()
        saved=save_copy(self.display.path,destination,self.display.edits,self.display.stamp)
        self.update_editor();self.show_message('已另存：'+saved.name)
        if self.on_save:
            try:self.on_save(saved)
            except (ValueError,OSError,DatabaseError) as exc:self.show_message('副本已保存，圖片庫更新失敗：'+str(exc))
        return saved

    def save_copy(self):
        self.display.finish_text()
        if not self.display.edits or not self.display.edits.dirty:return
        source=self.display.path;initial=source.with_name(source.stem+'-編輯.png');suffix=2
        while initial.exists():initial=source.with_name(f'{source.stem}-編輯-{suffix}.png');suffix+=1
        path,_=QFileDialog.getSaveFileName(self,'另存副本',str(initial),'PNG 圖片 (*.png)')
        if path:
            if not Path(path).suffix:path+='.png'
            try:self.save_to(path)
            except (ValueError,OSError) as exc:self.show_message(str(exc))

    def reject(self):
        if self.can_leave():super().reject()

    def showEvent(self,event):
        self.align_control_heights()
        super().showEvent(event);self.layout().activate();self.display.fit();self.position_edges();self.display.setFocus()

    def eventFilter(self,watched,event):
        if watched is getattr(self,'display',None) and event.type()==QEvent.Type.Resize:self.position_edges()
        if watched is getattr(self,'mark_tool',None):
            if event.type()==QEvent.Type.Resize:self.align_control_heights(event.size().height())
            elif event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):self.align_control_heights()
        return super().eventFilter(watched,event)


def show_image(parent,path,name='',**options):
    try:dialog=ImageViewerDialog(parent,path,name,**options)
    except ValueError as exc:
        parent.notice(str(exc));return
    dialog.exec()
