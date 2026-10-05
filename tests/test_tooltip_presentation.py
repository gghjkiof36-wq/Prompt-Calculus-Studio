"""Native Qt tooltip timing/content remains, with scoped rounded presentation."""
import os
import unittest
from pathlib import Path
from PySide6.QtCore import QElapsedTimer,QEvent,QPoint,Qt
from PySide6.QtGui import QFontDatabase,QHelpEvent,QColor,QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QFrame,QListWidget,QListWidgetItem,QPushButton,QStyleFactory,QToolTip,QVBoxLayout
from prompt_calculus_studio.core import DEFAULT_SETTINGS
from prompt_calculus_studio.theme import stylesheet,widget_palette,visual_tokens
from prompt_calculus_studio.tooltips import install_tooltip_styling,redundant_hint

APP=QApplication.instance() or QApplication([])
if APP.platformName()=='offscreen':
    for name in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
install_tooltip_styling()


class TooltipTests(unittest.TestCase):
    def setUp(self):
        self.panel=QFrame();self.panel.state={'settings':dict(DEFAULT_SETTINGS,material='solid')}
        self.panel.setObjectName('ContentSurface');self.panel.resize(640,180)
        self.button=QPushButton('媒體庫');self.button.setFixedSize(150,40)
        body=QVBoxLayout(self.panel);body.addWidget(self.button);body.addStretch()
        self.apply('graphite');self.panel.show();QTest.qWait(20)

    def tearDown(self):
        QToolTip.hideText();self.wait_for_hint(False);self.panel.close();APP.processEvents()

    def apply(self,palette):
        self.panel.state['settings']['visual_palette']=palette
        self.panel.setPalette(widget_palette(self.panel.state['settings']))
        self.panel.setStyleSheet(stylesheet(self.panel.state['settings']))

    @staticmethod
    def visible_hint():
        return next((w for w in APP.topLevelWidgets() if w.inherits('QTipLabel') and w.isVisible()),None)

    def wait_for_hint(self,visible,timeout=1500):
        # Native hideText is delayed and its 300 ms timer may run after 320 ms
        # on a busy host. Visibility is the contract: Qt may retain text while
        # the label is hidden, and the next hint can legitimately reuse it.
        timer=QElapsedTimer();timer.start()
        while timer.elapsed()<timeout:
            APP.processEvents()
            tip=self.visible_hint()
            if (tip is not None)==visible:
                return self.visible_hint()
            QTest.qWait(10)
        return self.visible_hint()

    def help(self,text):
        QToolTip.hideText()
        self.assertIsNone(self.wait_for_hint(False),'previous native hint did not hide')
        self.button.setToolTip(text)
        center=self.button.rect().center()
        APP.sendEvent(self.button,QHelpEvent(QEvent.Type.ToolTip,center,self.button.mapToGlobal(center)))
        return self.wait_for_hint(not redundant_hint(self.button))

    def assert_readable(self,tip,owner):
        rendered=tip.grab().toImage()
        settings=dict(owner.state['settings'],**getattr(owner,'appearance_preview',{}))
        tokens=visual_tokens(settings)
        foreground=QColor(tokens['text'])
        background=QColor(tokens['raised']);ink=foreground.getRgb()[:3]
        # Fractional DPR antialiases narrow strokes without necessarily leaving
        # a pixel equal to the exact ink. Require pixels close to the intended
        # foreground, well away from the background and muted text colors.
        tolerance=max(abs(a-b) for a,b in zip(ink,background.getRgb()[:3]))*.2
        dpr=rendered.devicePixelRatio()
        matching=sum(max(abs(a-b) for a,b in zip(rendered.pixelColor(x,y).getRgb()[:3],ink))<=tolerance
                     for y in range(round(4*dpr),rendered.height()-round(4*dpr))
                     for x in range(round(8*dpr),rendered.width()-round(8*dpr)))
        self.assertGreater(matching,20,'hint has no readable glyphs')
        self.assertEqual(tip.palette().color(QPalette.ColorGroup.Inactive,QPalette.ColorRole.ToolTipBase),
                         QColor(tokens['raised']))
        return rendered

    def test_visible_button_label_does_not_repeat_but_clipped_and_icon_only_hints_remain(self):
        self.assertIsNone(self.help('媒體庫'))
        self.assertTrue(redundant_hint(self.button))
        self.button.setText('');self.assertIsNotNone(self.help('媒體庫'))
        self.button.setText('媒體庫');self.button.setFixedWidth(35)
        self.assertIsNotNone(self.help('媒體庫'))
        self.button.setFixedWidth(150)
        self.assertIsNotNone(self.help('開啟媒體庫 · Ctrl+2'))

    def test_native_hint_uses_owner_colors_round_mask_and_wraps_long_text(self):
        self.button.setText('')
        for palette in ('graphite','paper','mist'):
            self.apply(palette)
            for kind,text in (('short','程式碼審查'),('long','完整圖片名稱：'+('柔光人像與場景測試_'*12)+'.png')):
                with self.subTest(palette=palette,kind=kind):
                    tip=self.help(text);self.assertIsNotNone(tip)
                    self.assertEqual(QToolTip.text(),text)
                    self.assertLessEqual(tip.width(),480)
                    self.assertFalse(tip.mask().contains(QPoint(0,0)))
                    self.assertTrue(tip.mask().contains(tip.rect().center()))
                    if kind=='long':self.assertGreater(tip.height(),tip.fontMetrics().height()*2)
                    else:self.assertLessEqual(tip.height(),tip.fontMetrics().height()+20)
                    # Check painted glyphs, not just the stylesheet: native Qt
                    # reuses its label and can retain a previous theme's text.
                    self.assert_readable(tip,self.panel)
                    folder=os.environ.get('PCS_TOOLTIP_QA_DIR')
                    if folder:
                        out=Path(folder);out.mkdir(parents=True,exist_ok=True)
                        tip.grab().save(str(out/f'{palette}-{kind}.png'))

    def test_moving_between_controls_reuses_tip_without_shrinking(self):
        self.button.setText('')
        next_button=QPushButton('',self.panel);next_button.setGeometry(190,10,150,40)
        next_button.setToolTip('切換清單');next_button.show()
        redundant=QPushButton('媒體庫',self.panel);redundant.setGeometry(380,10,150,40)
        redundant.setToolTip('媒體庫');redundant.show()
        for size in (11,18):
            self.panel.state['settings']['ui_size']=size;self.apply('mist')
            first=self.help('最近生成');self.assertIsNotNone(first)
            expected=(first.height(),first.fontMetrics().height())
            for target,text in ((next_button,'切換清單'),(self.button,'最近生成'),(next_button,'切換清單')):
                target.setToolTip(text);center=target.rect().center()
                QTest.mouseMove(target,center)
                APP.sendEvent(target,QHelpEvent(QEvent.Type.ToolTip,center,target.mapToGlobal(center)))
                tip=self.wait_for_hint(True);self.assertIsNotNone(tip)
                self.assertEqual((tip.height(),tip.fontMetrics().height()),expected)
                self.assertEqual(QToolTip.text(),text)
            center=redundant.rect().center()
            APP.sendEvent(redundant,QHelpEvent(QEvent.Type.ToolTip,center,redundant.mapToGlobal(center)))
            self.wait_for_hint(False)
            self.assertFalse(any(w.isVisible() for w in APP.topLevelWidgets() if w.inherits('QTipLabel')),
                             (size,redundant_hint(redundant),QToolTip.text(),redundant.fontMetrics().horizontalAdvance('媒體庫')))

    def test_medium_hint_stays_one_line_when_reused_across_owners_and_sizes(self):
        self.button.setText('')
        other=QFrame();other.state={'settings':dict(DEFAULT_SETTINGS,material='solid')}
        other.resize(640,180)
        other_button=QPushButton('');other_button.setFixedSize(150,40)
        body=QVBoxLayout(other);body.addWidget(other_button);body.addStretch()
        other.show()
        text='同一段共用提示文字'
        QToolTip.hideText();self.assertIsNone(self.wait_for_hint(False))
        try:
            for index,(size,palette) in enumerate((size,palette) for size in (11,18) for palette in ('graphite','paper','mist')):
                owner,target=(self.panel,self.button) if index%2==0 else (other,other_button)
                owner.state['settings'].update(ui_size=size,visual_palette=palette)
                owner.setPalette(widget_palette(owner.state['settings']))
                owner.setStyleSheet(stylesheet(owner.state['settings']))
                APP.processEvents()
                target.setToolTip(text);center=target.rect().center()
                # Do not hide here: Qt may keep its label for the next owner
                # even when the text is unchanged. Owner styling must refresh.
                APP.sendEvent(target,QHelpEvent(QEvent.Type.ToolTip,center,target.mapToGlobal(center)))
                tip=self.wait_for_hint(True);self.assertIsNotNone(tip)
                natural=tip.fontMetrics().horizontalAdvance(text)
                with self.subTest(size=size,palette=palette):
                    self.assertEqual(QToolTip.text(),text)
                    self.assertLess(natural+34,480)
                    self.assertGreaterEqual(tip.width(),natural+20)
                    self.assertLessEqual(tip.height(),tip.fontMetrics().height()+20)
                    self.assertLess(tip.fontMetrics().height(),target.fontMetrics().height())
                    self.assertGreaterEqual(tip.font().pixelSize(),13 if size==11 else 18)
                    self.assert_readable(tip,owner)
                    folder=os.environ.get('PCS_TOOLTIP_QA_DIR')
                    if folder:
                        out=Path(folder);out.mkdir(parents=True,exist_ok=True)
                        tip.grab().save(str(out/f'reuse-{palette}-{size}.png'))
        finally:
            QToolTip.hideText();self.wait_for_hint(False);other.close();APP.processEvents()

    def test_width_boundary_accounts_for_native_tooltip_margins(self):
        self.panel.state['settings']['ui_size']=18;self.apply('paper')
        self.button.setText('')
        tip=self.help('測試')
        text='測'*(465//tip.fontMetrics().horizontalAdvance('測'))
        while tip.fontMetrics().horizontalAdvance(text+'w')<=470:text+='w'
        tip=self.help(text);self.assertIsNotNone(tip)
        # Glyphs alone fit the limit, but their horizontal breathing room does
        # not. The last glyph must wrap instead of being clipped.
        self.assertLessEqual(tip.fontMetrics().horizontalAdvance(text),480)
        self.assertTrue(tip.wordWrap())
        self.assertGreater(tip.height(),tip.fontMetrics().height()+20)
        self.assertLessEqual(tip.sizeHint().width(),tip.maximumWidth())
        self.assertLessEqual(tip.width(),480)
        self.assertEqual(QToolTip.text(),text)
        folder=os.environ.get('PCS_TOOLTIP_QA_DIR')
        if folder:
            out=Path(folder);out.mkdir(parents=True,exist_ok=True)
            tip.grab().save(str(out/'boundary-paper-18.png'))

    def test_reused_hint_is_complete_before_the_next_event_loop_turn(self):
        self.button.setText('')
        next_button=QPushButton('',self.panel)
        next_button.setGeometry(190,10,150,40);next_button.show()
        for size in (11,18):
            self.panel.state['settings']['ui_size']=size
            for palette in ('graphite','paper','mist'):
                self.apply(palette)
                self.help('使用介面')
                for target,text in ((next_button,'介面個人化'),(self.button,'使用介面'),
                                    (next_button,'介面個人化'),(next_button,'介面個人化')):
                    target.setToolTip(text);center=target.rect().center()
                    APP.sendEvent(target,QHelpEvent(QEvent.Type.ToolTip,center,target.mapToGlobal(center)))
                    # No processEvents/wait here: the former queued styler
                    # allowed native reuse to display an intermediate frame.
                    tip=self.visible_hint();self.assertIsNotNone(tip)
                    self.assertEqual(QToolTip.text(),text)
                    before=(tip.geometry(),tip.font(),tip.mask())
                    self.assert_readable(tip,self.panel)
                    APP.processEvents()
                    self.assertEqual((tip.geometry(),tip.font(),tip.mask()),before)
                    self.assert_readable(tip,self.panel)

    def test_item_view_dynamic_hints_keep_native_text_and_keyboard_focus(self):
        listing=QListWidget(self.panel);listing.setGeometry(10,60,400,90)
        first=QListWidgetItem('第一張圖');first.setToolTip('完整圖片名稱：柔光人像.png');listing.addItem(first)
        second=QListWidgetItem('第二張圖');second.setToolTip('完整圖片名稱：室外場景.png');listing.addItem(second)
        listing.show();listing.setFocus();APP.processEvents()
        for item in (first,second,first):
            pos=listing.visualItemRect(item).center();target=listing.viewport()
            APP.sendEvent(target,QHelpEvent(QEvent.Type.ToolTip,pos,target.mapToGlobal(pos)))
            tip=self.visible_hint();self.assertIsNotNone(tip)
            self.assertEqual(QToolTip.text(),item.toolTip())
            self.assertIs(APP.focusWidget(),listing)
            self.assert_readable(tip,self.panel)
        QTest.keyClick(listing,Qt.Key.Key_Down)
        self.assertIs(APP.focusWidget(),listing)

    def test_native_hover_and_brief_leave_keep_the_first_hint_spacing(self):
        self.button.setText('');self.button.setToolTip('全部入口')
        other=QPushButton('',self.panel);other.setGeometry(190,10,150,40)
        other.setToolTip('返回工作區');other.show()
        QToolTip.hideText();self.assertIsNone(self.wait_for_hint(False))
        # Exercise Qt's actual hover timer, including its short reuse delay.
        # Directly dispatched help events alone miss platform style behavior.
        QTest.mouseMove(self.panel,QPoint(620,160));QTest.qWait(30)
        def hover(target):
            QTest.mouseMove(target,target.rect().center())
            timer=QElapsedTimer();timer.start()
            while timer.elapsed()<2000:
                QTest.qWait(10)
                tip=self.visible_hint()
                if tip is not None and QToolTip.text()==target.toolTip():return tip
            self.fail('native hover did not show the requested hint')
        first=hover(self.button);expected=(first.size(),first.contentsMargins(),first.font())
        self.assertGreaterEqual(first.height(),first.fontMetrics().height()+12)
        for target in (other,self.button):
            tip=hover(target)
            self.assertEqual(tip.height(),expected[0].height())
            self.assertEqual(tip.contentsMargins(),expected[1])
            self.assert_readable(tip,self.panel)
        QTest.mouseMove(self.panel,QPoint(620,160));QTest.qWait(40)
        tip=hover(self.button)
        self.assertEqual((tip.size(),tip.contentsMargins(),tip.font()),expected)
        self.assert_readable(tip,self.panel)
        QTest.mouseMove(self.panel,QPoint(620,160))
        self.assertIsNone(self.wait_for_hint(False),'leaving must still dismiss the tooltip')

    def test_long_hint_stays_inside_available_screen_at_lower_right_corner(self):
        self.button.setText('');self.button.setToolTip('長檔名：'+('透明玻璃與柔光場景_'*18)+'.png')
        bounds=self.panel.screen().availableGeometry()
        APP.sendEvent(self.button,QHelpEvent(QEvent.Type.ToolTip,self.button.rect().center(),bounds.bottomRight()-QPoint(2,2)))
        tip=self.visible_hint();self.assertIsNotNone(tip)
        self.assertTrue(bounds.contains(tip.geometry()))
        self.assertLessEqual(tip.width(),480)
        self.assert_readable(tip,self.panel)

    def test_live_appearance_preview_styles_hints_without_saving_settings(self):
        self.button.setText('');saved=dict(self.panel.state['settings'])
        try:
            for size,palette in ((11,'paper'),(18,'mist'),(11,'graphite')):
                self.panel.appearance_preview={'ui_size':size,'visual_palette':palette}
                settings=dict(saved,**self.panel.appearance_preview)
                self.panel.setPalette(widget_palette(settings))
                self.panel.setStyleSheet(stylesheet(settings))
                tip=self.help('預覽介面配色');self.assertIsNotNone(tip)
                self.assert_readable(tip,self.panel)
                self.assertEqual(self.panel.state['settings'],saved)
        finally:
            del self.panel.appearance_preview


@unittest.skipUnless('windows11' in {name.lower() for name in QStyleFactory.keys()},'Windows 11 style unavailable')
class WindowsTooltipTests(TooltipTests):
    def setUp(self):
        original=APP.style().objectName()
        self.addCleanup(lambda:APP.setStyle(original))
        APP.setStyle('windows11')
        super().setUp()


if __name__=='__main__':unittest.main()
