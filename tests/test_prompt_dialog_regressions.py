"""Disclosure geometry, hover feedback and the rendered material draft agree."""
import copy
import unittest
from PySide6.QtCore import Qt,QPoint,QPointF,QTimer,QEvent,QObject
from PySide6.QtGui import QMouseEvent,QHelpEvent
from PySide6.QtWidgets import QCheckBox,QWidget,QPushButton,QToolTip
from PySide6.QtTest import QTest
from unittest.mock import patch
from PySide6.QtCore import QRect
from PySide6.QtGui import QScreen
import test_prompt_group_ui as prior
from prompt_calculus_studio import composition as comp, prompt_groups as groups, materials, prompt_sources as sources
from prompt_calculus_studio.text_canvas import NodeDialog
from prompt_calculus_studio.prompt_source_widgets import GroupSourceDialog


class PromptDialogTests(unittest.TestCase):
    setUpClass=classmethod(prior.GroupUITests.setUpClass.__func__)
    def setUp(self):
        # Wide-layout assertions need a wide available screen, not the
        # offscreen platform's default 800x800 desktop. Narrow-window cases
        # still constrain the real parent window below.
        screen=patch.object(QScreen,'availableGeometry',return_value=QRect(0,0,1920,1200))
        screen.start();self.addCleanup(screen.stop)
        prior.GroupUITests.setUp(self)
    tearDown=prior.GroupUITests.tearDown
    create=prior.GroupUITests.create

    def populated(self):
        self.w.resize(1440,1100)
        dialog,ed=self.create();first=ed.sequence['groups'][0]
        first.update(input='modules',modules=[comp.node('自然站立','standing, smile, smile'),comp.node('微笑','smile, (smile:1.2)')])
        ed.sequence['groups'].append(groups.new_group('組合 2','garden, garden'))
        ed.refresh_overview();QTest.qWait(30)
        return dialog,ed

    def cycle(self,dialog,toggle):
        # Disclosure motion now belongs to the centered child surface; its
        # transparent native envelope stays fixed to prevent backing flicker.
        surface=dialog.presentation.surface;closed=QRect(surface.geometry());native=QRect(dialog.geometry())
        for _ in range(3):
            QTest.mouseClick(toggle,Qt.MouseButton.LeftButton)
            QTest.qWait(220)
            self.assertGreater(surface.height(),closed.height())
            self.assertEqual(dialog.geometry(),native,'disclosure must not move or resize the native window')
            QTest.mouseClick(toggle,Qt.MouseButton.LeftButton)
            QTest.qWait(220)
            # Fractional DPI can round the centered child by one logical pixel.
            # Compare every edge to the original, so repeated cycles cannot drift.
            actual=surface.geometry()
            for edge in ('left','top','right','bottom'):
                self.assertLessEqual(abs(getattr(actual,edge)()-getattr(closed,edge)()),1)
            self.assertEqual(dialog.geometry(),native)

    def side_cycle(self,dialog,toggle):
        closed=dialog.geometry();main_width=dialog.panels.main.width()
        def settle():
            for _ in range(60):
                if dialog.panels.motion.state()==dialog.panels.motion.State.Stopped:return
                QTest.qWait(10)
            self.fail('side-card animation did not finish')
        for _ in range(3):
            QTest.mouseClick(toggle,Qt.MouseButton.LeftButton)
            settle()
            self.assertEqual(dialog.height(),closed.height())
            self.assertEqual(dialog.panels.main.width(),main_width)
            self.assertEqual(dialog.geometry(),closed,'native window must not resize during a side-card transition')
            self.assertEqual(dialog.panels.side.x()-dialog.panels.main.geometry().right()-1,14)
            QTest.mouseClick(toggle,Qt.MouseButton.LeftButton)
            settle()
            self.assertEqual(dialog.geometry(),closed)
            self.assertFalse(dialog.panels.side.isVisible())

    def test_close_during_side_motion_stops_timer_and_reopen_is_collapsed(self):
        dialog=self.page.new_material(); QTest.qWait(30)
        dialog.pages.details_toggle.setChecked(True)
        self.assertEqual(dialog.panels.motion.state(), dialog.panels.motion.State.Running)
        dialog.reject()
        self.assertEqual(dialog.panels.motion.state(), dialog.panels.motion.State.Stopped)
        reopened=self.page.new_material(); QTest.qWait(30)
        self.assertFalse(reopened.panels.opened)
        self.assertFalse(reopened.panels.side.isVisible())
        reopened.reject()

    def test_overview_and_group_collapse_return_to_content_height(self):
        dialog,ed=self.populated()
        def listing_fits():
            # The taller default leaves room for more groups; existing rows
            # must remain fully visible without an unnecessary scrollbar.
            self.assertTrue(ed.listing.isVisible())
            self.assertGreaterEqual(ed.listing.height(),ed.listing.sizeHint().height())
            self.assertGreaterEqual(dialog.panels.main.height(),680)
            self.assertGreater(ed.listing.count(),0)
            for index in range(ed.listing.count()):
                self.assertTrue(ed.listing.viewport().rect().contains(ed.listing.visualItemRect(ed.listing.item(index))))
            self.assertEqual(ed.listing.verticalScrollBar().maximum(),0)
        self.assertFalse(hasattr(ed,'overview_preview_toggle'))
        self.assertEqual([c for c in dialog.pages.findChildren(QCheckBox) if c.isVisible()],[])
        listing_fits()
        ed.edit_group(ed.sequence['groups'][0]['id']);QTest.qWait(30)
        self.side_cycle(dialog,ed.preview_toggle)
        ed.back();QTest.qWait(30)
        listing_fits()
        self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0)
        dialog.reject()

    def test_excludes_visibility_and_height_change_together_and_keep_edits(self):
        self.w.resize(1440,1100)
        root=comp.node('自然站立','standing, smile, smile')
        dialog=NodeDialog(self.w,root);dialog.show();QTest.qWait(40)
        self.cycle(dialog,dialog.excludes_toggle)
        dialog.excludes_toggle.setChecked(True)
        dialog.excludes.setPlainText('standing');dialog.dedupe.setChecked(True)
        dialog.excludes_toggle.setChecked(False)
        self.assertEqual(comp.render(dict(root,**dialog.values())),'standing, smile')
        self.assertEqual(dialog.values()['excludes'],['standing'])
        self.assertTrue(dialog.dedupe.isHidden() or not dialog.dedupe.isVisible())
        self.assertTrue(dialog.enabled.isVisible());dialog.reject()

    def test_round_dialog_expansion_stays_inside_available_screen(self):
        root=comp.node('運動')
        a=groups.new_group('跑步','running, stadium');b=groups.new_group('跳躍','jumping, stadium')
        root['sequence']=dict(version=1,mode='increment',selected=a['id'],start=a['id'],groups=[a,b])
        self.w.resize(1440,900);dialog=NodeDialog(self.w,root);dialog.show();QTest.qWait(30)
        for _ in range(3):
            dialog.excludes_toggle.setChecked(True);QTest.qWait(20)
            bounds=dialog.screen().availableGeometry().adjusted(20,20,-20,-20)
            self.assertTrue(bounds.contains(dialog.geometry()),str(dialog.geometry()))
            self.assertGreaterEqual(self.w.mapToGlobal(self.w.rect().bottomRight()).y()-dialog.geometry().bottom(),12)
            dialog.excludes_toggle.setChecked(False);QTest.qWait(20)
        dialog.reject()

    def test_small_window_scrolls_without_losing_controls_or_footer(self):
        dialog,ed=self.populated();self.w.resize(1040,560)
        dialog.fit_page();QTest.qWait(20)
        ed.edit_group(ed.sequence['groups'][0]['id']);QTest.qWait(20)
        for _ in range(2):
            ed.preview_toggle.setChecked(True);QTest.qWait(20)
            self.assertLessEqual(dialog.height(),self.w.height()-24)
            dialog.form_scroll.ensureWidgetVisible(ed.preview_toggle);QTest.qWait(20)
            point=ed.preview_toggle.mapTo(dialog.form_scroll.viewport(),QPoint(8,8))
            self.assertTrue(dialog.form_scroll.viewport().rect().contains(point))
            self.assertTrue(dialog.rect().contains(dialog.done_button.mapTo(dialog,dialog.done_button.rect().center())))
            ed.preview_toggle.setChecked(False);QTest.qWait(20)
        dialog.reject()

    def test_dedupe_preview_save_reopen_and_prose_match_rendered_output(self):
        dialog,ed=self.populated()
        original=copy.deepcopy(ed.sequence)
        ed.edit_group(ed.sequence['groups'][0]['id']);QTest.qWait(20)
        self.assertEqual(ed.preview.toPlainText(),'standing, smile, smile, smile, (smile:1.2)')
        def enable_dedupe():
            node=ed.active_node_dialog;node.excludes_toggle.setChecked(True)
            QTest.mouseClick(node.dedupe,Qt.MouseButton.LeftButton,pos=QPoint(8,node.dedupe.height()//2));node.save()
        QTimer.singleShot(0,enable_dedupe);ed.open_module(ed.current_group['modules'][0]['id'])
        expected='standing, smile, smile, (smile:1.2)'
        self.assertEqual(ed.preview.toPlainText(),expected)
        self.assertEqual(ed.current_group['modules'][0]['prompt'],original['groups'][0]['modules'][0]['prompt'])
        ed.back()
        self.assertEqual(comp.render(dialog._value()['composition']),expected)
        ident=dialog.initial_id;QTest.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton);QTest.qWait(20)
        saved=materials.item(self.w.state,ident)
        self.assertEqual(comp.render(saved['composition']),expected)
        dialog=self.page.edit_material(ident);ed=dialog.pages.group_editor;QTest.qWait(20)
        self.assertFalse(hasattr(dialog.pages,'dedupe'));self.assertFalse(hasattr(ed,'overview_preview'))
        ed.edit_group(ed.sequence['groups'][1]['id']);ed.direct_kind.setCurrentIndex(ed.direct_kind.findData('text'))
        self.assertEqual(ed.preview.toPlainText(),'garden, garden','prose must not be treated as tags')
        dialog.reject()

    def test_highlight_follows_pointer_and_keyboard_without_sticky_selection(self):
        dialog,ed=self.populated();ed.edit_group(ed.sequence['groups'][0]['id']);QTest.qWait(30)
        listing=ed.modules;first=listing.visualItemRect(listing.item(0));second=listing.visualItemRect(listing.item(1))
        def colors():
            image=listing.viewport().grab().toImage()
            return [image.pixelColor(8,rect.center().y()).name() for rect in (first,second)]
        def hover(pos):
            # Offscreen QTest.mouseMove changes the cursor but does not deliver a native move.
            event=QMouseEvent(QEvent.Type.MouseMove,QPointF(pos),QPointF(listing.viewport().mapToGlobal(pos)),
                Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(listing.viewport(),event);QTest.qWait(20)
        QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=first.center());QTest.qWait(20)
        hover(second.center())
        a,b=colors();self.assertNotEqual(a,b)
        self.app.sendEvent(listing.viewport(),QEvent(QEvent.Type.Leave))
        QTest.qWait(20);self.assertEqual(*colors(),'mouse selection must not remain highlighted')
        listing.setFocus(Qt.FocusReason.TabFocusReason);QTest.keyClick(listing,Qt.Key.Key_Down);QTest.qWait(20)
        self.assertNotEqual(*colors(),'keyboard navigation retains visible focus')
        hover(first.center())
        self.assertEqual(colors(),[b,a]);dialog.reject()

    def test_read_group_preview_uses_same_stable_collapse(self):
        dialog,ed=self.populated();root=dialog._value()['composition'];dialog.reject();keys=[]
        self.w.canvas.commit(lambda state:keys.append(sources.add(state,root=root)))
        self.w.enter_canvas();dialog=GroupSourceDialog(self.w.canvas,keys[0]);dialog.show()
        dialog.open_group(root['sequence']['groups'][0]['id']);QTest.qWait(40)
        self.side_cycle(dialog,dialog.preview_toggle)
        self.assertEqual(dialog.preview.toPlainText(),comp.render(root));dialog.reject()

    def test_single_material_grows_right_without_compressing_prompt(self):
        self.w.resize(1440,1100);dialog=self.page.new_material();QTest.qWait(40)
        self.assertLessEqual(dialog.panels.main.width(),600);self.assertGreaterEqual(dialog.prompt.height(),280)
        original=dialog.geometry();prompt_width=dialog.prompt.width();prompt_height=dialog.prompt.height();name_width=dialog.name.width()
        main_position=dialog.panels.main.mapToGlobal(QPoint())
        closed_icon=dialog.pages.details_toggle.icon().cacheKey()
        QTest.mouseClick(dialog.pages.details_toggle,Qt.MouseButton.LeftButton);QTest.qWait(260)
        self.assertEqual(dialog.geometry(),original)
        self.assertGreater(dialog.panels.side.width(),300)
        self.assertLess(dialog.panels.main.mapToGlobal(QPoint()).x(),main_position.x());self.assertAlmostEqual(dialog.prompt.width(),prompt_width,delta=2)
        self.assertEqual(dialog.name.width(),name_width)
        self.assertEqual(dialog.panels.side.x()-dialog.panels.main.geometry().right()-1,14)
        self.assertGreaterEqual(dialog.prompt.height(),prompt_height)
        self.assertNotEqual(dialog.pages.details_toggle.icon().cacheKey(),closed_icon)
        self.assertTrue(dialog.pages.details.isVisible())
        QTest.mouseClick(dialog.pages.details_toggle,Qt.MouseButton.LeftButton);QTest.qWait(260)
        self.assertEqual(dialog.width(),original.width());self.assertEqual(dialog.prompt.width(),prompt_width)
        self.assertFalse(dialog.pages.details.isVisible());dialog.reject()

    def test_picker_adds_once_and_returns_to_group_immediately(self):
        dialog,ed=self.populated();ed.edit_group(ed.sequence['groups'][0]['id']);ed.open_picker();QTest.qWait(30)
        ed.search.setText('Bravo');QTest.qWait(20);before=len(ed.current_group['modules'])
        listing=ed.candidates;item=listing.item(0)
        QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=listing.visualItemRect(item).center())
        self.assertEqual(ed.stack.currentIndex(),1);self.assertEqual(len(ed.current_group['modules']),before+1)
        ed.pick_material(self.ids[0]);self.assertEqual(len(ed.current_group['modules']),before+1)
        ed.open_picker();QTest.qWait(20);self.assertEqual(ed.search.text(),'Bravo')
        listing.setCurrentRow(0);QTest.keyClick(listing,Qt.Key.Key_Return)
        self.assertEqual(ed.stack.currentIndex(),1);self.assertEqual(len(ed.current_group['modules']),before+2)
        self.assertFalse(ed.listing.item(0).toolTip());self.assertFalse(ed.modules.item(0).toolTip());dialog.reject()

    def test_rounds_keep_shared_location_and_legacy_root_setting_is_preserved(self):
        dialog,ed=self.populated()
        for group,action in zip(ed.sequence['groups'],('running','jumping')):
            group.update(input='modules',modules=[dict(comp.node(action,f'{action}, stadium, stadium'),deduplicate=True)])
        ed.refresh_overview()
        for group,action in zip(ed.sequence['groups'],('running','jumping')):
            ed.edit_group(group['id']);self.assertEqual(ed.preview.toPlainText(),f'{action}, stadium')
        # Hiding the redundant outer control must not rewrite existing saved options.
        dialog.pages.root['deduplicate']=True
        self.assertTrue(dialog._value()['composition']['deduplicate']);dialog.reject()

    def test_direct_input_preview_collapse_restores_editor_height(self):
        dialog,ed=self.populated();ed.edit_group(ed.sequence['groups'][1]['id']);QTest.qWait(30)
        height=ed.direct_text.height();self.side_cycle(dialog,ed.preview_toggle)
        self.assertEqual(ed.direct_text.height(),height)
        self.assertEqual(ed.direct_text.toPlainText(),'garden, garden');dialog.reject()

    def test_group_advanced_only_has_relevant_fields_and_adjacent_excludes(self):
        dialog,ed=self.populated();dialog.form.lookup.setChecked(True)
        dialog.pages.show_group_details();QTest.qWait(30)
        self.assertFalse(dialog.form.prompt.lookup_active())
        self.assertFalse(dialog.form.lookup.isVisible());self.assertFalse(dialog.form.kind.isVisible())
        self.assertEqual([dialog.pages.details.tabText(i) for i in range(dialog.pages.details.count())],['資訊','排除 Tag','預覽圖'])
        dialog.pages.details.setCurrentIndex(1);QTest.qWait(30)
        pane=dialog.pages.details.currentWidget();title=pane.layout().itemAt(0).widget()
        self.assertLessEqual(dialog.form.excludes.y()-title.geometry().bottom(),12)
        self.assertEqual(dialog.form.excludes.placeholderText(),'')
        self.assertTrue(dialog.history_button.isVisible());dialog.pages.close_group_details();QTest.qWait(260)
        self.assertEqual(dialog.pages.stack.currentIndex(),1);dialog.reject()

    def test_history_selects_latest_and_can_restore_group_content(self):
        from prompt_calculus_studio.prompt_editor import HistoryPanel
        dialog,ed=self.populated();ident=dialog.initial_id
        QTest.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton);QTest.qWait(20)
        dialog=self.page.edit_material(ident);ed=dialog.pages.group_editor
        ed.edit_group(ed.sequence['groups'][1]['id']);ed.direct_text.setPlainText('rainy street')
        ed.back();QTest.mouseClick(dialog.save_button,Qt.MouseButton.LeftButton);QTest.qWait(20)
        panel=HistoryPanel(self.w,ident);panel.resize(820,420);panel.show();QTest.qWait(20)
        self.assertEqual(panel.listing.currentRow(),0);self.assertIn('rainy street',panel.detail.toPlainText())
        self.assertIn('garden, garden',panel.detail.toPlainText());self.assertFalse(panel.targets.isVisible())
        panel.restore(True)
        self.assertEqual(materials.item(self.w.state,ident)['composition']['sequence']['groups'][1]['text'],'garden, garden')
        panel.close();panel.deleteLater()

    def test_opening_material_and_module_editors_never_shows_detached_controls(self):
        shown=[]
        class ShowProbe(QObject):
            def eventFilter(probe,widget,event):
                if isinstance(widget,QWidget) and widget.isWindow() and event.type()==QEvent.Type.Show:
                    shown.append(type(widget).__name__)
                return False
        probe=ShowProbe();self.app.installEventFilter(probe)
        try:
            dialog=self.page.new_material();QTest.qWait(30)
            self.assertEqual(shown,['MaterialEditDialog'])
            self.assertNotIn('展開長文',[b.text() for b in dialog.findChildren(QPushButton)])
            dialog.pages.type_picker.setCurrentIndex(1);QTest.qWait(20)
            self.assertEqual(shown,['MaterialEditDialog']);dialog.reject();QTest.qWait(20)
            # Follow the actual library double-click entry, for every visible item.
            for i in range(self.page.listing.count()):
                listing=self.page.listing;listing.scrollToItem(listing.item(i));QTest.qWait(10)
                shown.clear();point=listing.visualItemRect(listing.item(i)).center()
                QTest.mouseClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=point)
                QTest.mouseDClick(listing.viewport(),Qt.MouseButton.LeftButton,pos=point)
                self.assertIsNotNone(self.page.active_dialog,'the second click opens immediately, without another click or timeout')
                QTest.qWait(30)
                self.assertEqual(shown,['MaterialEditDialog'])
                self.assertIsNotNone(self.page.active_dialog);self.page.active_dialog.reject();QTest.qWait(10)
            shown.clear()
            dialog=NodeDialog(self.w,comp.node('模組','smile'));dialog.show();QTest.qWait(20)
            dialog.excludes_toggle.setChecked(True);QTest.qWait(20)
            self.assertEqual(shown,['NodeDialog']);dialog.reject()
        finally:self.app.removeEventFilter(probe)

    def test_material_format_tracks_type_and_reader_only_lists_groups(self):
        dialog=self.page.new_material();dialog.name.setText('單模組');dialog.prompt.setPlainText('smile')
        self.assertEqual(dialog.form.kind.currentData(),'tags');single=dialog.initial_id;self.assertTrue(dialog.save())
        dialog=self.page.new_material();dialog.name.setText('流程 A');dialog.pages.type_picker.setCurrentIndex(dialog.pages.type_picker.findData('flow'))
        dialog.pages.show_group_details();QTest.qWait(20)
        self.assertTrue(dialog.pages.group_format.isVisible());self.assertEqual(dialog.pages.group_format.text(),'流程')
        grouped=dialog.initial_id;self.assertTrue(dialog.save())
        keys=[];self.w.canvas.commit(lambda state:keys.append(sources.add(state)))
        reader=GroupSourceDialog(self.w.canvas,keys[0]);reader.show();QTest.qWait(20)
        reader.open_picker()
        found={reader.candidates.item(i).data(Qt.ItemDataRole.UserRole) for i in range(reader.candidates.count())}
        self.assertIn(grouped,found);self.assertNotIn(single,found);reader.reject()
        # Existing non-tag documents keep their format when opened and saved.
        prose=[];self.w.canvas.commit(lambda state:prose.append(materials.save(state,dict(name='文章',prompt='A sentence, with a comma.',content_kind='text',module=self.cat))))
        dialog=self.page.edit_material(prose[0]);self.assertEqual(dialog.form.kind.currentData(),'text');dialog.reject()

    def test_editor_and_nested_dialog_suppress_floating_hints(self):
        self.page.new_button.setToolTip('新增')
        for control in (self.page.new_button,):
            point=control.rect().center();self.app.sendEvent(control,QHelpEvent(QEvent.Type.ToolTip,point,control.mapToGlobal(point)))
            self.assertFalse(QToolTip.isVisible())
        dialog=self.page.new_material();QTest.qWait(20)
        close=dialog.findChild(QPushButton,'DialogClose');point=close.rect().center()
        self.app.sendEvent(close,QHelpEvent(QEvent.Type.ToolTip,point,close.mapToGlobal(point)))
        self.assertFalse(QToolTip.isVisible());self.assertTrue(close.accessibleName());dialog.reject()

    def test_drag_card_body_reorders_and_group_requires_double_click(self):
        from test_editor_reorder import EditorReorderTests
        dialog,ed=self.populated();before=[g['id'] for g in ed.sequence['groups']]
        EditorReorderTests.drag(self,ed.listing,before[1],before[0])
        self.assertEqual(ed.listing.order_ids(),before[::-1]);self.assertEqual(ed.stack.currentIndex(),0)
        self.assertTrue(dialog.pages.group_details.isVisible())
        self.assertLess(dialog.pages.group_details.mapTo(dialog,QPoint()).y(),ed.mapTo(dialog,QPoint()).y())
        point=ed.listing.regions(ed.listing.visualItemRect(ed.listing.item(1)))['name'].center()
        QTest.mouseClick(ed.listing.viewport(),Qt.MouseButton.LeftButton,pos=point)
        self.assertEqual(ed.stack.currentIndex(),0)
        QTest.mouseDClick(ed.listing.viewport(),Qt.MouseButton.LeftButton,pos=point);QTest.qWait(20)
        self.assertEqual(ed.stack.currentIndex(),1);self.assertEqual(ed.current_group['id'],before[0])
        modules=[m['id'] for m in ed.current_group['modules']]
        EditorReorderTests.drag(self,ed.modules,modules[1],modules[0])
        self.assertEqual(ed.modules.order_ids(),modules[::-1]);self.assertFalse(ed.inspector.isVisible())
        self.assertEqual(ed.preview.toPlainText(),'smile, (smile:1.2), standing, smile, smile');dialog.reject()

    def test_cancel_new_module_leaves_no_empty_entry(self):
        dialog,ed=self.populated();ed.edit_group(ed.sequence['groups'][0]['id'])
        before=copy.deepcopy(ed.current_group['modules'])
        QTimer.singleShot(0,lambda:ed.active_node_dialog.reject());ed.add_empty()
        self.assertEqual(ed.current_group['modules'],before);dialog.reject()

    def test_separated_panels_animate_reverse_and_obey_reduced_motion(self):
        dialog=self.page.new_material();QTest.qWait(30);panels=dialog.panels
        closed=dialog.geometry();main_start=panels.main.mapToGlobal(QPoint()).x()
        dialog.pages.details_toggle.setChecked(True);QTest.qWait(70)
        middle=panels.main.mapToGlobal(QPoint()).x();side_middle=panels.side.mapToGlobal(QPoint()).x()
        self.assertLess(middle,main_start);self.assertGreater(panels.progress,0);self.assertLess(panels.progress,1)
        QTest.qWait(190)
        self.assertLess(panels.main.mapToGlobal(QPoint()).x(),middle)
        self.assertGreater(panels.side.mapToGlobal(QPoint()).x(),side_middle)
        # Reverse a partly completed movement without leaving a detached panel.
        dialog.pages.details_toggle.setChecked(False);QTest.qWait(60)
        dialog.pages.details_toggle.setChecked(True);QTest.qWait(260)
        self.assertEqual(dialog.name.width(),dialog.prompt.width())
        dialog.pages.details_toggle.setChecked(False);QTest.qWait(260)
        self.assertEqual(dialog.geometry(),closed);self.assertFalse(panels.side.isVisible())
        self.w.state['settings']['reduce_motion']=True
        dialog.pages.details_toggle.setChecked(True)
        self.assertEqual(panels.progress,1.);self.assertEqual(panels.motion.state(),panels.motion.State.Stopped)
        self.w.resize(900,640);QTest.qWait(30)
        self.assertTrue(panels.bounds().contains(dialog.geometry()))
        self.assertTrue(panels.rect().contains(panels.main.geometry()))
        self.assertTrue(panels.rect().contains(panels.side.geometry()))
        self.assertGreater(dialog.pages.details_toggle.y(),dialog.prompt.geometry().bottom())
        self.assertTrue(dialog.rect().contains(dialog.save_button.mapTo(dialog,dialog.save_button.rect().bottomRight())))
        dialog.reject()

    def test_preview_image_has_its_own_space_and_survives_save_undo_reopen(self):
        from PySide6.QtGui import QImage,QColor
        from pathlib import Path
        source=Path(self.folder.name)/'fixture.png';picture=QImage(800,600,QImage.Format.Format_RGB32)
        picture.fill(QColor('#758d80'));self.assertTrue(picture.save(str(source)))
        dialog=self.page.edit_material(self.ids[0]);dialog.pages.details_toggle.setChecked(True)
        dialog.pages.details.setCurrentIndex(2);dialog.form.receive_image(str(source));QTest.qWait(260)
        preview=dialog.form.preview
        self.assertGreaterEqual(preview.width(),360);self.assertGreaterEqual(preview.height(),300)
        self.assertAlmostEqual(preview.pixmap().width()/preview.pixmap().height(),4/3,delta=.01)
        self.assertTrue(dialog.panels.side.rect().contains(preview.mapTo(dialog.panels.side,preview.rect().bottomRight())))
        path=dialog.form.preview_path;self.assertTrue(dialog.save());self.app.processEvents()
        self.assertEqual(materials.item(self.w.state,self.ids[0])['preview'],path)
        self.w.canvas.undo();self.assertFalse(materials.item(self.w.state,self.ids[0]).get('preview'))
        self.w.canvas.redo();dialog=self.page.edit_material(self.ids[0])
        self.assertFalse(dialog.form.preview.original.isNull());dialog.form.clear_image();dialog.reject()
        self.assertEqual(materials.item(self.w.state,self.ids[0])['preview'],path)
