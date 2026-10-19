"""Localized window-menu appearance preserves native command routing."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget
from shiboken6 import isValid
from prompt_calculus_studio.core import DEFAULT_SETTINGS
from prompt_calculus_studio.theme import stylesheet, widget_palette
from prompt_calculus_studio.window_menu import open_window_menu
from prompt_calculus_studio.window_chrome import install_expanded_chrome

APP=QApplication.instance() or QApplication([])


class WindowMenuTests(unittest.TestCase):
    def setUp(self):
        self.window=QMainWindow();self.window.setCentralWidget(QWidget())
        self.window.resize(800,600)
        self.window.state={'settings':dict(DEFAULT_SETTINGS)}
        self.window.setStyleSheet(stylesheet(DEFAULT_SETTINGS))
        self.window.setPalette(widget_palette(DEFAULT_SETTINGS))
        self.commands=[]
        self.control=SimpleNamespace(hwnd=123,native_api=SimpleNamespace(
            post_system_command=lambda hwnd,command:self.commands.append((hwnd,command))))
        self.window._expanded_window_chrome=self.control
        self.window.show();APP.processEvents()

    def tearDown(self):
        menu=getattr(self.control,'system_menu',None)
        if menu is not None and isValid(menu):menu.close()
        self.window.close();self.window.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()

    def menu(self):
        self.assertTrue(open_window_menu(self.window));APP.processEvents()
        return self.control.system_menu

    def test_menu_commands_keyboard_cancel_toggle_and_state(self):
        menu=self.menu();actions={a.data():a for a in menu.actions() if not a.isSeparator()}
        self.assertFalse(actions[0xF120].isEnabled())
        self.assertTrue(actions[0xF010].isEnabled());self.assertTrue(actions[0xF000].isEnabled())
        self.assertTrue(menu.property('pcsPopupSurface'))
        self.assertEqual(menu.activeAction(),actions[0xF010])
        QTest.keyClick(menu,Qt.Key.Key_Escape);APP.processEvents()
        self.assertEqual(self.commands,[])
        menu=self.menu();self.assertTrue(open_window_menu(self.window));APP.processEvents()
        self.assertFalse(isValid(menu) and menu.isVisible())
        self.window.showMaximized();APP.processEvents()
        menu=self.menu();actions={a.data():a for a in menu.actions() if not a.isSeparator()}
        self.assertTrue(actions[0xF120].isEnabled())
        for command in (0xF010,0xF000,0xF030):self.assertFalse(actions[command].isEnabled())
        QTest.keyClick(menu,Qt.Key.Key_Return);APP.processEvents()
        self.assertEqual(self.commands,[(123,0xF120)])

    def test_fixed_window_and_pointer_anchor_keep_native_actions(self):
        self.window.setFixedSize(800,600)
        self.assertTrue(open_window_menu(self.window,self.window.mapToGlobal(QPoint(40,30))))
        menu=self.control.system_menu;APP.processEvents()
        actions={a.data():a for a in menu.actions() if not a.isSeparator()}
        self.assertFalse(actions[0xF000].isEnabled());self.assertFalse(actions[0xF030].isEnabled())
        QTest.mouseClick(menu,Qt.MouseButton.LeftButton,pos=menu.actionGeometry(actions[0xF060]).center())
        APP.processEvents();self.assertEqual(self.commands,[(123,0xF060)])

    def test_native_routes_only_alt_space_and_window_menu_requests(self):
        window=QMainWindow();window.setCentralWidget(QWidget())
        with patch('prompt_calculus_studio.window_chrome.supported',return_value=True):
            control=install_expanded_chrome(window)
        actual=(control.hwnd,control.caption_replaced)
        control.hwnd,control.caption_replaced=456,True
        try:
            with patch('prompt_calculus_studio.window_menu.open_window_menu') as show:
                self.assertEqual(control.native_notification(456,0x112,0xF100,32),(True,0))
                APP.processEvents();show.assert_called_once_with(window,None)
                show.reset_mock()
                self.assertEqual(control.native_notification(456,0x104,32,1<<29),(True,0))
                self.assertEqual(control.native_notification(456,0x104,32,(1<<29)|(1<<30)),(True,0))
                self.assertEqual(control.native_notification(456,0x106,32,1<<29),(True,0))
                APP.processEvents();show.assert_called_once_with(window)
                show.reset_mock()
                self.assertEqual(control.native_notification(456,0x112,0xF090,0),(True,0))
                APP.processEvents();self.assertEqual(show.call_count,1)
                for hwnd,command,key in ((456,0xF100,ord('f')),(456,0xF060,0),(999,0xF100,32)):
                    self.assertEqual(control.native_notification(hwnd,0x112,command,key),(False,0))
        finally:
            control.hwnd,control.caption_replaced=actual
            window.close();window.deleteLater();APP.processEvents()


@unittest.skipUnless(APP.platformName()=='windows','requires Windows system commands')
class NativeWindowMenuTests(unittest.TestCase):
    def test_real_system_commands_preserve_restore_and_close_veto(self):
        class Host(QMainWindow):
            allow_close=False
            def closeEvent(self,event):
                event.accept() if self.allow_close else event.ignore()
        window=Host();window.setCentralWidget(QWidget());window.resize(640,480)
        control=install_expanded_chrome(window)
        window.show();QTest.qWait(80)
        self.assertIsNotNone(control.native_api)
        def choose(command):
            self.assertTrue(open_window_menu(window));menu=control.system_menu
            action=next(a for a in menu.actions() if a.data()==command)
            self.assertTrue(action.isEnabled())
            QTest.mouseClick(menu,Qt.MouseButton.LeftButton,pos=menu.actionGeometry(action).center())
            QTest.qWait(100)
        try:
            choose(0xF030);self.assertTrue(window.isMaximized())
            choose(0xF120);self.assertFalse(window.isMaximized())
            choose(0xF020);self.assertTrue(window.isMinimized())
            window.showNormal();QTest.qWait(80)
            choose(0xF060);self.assertTrue(window.isVisible())
            window.allow_close=True
            choose(0xF060);self.assertFalse(window.isVisible())
        finally:
            window.allow_close=True;window.close();window.deleteLater();APP.processEvents()


if __name__=='__main__':unittest.main()
