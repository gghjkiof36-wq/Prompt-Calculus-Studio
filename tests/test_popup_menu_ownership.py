"""Real editor context menus and owned submenu surface/lifetime regressions."""
import unittest
from shiboken6 import isValid
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QMouseEvent, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QPlainTextEdit, QVBoxLayout, QWidget

from prompt_studio.core import DEFAULT_SETTINGS
from prompt_studio.theme import stylesheet, visual_tokens, widget_palette
from prompt_studio.widgets import RoundMenu


APP=QApplication.instance() or QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')


class PopupMenuOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.windows=[];self.menus=[]

    def tearDown(self):
        for menu in self.menus:
            if isValid(menu):menu.close()
        for window in self.windows:
            window.close();window.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()

    def host(self,palette):
        settings=dict(DEFAULT_SETTINGS,visual_palette=palette)
        host=QWidget();host.setStyleSheet(stylesheet(settings));host.setPalette(widget_palette(settings))
        host.setGeometry(20,20,520,320);host.box=QVBoxLayout(host);self.windows.append(host)
        host.show();APP.processEvents()
        return host,visual_tokens(settings)

    def assert_surface(self,menu,tokens,frosted):
        APP.processEvents();image=menu.grab().toImage()
        actual=image.pixelColor(menu.width()//2,menu.height()-3);expected=QColor(tokens['raised'])
        self.assertEqual(actual.alpha(),255)
        tolerance=16 if frosted else 0
        self.assertTrue(all(abs(a-b)<=tolerance for a,b in zip(actual.getRgb()[:3],expected.getRgb()[:3])),
                        (actual.getRgb(),expected.getRgb(),frosted))
        self.assertEqual(bool(menu.property('pcsPopupSurface')),frosted)
        if frosted:self.assertEqual(image.pixelColor(0,0).alpha(),0)
        return image

    def test_standard_line_and_multiline_context_menus_have_opaque_readable_fallback(self):
        for palette in ('graphite','mist','paper'):
            for editor_type in (QLineEdit,QPlainTextEdit):
                with self.subTest(palette=palette,editor=editor_type.__name__):
                    host,tokens=self.host(palette);editor=editor_type();host.box.addWidget(editor)
                    if isinstance(editor,QLineEdit):editor.setText('Synthetic editor text')
                    else:editor.setPlainText('Synthetic editor text')
                    editor.selectAll();APP.processEvents()
                    menu=editor.createStandardContextMenu();self.menus.append(menu)
                    menu.popup(editor.mapToGlobal(QPoint(10,10)));APP.processEvents()
                    action=next(a for a in menu.actions() if a.isEnabled() and not a.isSeparator())
                    menu.setActiveAction(action);image=self.assert_surface(menu,tokens,False)
                    rect=menu.actionGeometry(action);crop=image.copy(rect)
                    colors={crop.pixelColor(x,y).name() for x in range(crop.width()) for y in range(crop.height())}
                    self.assertIn(tokens['popup_active'],colors)
                    foreground=QColor(tokens['text'])
                    self.assertTrue(any(sum(abs(a-b) for a,b in zip(QColor(color).getRgb()[:3],foreground.getRgb()[:3]))<20
                                        for color in colors),'editor menu action text remains readable')
                    menu.close();host.close()

    def test_title_submenu_reopens_on_hover_and_lives_until_root_closes(self):
        for palette in ('graphite','mist','paper'):
            with self.subTest(palette=palette):
                host,tokens=self.host(palette);root=RoundMenu(host);self.menus.append(root)
                child=root.addMenu('本次 Stage 結果');child.addAction('Stage 1 結果')
                other=root.addAction('其他操作')
                self.assertIsInstance(child,RoundMenu);self.assertIs(child.parentWidget(),root)
                root.popup(QPoint(50,60));APP.processEvents()
                for opening in (1,2):
                    QTest.mouseMove(root,root.actionGeometry(child.menuAction()).center());QTest.qWait(350)
                    self.assertTrue(child.isVisible(),(palette,opening))
                    self.assertEqual(child.width(),240)
                    self.assert_surface(child,tokens,True)
                    QTest.mouseMove(child,child.actionGeometry(child.actions()[0]).center())
                    QTest.qWait(30)
                    point=root.actionGeometry(other).center()
                    # An active popup grabs QTest's platform mouse move on
                    # offscreen; deliver the corresponding parent move to Qt
                    # directly so its real submenu hover/close path runs.
                    APP.sendEvent(root,QMouseEvent(QEvent.Type.MouseMove,QPointF(point),
                        QPointF(root.mapToGlobal(point)),Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier))
                    QTest.qWait(1100)
                    self.assertFalse(child.isVisible(),(palette,opening,root.activeAction().text() if root.activeAction() else None,
                                                       root.geometry(),child.geometry()))
                    self.assertTrue(isValid(child));self.assertTrue(isValid(child.menuAction()))
                    self.assertIn(child.menuAction(),root.actions())
                root.close();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()
                self.assertFalse(isValid(root));self.assertFalse(isValid(child))

    def test_existing_menu_overload_keeps_qt_ownership_and_icon_title_gets_surface(self):
        host,tokens=self.host('graphite');root=RoundMenu(host);self.menus.append(root)
        supplied=QMenu('Externally owned',host);supplied.addAction('Existing action');self.menus.append(supplied)
        result=root.addMenu(supplied)
        self.assertIs(result,supplied.menuAction());self.assertIs(supplied.parentWidget(),host)
        pixmap=QPixmap(16,16);pixmap.fill(Qt.GlobalColor.blue)
        child=root.addMenu(QIcon(pixmap),'嵌入畫布');child.addAction('畫布 1')
        self.assertIsInstance(child,RoundMenu);self.assertEqual(child.title(),'嵌入畫布');self.assertFalse(child.icon().isNull())
        root.popup(QPoint(50,60));APP.processEvents();supplied.popup(QPoint(310,70));APP.processEvents()
        self.assert_surface(supplied,tokens,False);supplied.hide();root.close()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()
        self.assertTrue(isValid(supplied))


if __name__=='__main__':unittest.main()
