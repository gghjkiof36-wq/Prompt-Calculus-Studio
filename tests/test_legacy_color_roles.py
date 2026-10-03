"""Legacy painted controls follow the palette without recoloring documents."""
import copy,os,tempfile,unittest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QRect
from PySide6.QtGui import QImage,QPainter,QStandardItem,QStandardItemModel,QFontDatabase
from PySide6.QtWidgets import QApplication,QStyle,QStyleOptionViewItem,QFrame,QVBoxLayout
from PySide6.QtTest import QTest
from prompt_studio.theme import visual_tokens
from prompt_studio.views import PromptDelegate,BuilderDelegate,BuilderTree,DETAIL_ROLE,widget_colors

APP=QApplication.instance() or QApplication([])
for name in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)


class LegacyColorRoleTests(unittest.TestCase):
    def setUp(self):
        from prompt_studio.window import Window
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':self.temp.name});self.env.start()
        self.transport=patch('prompt_studio.comfy_client.ComfyClient.request',return_value=None);self.transport.start()
        self.w=Window(self.temp.name);self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.resize(1200,800);self.w.show();self.w.enter_canvas();QTest.qWait(30)

    def tearDown(self):
        self.w.close();APP.processEvents();self.transport.stop();self.env.stop();self.temp.cleanup()

    def palette(self,name):
        self.w.state['settings']['visual_palette']=name;self.w.apply_theme(preserve_layout=True);QTest.qWait(20)
        return visual_tokens(self.w.state['settings'])

    def test_painted_lists_and_embedded_owner_follow_each_palette(self):
        prompt=PromptDelegate(self.w);tree=self.w.selected;builder=BuilderDelegate(tree)
        model=QStandardItemModel();item=QStandardItem('Synthetic prompt');model.appendRow(item)
        item.setData(('use','synthetic'),Qt.ItemDataRole.UserRole)
        option=QStyleOptionViewItem();option.font=self.w.font();option.rect=QRect(0,0,500,140)
        proxy_panel=QFrame();layout=QVBoxLayout(proxy_panel);embedded=BuilderTree();layout.addWidget(embedded)
        scene=self.w.canvas.view.scene();proxy=scene.addWidget(proxy_panel)
        document=copy.deepcopy(self.w.canvas.data())
        try:
            for name in ('paper','graphite','mist'):
                colors=self.palette(name);self.assertEqual(widget_colors(embedded),colors)
                for chosen,hover,role in ((False,False,'surface'),(False,True,'hover'),(True,False,'selected')):
                    item.setData(dict(name='Synthetic prompt',prompt='plain content',chosen=chosen),DETAIL_ROLE)
                    option.state=QStyle.StateFlag.State_Enabled | (QStyle.StateFlag.State_MouseOver if hover else QStyle.StateFlag.State_None)
                    image=QImage(500,140,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.transparent)
                    painter=QPainter(image);prompt.paint(painter,option,model.index(0,0));painter.end()
                    self.assertEqual(image.pixelColor(18,120).name(),colors[role])
                    option.state|=QStyle.StateFlag.State_Selected if chosen else QStyle.StateFlag.State_None
                    image.fill(Qt.GlobalColor.transparent);painter=QPainter(image);builder.paint(painter,option,model.index(0,0));painter.end()
                    self.assertEqual(image.pixelColor(18,120).name(),colors[role])
                self.assertEqual(self.w.canvas.data(),document)
        finally:
            scene.removeItem(proxy);proxy.setWidget(None);proxy_panel.deleteLater()

    def test_image_editor_theme_changes_leave_brush_and_layer_colors_intact(self):
        from prompt_studio.composition_image import document,layer,render_image
        cid=next(iter(self.w.canvas.containers));drawing=document(120,80)
        shape=layer('rect',10,10,60,45);shape.update(fill='#bc412d',stroke='#124b88');drawing['layers'].append(shape)
        self.w.canvas.commit(lambda state:state['multi_output']['canvases'][cid].update(image=drawing))
        self.w.canvas.open_editor(cid);QTest.qWait(20);editor=self.w.canvas.editor_page
        saved=copy.deepcopy(editor.image());history=copy.deepcopy(self.w.canvas.undo_stack)
        rendered=render_image(saved,self.w.store.directory)
        for name in ('paper','mist','graphite'):
            colors=self.palette(name)
            self.assertEqual(editor.view.backgroundBrush().color().name(),colors['base'])
            self.assertEqual(editor.view.outline.pen().color().name(),colors['info'])
            self.assertEqual(editor.color,'#718caa');self.assertEqual(editor.image(),saved)
            self.assertEqual(self.w.canvas.undo_stack,history)
            self.assertEqual(render_image(editor.image(),self.w.store.directory),rendered)
        editor.close_editor();APP.processEvents()

    def test_recent_destination_warning_repaints_with_semantic_warning(self):
        warning=self.w.recent.destination_warning
        self.assertEqual(warning.objectName(),'DestinationWarning')
        for name in ('paper','mist','graphite'):
            colors=self.palette(name);warning.ensurePolished();image=warning.grab().toImage()
            self.assertTrue(any(image.pixelColor(x,y).name()==colors['warning'] for x in range(7,14) for y in range(2)))


if __name__=='__main__':unittest.main()
