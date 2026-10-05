"""Rendered popup regressions for the final 0.8.6 feedback; no services."""
import os
import unittest
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCompleter, QLineEdit, QFrame, QStyleFactory, QVBoxLayout, QWidget

from prompt_calculus_studio.core import DEFAULT_SETTINGS
from prompt_calculus_studio.stage_parameter_choices import ParameterChoices
from prompt_calculus_studio.recent_sidebar import CanvasWorkspaceCombo
from prompt_calculus_studio.theme import stylesheet, visual_tokens, widget_palette
from prompt_calculus_studio.widgets import ComboBox, FontComboBox, RoundMenu, style_completion
from prompt_calculus_studio.popup_surface import PopupSurface


APP=QApplication.instance() or QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')
QFontDatabase.addApplicationFont('C:/Windows/Fonts/consola.ttf')


class PopupSurfaceFinalTests(unittest.TestCase):
    def setUp(self):
        self.windows=[]
        self.style=APP.style().objectName()

    def tearDown(self):
        for window in self.windows:
            window.close();window.deleteLater()
        APP.processEvents()
        APP.setStyle(self.style)

    def host(self,palette='graphite',size=11,width=260):
        settings=dict(DEFAULT_SETTINGS,visual_palette=palette,ui_size=size)
        window=QWidget();window.setStyleSheet(stylesheet(settings));window.setPalette(widget_palette(settings))
        window.box=QVBoxLayout(window);window.resize(width,150);window.move(20,20)
        self.windows.append(window)
        return window,visual_tokens(settings)

    def check_surface(self,widget,tokens,name):
        APP.processEvents();image=widget.grab().toImage()
        pixel=image.pixelColor(widget.width()//2,widget.height()-3)
        self.assertEqual(pixel.alpha(),255,(name,pixel.getRgb()))
        expected=QColor(tokens['raised'])
        self.assertTrue(all(abs(a-b)<=16 for a,b in zip(pixel.getRgb()[:3],expected.getRgb()[:3])),name)
        self.assertEqual(widget.windowOpacity(),1.0)
        # These are alpha-backed widgets, not a rectangular native background
        # wearing a rounded inner view. The actual corner must stay clear.
        self.assertEqual(image.pixelColor(0,0).alpha(),0,name)
        edge_limit=max(QColor(tokens['raised']).lightness(),QColor(tokens['divider']).lightness())+5
        self.assertTrue(all(image.pixelColor(x,widget.height()//2).lightness()<=edge_limit
                            for x in range(1,5)),(name,'bright native edge'))
        target=os.environ.get('PCS_TEST_ARTIFACT_DIR')
        if target:
            path=Path(target);path.mkdir(parents=True,exist_ok=True)
            self.assertTrue(image.save(str(path/(name+'.png'))))
        return image

    def check_glyphs(self,image,rect,tokens):
        target=QColor(tokens['text'])
        pixels=[image.pixelColor(x,y) for x in range(rect.left(),min(rect.right(),rect.left()+180))
                for y in range(rect.top()+4,rect.bottom()-3)]
        self.assertTrue(any(p.alpha()==255 and sum(abs(a-b) for a,b in zip(p.getRgb()[:3],target.getRgb()[:3]))<20
                            for p in pixels),'opaque foreground glyphs must survive surface transparency')

    def test_combo_menu_completion_and_search_render_three_palettes(self):
        for palette in ('graphite','mist','paper'):
            with self.subTest(palette=palette):
                window,tokens=self.host(palette)
                combo=ComboBox();combo.addItems(['第一個選項','第二個選項','第三個選項']);window.box.addWidget(combo)
                editor=QLineEdit();window.box.addWidget(editor)
                completer=QCompleter(['First choice','Second choice'],editor);editor.setCompleter(completer)
                style_completion(completer,editor)
                search=ParameterChoices();search.addItems([f'選項 Choice {i:02}' for i in range(25)])
                window.box.addWidget(search);window.show();APP.processEvents()
                combo.showPopup();APP.processEvents()
                view=combo.view();rect=view.visualRect(view.model().index(0,0))
                self.assertEqual(rect.height(),28)
                self.assertEqual(combo._popup.width(),240)
                image=self.check_surface(combo._popup,tokens,palette+'-combo')
                self.check_glyphs(image,rect.translated(view.viewport().mapTo(combo._popup,QPoint())),tokens)
                combo.hidePopup()
                workspace=CanvasWorkspaceCombo();workspace.addItem('預設工作區');workspace.setFixedWidth(156)
                window.box.addWidget(workspace);APP.processEvents();workspace.showPopup();APP.processEvents()
                self.assertEqual(workspace._popup.width(),240)
                self.assertEqual(workspace.view().visualRect(workspace.model().index(0,0)).height(),28)
                self.check_surface(workspace._popup,tokens,palette+'-workspace');workspace.hidePopup()
                menu=RoundMenu(window);first=menu.addAction('第一個選項');menu.addAction('第二個選項')
                menu.open_at(QPoint(40,40));menu.setActiveAction(first);APP.processEvents()
                self.assertEqual(menu.width(),240);self.assertEqual(menu.actionGeometry(first).height(),28)
                image=self.check_surface(menu,tokens,palette+'-menu');self.check_glyphs(image,menu.actionGeometry(first),tokens)
                menu.close()
                editor.setFocus();completer.complete(QRect(0,editor.height(),240,editor.height()));APP.processEvents()
                completion=completer.popup();completion.setCurrentIndex(completer.completionModel().index(0,0))
                self.assertEqual(completion.visualRect(completion.currentIndex()).height(),28)
                self.check_surface(completion,tokens,palette+'-completion')
                completion.resize(completion.width(),completion.height()+16);APP.processEvents()
                self.assertEqual(completion._pcs_surface.backdrop.size(),completion.size())
                completion.hide()
                search.showPopup();popup=search._choices_popup;APP.processEvents()
                self.assertEqual(popup.items.visualItemRect(popup.items.item(0)).height(),28)
                self.check_surface(popup,tokens,palette+'-search');popup.search.setText('not present')
                self.check_surface(popup,tokens,palette+'-empty-search');search.hidePopup();window.close()

    def test_native_styles_large_font_long_items_single_row_and_screen_edges(self):
        available={style.lower():style for style in QStyleFactory.keys()}
        for style in ('fusion','windows','windows11','windowsvista'):
            if style not in available:continue
            APP.setStyle(available[style])
            for size in (11,18):
                for count in (1,30):
                    with self.subTest(style=style,size=size,count=count):
                        window,_=self.host(size=size,width=760)
                        combo=ComboBox();combo.addItems(['Very long model name '+str(i)+'—'*60 for i in range(count)])
                        window.box.addWidget(combo);window.show();APP.processEvents()
                        area=window.screen().availableGeometry()
                        window.move(area.right()-window.width(),area.bottom()-window.height());APP.processEvents()
                        combo.setCurrentIndex(count-1);combo.showPopup();APP.processEvents()
                        view=combo.view();rect=view.visualRect(view.model().index(count-1,0))
                        self.assertGreaterEqual(rect.height(),view.fontMetrics().height()+8)
                        self.assertLessEqual(rect.height(),view.fontMetrics().height()+12)
                        self.assertLessEqual(combo._popup.width(),480)
                        self.assertTrue(area.contains(combo._popup.frameGeometry()),(area,combo._popup.frameGeometry()))
                        if count==1:self.assertLessEqual(combo._popup.height(),rect.height()+16)
                        else:self.assertTrue(view.verticalScrollBar().isVisible())
                        QTest.keyClick(view,Qt.Key.Key_Escape)
                        self.assertEqual(combo.currentIndex(),count-1)
                        combo.showPopup();APP.processEvents();QTest.keyClick(view,Qt.Key.Key_Home)
                        QTest.keyClick(view,Qt.Key.Key_Return);self.assertEqual(combo.currentIndex(),0)
                        window.close()

    def test_font_picker_uses_shared_surface_and_keeps_font_selection_explicit(self):
        window,tokens=self.host();picker=FontComboBox();window.box.addWidget(picker)
        window.show();APP.processEvents();picker.setCurrentIndex(0)
        before=picker.currentFont().family();changes=[];picker.currentFontChanged.connect(lambda font:changes.append(font.family()))
        self.assertGreater(picker.count(),1)
        picker.showPopup();APP.processEvents();view=picker.view()
        self.check_surface(picker._popup,tokens,'graphite-font-picker')
        self.assertLessEqual(view.visualRect(view.currentIndex()).height(),32)
        QTest.keyClick(view,Qt.Key.Key_Down);QTest.keyClick(view,Qt.Key.Key_Escape)
        self.assertEqual(picker.currentFont().family(),before);self.assertEqual(changes,[])
        picker.showPopup();QTest.keyClick(view,Qt.Key.Key_Down);QTest.keyClick(view,Qt.Key.Key_Return)
        self.assertNotEqual(picker.currentFont().family(),before);self.assertEqual(len(changes),1)

    def test_frosted_surface_softens_underlying_separator_and_has_one_border(self):
        window,tokens=self.host(width=420);window.resize(420,360)
        line=QFrame(window);line.setGeometry(0,177,420,2);line.setStyleSheet('background:white;')
        window.show();APP.processEvents()
        popup=QFrame(window,Qt.WindowType.Popup|Qt.WindowType.FramelessWindowHint)
        popup.setGeometry(window.mapToGlobal(QPoint(60,100)).x(),window.mapToGlobal(QPoint(60,100)).y(),240,160)
        popup.setPalette(window.palette());surface=PopupSurface(popup,window)
        popup.show();APP.processEvents()
        try:
            image=self.check_surface(popup,tokens,'graphite-softened-separator')
            # A raw two-pixel white line through a 240/255 tint would leave
            # about 12 levels of contrast. The backdrop must soften it before
            # composition, rather than letting a sharp line leak through.
            values=[image.pixelColor(120,y).lightness() for y in range(60,96)]
            self.assertLessEqual(max(values)-min(values),3)
            self.assertIsNotNone(surface.backdrop)
            border=QColor(tokens['divider'])
            self.assertEqual(image.pixelColor(120,159).name(),border.name())
        finally:popup.close()


if __name__=='__main__':unittest.main()
