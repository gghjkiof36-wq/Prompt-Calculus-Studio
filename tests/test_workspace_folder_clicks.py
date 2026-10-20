"""Real double-click event sequences must not throttle folder disclosure."""
import unittest
from PySide6.QtCore import QPoint,Qt
from PySide6.QtTest import QTest
from prompt_calculus_studio.workspace_locations import path_key
import test_workspace_shell as fixture


class FolderClickTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp=fixture.WorkspaceShellTests.setUp
    tearDown=fixture.WorkspaceShellTests.tearDown
    events=fixture.WorkspaceShellTests.events

    def test_each_rapid_folder_click_toggles_in_docked_and_floating_sidebar(self):
        folder=self.root/'Folder'
        (folder/'Child.md').write_text('Child',encoding='utf-8')
        self.w.refresh_tree();self.events()
        item=self.w.path_items[path_key(folder)];tree=self.w.tree
        for floating in (False,True):
            if floating:
                self.w.sidebar_preference=False;self.w.sync_sidebar();self.events()
                self.w.sidebar_peek.show_pinned();self.events()
            for branch in (False,True):
                item.setExpanded(False);self.events()
                rect=tree.visualItemRect(item)
                x=rect.left()-tree.indentation()//2 if branch else rect.left()+55
                point=QPoint(x,rect.center().y())
                QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=point)
                self.assertTrue(item.isExpanded(),(floating,branch,'first'))
                # Windows sends DoubleClick for the second press within the
                # configured interval. Repeated mouseClick calls omit it.
                QTest.mouseDClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=point)
                QTest.mouseRelease(tree.viewport(),Qt.MouseButton.LeftButton,pos=point)
                self.assertFalse(item.isExpanded(),(floating,branch,'second'))
                QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=point)
                self.assertTrue(item.isExpanded(),(floating,branch,'third'))
        self.w.sidebar_peek.close()

    def test_file_double_click_still_retains_the_document(self):
        tree=self.w.tree;item=self.w.path_items[path_key(self.root/'Notes.md')]
        point=tree.visualItemRect(item).center()
        QTest.mouseClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=point);self.events()
        tab=self.w.tab_for_path(self.root/'Notes.md');self.assertIsNotNone(tab)
        QTest.mouseDClick(tree.viewport(),Qt.MouseButton.LeftButton,pos=point)
        QTest.mouseRelease(tree.viewport(),Qt.MouseButton.LeftButton,pos=point);self.events()
        self.assertTrue(tab.retained)
        self.assertEqual(len([t for t in self.w.tabs_by_key.values() if t.path==tab.path]),1)


if __name__=='__main__':unittest.main()
