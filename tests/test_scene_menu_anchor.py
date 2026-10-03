"""Desktop popups anchored to Qt scene widgets, without a second scene transform."""
import unittest
from shiboken6 import isValid
from PySide6.QtCore import QPoint,QPointF,Qt
from PySide6.QtWidgets import QApplication,QWidget,QPushButton,QGraphicsScene,QGraphicsView,QVBoxLayout,QHBoxLayout
from prompt_studio.widgets import RoundMenu

APP=QApplication.instance() or QApplication([])


class SceneMenuAnchorTests(unittest.TestCase):
    def setUp(self):
        self.view=QGraphicsView();self.scene=QGraphicsScene(self.view)
        self.scene.setSceneRect(-2500,-2500,5000,5000);self.view.setScene(self.scene)
        self.view.setGeometry(32,32,700,700)
        self.panel=QWidget();layout=QVBoxLayout(self.panel);layout.setContentsMargins(17,13,19,11)
        nested=QWidget();buttons=QHBoxLayout(nested);buttons.setContentsMargins(9,7,8,6)
        self.first=QPushButton('More');self.second=QPushButton('History')
        buttons.addWidget(self.first);buttons.addWidget(self.second);layout.addWidget(nested)
        self.proxy=self.scene.addWidget(self.panel);self.proxy.setPos(180,-90)
        self.view.show();APP.processEvents();self.menus=[]
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for menu in self.menus:
            if isValid(menu):menu.close()
        self.view.close();APP.processEvents()

    def place(self,button,scale,point):
        self.view.resetTransform();self.view.scale(scale,scale)
        self.view.centerOn(410,-270);APP.processEvents()
        local=button.mapTo(self.panel,QPoint())
        self.proxy.setPos(self.view.mapToScene(point)-QPointF(local));APP.processEvents()

    def menu(self,parent=None):
        menu=RoundMenu(parent or self.panel)
        menu.setStyleSheet('QMenu { font-size: 14px; padding: 5px; } QMenu::item { padding: 10px 20px; }')
        for name in ('Add input','Input values','Remove waiting item','Cancel workflow'):menu.addAction(name)
        self.menus.append(menu);return menu

    def desktop_point(self,button,point):
        # Independent oracle: compose scene and viewport transforms.
        local=QPointF(button.mapTo(self.panel,point))
        viewport=self.view.viewportTransform().map(self.proxy.sceneTransform().map(local))
        return self.view.viewport().mapToGlobal(viewport.toPoint())

    def test_zoom_pan_nested_buttons_keep_desktop_size_and_fixed_gap(self):
        sizes=[]
        for scale in (.35,.9,1.25):
            for button in (self.first,self.second):
                for point in (QPoint(100,90),QPoint(260,330)):
                    with self.subTest(scale=scale,button=button.text(),point=point):
                        self.place(button,scale,point);menu=self.menu();menu.open_for(button,self.view);APP.processEvents()
                        self.assertIsNone(menu.graphicsProxyWidget())
                        self.assertTrue(menu.isWindow())
                        top=self.desktop_point(button,QPoint())
                        bottom=self.desktop_point(button,QPoint(button.width(),button.height()))
                        actual=menu.mapToGlobal(QPoint())
                        self.assertLessEqual(abs(actual.x()-top.x()),1)
                        self.assertLessEqual(abs(actual.y()-bottom.y()-4),1)
                        sizes.append(menu.size());menu.hide()
        self.assertTrue(all(size==sizes[0] for size in sizes))

    def test_screen_bottom_right_flips_above_and_aligns_trigger_right(self):
        for scale in (.35,.9,1.25):
            with self.subTest(scale=scale):
                self.place(self.second,scale,QPoint(625,655))
                menu=self.menu();menu.open_for(self.second,self.view);APP.processEvents()
                top=self.desktop_point(self.second,QPoint())
                bottom=self.desktop_point(self.second,QPoint(self.second.width(),self.second.height()))
                area=self.view.screen().availableGeometry().adjusted(4,4,-4,-4)
                geometry=menu.geometry()
                self.assertTrue(area.contains(geometry),str((area,geometry)))
                self.assertLessEqual(abs(geometry.bottom()+5-top.y()),1)
                self.assertLessEqual(abs(geometry.right()+1-bottom.x()),1)
                menu.hide()

    def test_context_popup_global_point_and_submenu_are_not_embedded(self):
        self.place(self.first,.35,QPoint(100,90))
        menu=self.menu();submenu=RoundMenu(menu);submenu.addAction('Text');menu.addMenu(submenu)
        menu.open_at(QPoint(210,180));APP.processEvents()
        self.assertIsNone(menu.graphicsProxyWidget());self.assertIsNone(submenu.graphicsProxyWidget())
        self.assertEqual(menu.pos(),QPoint(210,180))
        menu.setActiveAction(submenu.menuAction());submenu.popup(QPoint(390,180))
        self.assertIsNone(submenu.graphicsProxyWidget())
        # Qt styles allow a small submenu overlap for continuous pointer travel.
        self.assertLessEqual(abs(submenu.x()-menu.geometry().right()-1),12)
        submenu.hide()

    def test_plain_widget_button_keeps_normal_desktop_anchor(self):
        host=QWidget();host.setGeometry(80,70,300,160);button=QPushButton('More',host);button.setGeometry(25,40,90,30)
        host.show();APP.processEvents();menu=self.menu(host)
        menu.open_for(button);APP.processEvents()
        self.assertEqual(menu.pos(),button.mapToGlobal(QPoint(0,button.height()))+QPoint(0,4))
        self.assertIsNone(menu.graphicsProxyWidget());menu.hide();host.close()


if __name__=='__main__':unittest.main()
