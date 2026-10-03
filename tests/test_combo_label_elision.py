"""Closed labels show an ellipsis while selection and help retain full text."""
import unittest
from PySide6.QtCore import QPoint, QEvent, Qt
from PySide6.QtGui import QFontDatabase, QHelpEvent, QIcon, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QStyle, QStyleOptionComboBox, QToolTip, QWidget

from prompt_studio.core import DEFAULT_SETTINGS
from prompt_studio.theme import stylesheet,widget_palette
from prompt_studio.widgets import ComboBox,FontComboBox
from prompt_studio.recent_sidebar import CanvasWorkspaceCombo
from prompt_studio.workflow_manager import FolderComboBox


APP=QApplication.instance() or QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')
QFontDatabase.addApplicationFont('C:/Windows/Fonts/consola.ttf')


class ComboLabelElisionTests(unittest.TestCase):
    def setUp(self):self.windows=[]

    def tearDown(self):
        QToolTip.hideText()
        for window in self.windows:window.close();window.deleteLater()
        APP.processEvents()

    def host(self,size):
        settings=dict(DEFAULT_SETTINGS,ui_size=size)
        host=QWidget();host.setStyleSheet(stylesheet(settings));host.setPalette(widget_palette(settings))
        host.resize(1200,240);host.move(20,20);self.windows.append(host)
        host.show();APP.processEvents();return host

    def oracle_label(self,combo):
        option=QStyleOptionComboBox();combo.initStyleOption(option)
        field=combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,combo)
        available=field.width()-2-(option.iconSize.width()+4 if not option.currentIcon.isNull() else 0)
        return combo.fontMetrics().elidedText(combo.currentText(),Qt.TextElideMode.ElideRight,max(0,available)),field

    def test_rendered_label_matches_native_ellipsis_at_three_sizes_and_two_widths(self):
        long='Acrylic · 背景霧化與材質效果'
        for size in (11,18,22):
            for width in (150,760):
                for with_icon in (False,True):
                    with self.subTest(size=size,width=width,icon=with_icon):
                        host=self.host(size);combo=ComboBox(host);reference=QComboBox(host)
                        pixmap=QPixmap(16,16);pixmap.fill(Qt.GlobalColor.blue);icon=QIcon(pixmap) if with_icon else QIcon()
                        combo.addItem(icon,long,'saved-value');combo.setGeometry(20,20,width,56)
                        reference.setGeometry(20,100,width,56);combo.show();reference.show()
                        combo.clearFocus();reference.clearFocus();APP.processEvents()
                        visible,field=self.oracle_label(combo)
                        self.assertEqual(visible.endswith('…'),width==150)
                        reference.addItem(icon,visible);APP.processEvents()
                        self.assertEqual(combo.grab().toImage().copy(field),reference.grab().toImage().copy(field))
                        self.assertEqual(combo.currentText(),long);self.assertEqual(combo.currentData(),'saved-value')
                        self.assertEqual(combo.toolTip(),long if width==150 else '')
                        host.close()

    def test_resize_font_and_selection_refresh_only_generated_hints(self):
        host=self.host(11);combo=ComboBox(host);combo.setGeometry(20,20,150,48)
        long='Acrylic · 背景霧化與材質效果';second='Mica · 背景紋理與桌面配色'
        combo.addItems([long,'實色',second]);combo.show();APP.processEvents()
        self.assertEqual(combo.toolTip(),long)
        combo.setCurrentIndex(1);APP.processEvents();self.assertEqual(combo.toolTip(),'')
        combo.setCurrentIndex(2);APP.processEvents();self.assertEqual(combo.toolTip(),second)
        combo.resize(760,48);APP.processEvents();self.assertEqual(combo.toolTip(),'')
        combo.resize(300,56);APP.processEvents();self.assertEqual(combo.toolTip(),'')
        settings=dict(DEFAULT_SETTINGS,ui_size=22);host.setStyleSheet(stylesheet(settings));APP.processEvents()
        self.assertEqual(combo.toolTip(),second)
        point=combo.rect().center();APP.sendEvent(combo,QHelpEvent(QEvent.Type.ToolTip,point,combo.mapToGlobal(point)))
        self.assertEqual(QToolTip.text(),second);QToolTip.hideText()
        help_text='調整背景材質；不影響文字區的可讀性。'
        combo.setToolTip(help_text);combo.setCurrentIndex(0);combo.resize(150,56);APP.processEvents()
        self.assertEqual(combo.toolTip(),help_text)
        combo.resize(760,56);APP.processEvents();self.assertEqual(combo.toolTip(),help_text)
        combo.setToolTip('');combo.resize(150,56);APP.processEvents();self.assertEqual(combo.toolTip(),long)

    def test_workspace_and_folder_use_same_label_hint_contract(self):
        host=self.host(18)
        for index,control in enumerate((CanvasWorkspaceCombo(host),FolderComboBox())):
            control.setParent(host);control.addItems(['全部資料夾','工作區與資料夾的完整長名稱_'*8])
            control.setGeometry(20,20+index*80,180,60);control.show();control.setCurrentIndex(1);APP.processEvents()
            self.assertTrue(self.oracle_label(control)[0].endswith('…'))
            self.assertEqual(control.toolTip(),control.currentText())
            control.setCurrentIndex(0);control.setFixedWidth(420);APP.processEvents()
            self.assertEqual(control.toolTip(),'')

    def test_editable_font_and_combo_keep_input_text_cursor_and_explicit_help(self):
        host=self.host(18)
        for index,control in enumerate((FontComboBox(host),ComboBox(host))):
            control.setEditable(True);control.setGeometry(20,20+index*80,150,56);control.show()
            control.setEditText('Microsoft JhengHei UI');editor=control.lineEdit();editor.setFocus();editor.setCursorPosition(9)
            control.setToolTip('字型選擇說明');APP.processEvents();before=(editor.text(),editor.cursorPosition())
            control.repaint();APP.processEvents()
            self.assertEqual((editor.text(),editor.cursorPosition()),before)
            self.assertEqual(control.toolTip(),'字型選擇說明')
            QTest.keyClicks(editor,'X');self.assertIn('X',editor.text())


if __name__=='__main__':unittest.main()
