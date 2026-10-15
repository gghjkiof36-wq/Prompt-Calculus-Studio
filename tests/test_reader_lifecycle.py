"""Reader modal closure and separately forced QObject disposal, with real events."""
import copy
import unittest
from PySide6.QtCore import Qt,QTimer,QCoreApplication,QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from prompt_calculus_studio import composition as comp,prompt_groups as groups,prompt_sources as sources
from prompt_calculus_studio.prompt_source_widgets import GroupSourceDialog
import test_prompt_group_ui as prior


class ReaderLifecycleTests(unittest.TestCase):
    setUpClass=classmethod(prior.GroupUITests.setUpClass.__func__)
    tearDown=prior.GroupUITests.tearDown

    def setUp(self):
        prior.GroupUITests.setUp(self);self.w.enter_canvas();created=[]
        a=groups.new_group('First','garden');b=groups.new_group('Second','beach')
        root=comp.node('Lifecycle flow','')
        root.update(material_type='flow',sequence=dict(version=1,mode='increment',start=a['id'],selected=a['id'],groups=[a,b]))
        self.assertTrue(self.w.canvas.commit(lambda s:created.append(sources.add(s,root=root))))
        self.key=created[0];self.panel=self.w.canvas.functions.cards[self.key].panel
        self.before=self.w.canvas.history_projection(self.w.state)

    def test_normal_edit_double_click_and_immediate_or_delayed_close(self):
        for delay in (0,30):
            for repeat in range(3):
                with self.subTest(delay=delay,repeat=repeat):
                    inspected=[]
                    def interact():
                        dialog=self.panel.active_dialog
                        QTimer.singleShot(1500,dialog,dialog.reject)
                        point=dialog.listing.visualItemRect(dialog.listing.item(0)).center()
                        QTest.mouseDClick(dialog.listing.viewport(),Qt.MouseButton.LeftButton,pos=point)
                        inspected.append(dialog.stack.currentIndex())
                        close=next(b for b in dialog.findChildren(QPushButton) if b.accessibleName()=='關閉視窗')
                        if delay:QTimer.singleShot(delay,dialog,lambda:QTest.mouseClick(close,Qt.MouseButton.LeftButton))
                        else:QTest.mouseClick(close,Qt.MouseButton.LeftButton)
                    QTimer.singleShot(0,self.panel,interact);self.panel.edit();QTest.qWait(40)
                    self.assertEqual(inspected,[1]);self.assertIsNone(self.panel.active_dialog)
                    self.assertEqual(self.errors,[])
                    self.assertEqual(self.w.canvas.history_projection(self.w.state),self.before)

    def test_z_forced_delete_cancels_pending_reader_layout(self):
        for repeat in range(4):
            with self.subTest(repeat=repeat):
                dialog=GroupSourceDialog(self.w.canvas,self.key);dialog.show()
                dialog.open_group(dialog.root['sequence']['groups'][0]['id'])
                dialog.reject();dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);QTest.qWait(40)
                self.assertEqual(self.errors,[])
                self.assertEqual(self.w.canvas.history_projection(self.w.state),self.before)

    def test_live_reader_layout_and_edit_apply_still_work(self):
        dialog=GroupSourceDialog(self.w.canvas,self.key);dialog.show();QTest.qWait(30)
        try:
            self.assertGreater(dialog.stack.width(),0)
            key=dialog.root['sequence']['groups'][0]['id'];dialog.open_group(key);QTest.qWait(30)
            self.assertEqual(dialog.stack.currentIndex(),1);self.assertEqual(dialog.preview.toPlainText(),'garden')
            dialog.back();QTest.qWait(30);self.assertEqual(dialog.stack.currentIndex(),0)
            dialog.mode.setCurrentIndex(dialog.mode.findData('decrement'))
            QTest.mouseClick(dialog.apply_button,Qt.MouseButton.LeftButton);QTest.qWait(30)
            self.assertEqual(sources.definitions(self.w.state)[self.key]['root']['sequence']['mode'],'decrement')
            self.w.canvas.undo();self.assertEqual(self.w.canvas.history_projection(self.w.state),self.before)
            self.w.canvas.redo();self.w.persist()
            self.assertEqual(sources.definitions(self.w.store.load())[self.key]['root']['sequence']['mode'],'decrement')
        finally:dialog.reject();dialog.deleteLater();self.app.processEvents()


if __name__=='__main__':unittest.main()
