"""File labels reflect their containing folder without changing tree ownership."""
import os
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from prompt_calculus_studio.workspace_locations import path_key
from prompt_calculus_studio.workspace_shell import WorkspaceShell


class WorkspaceTreeAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
            font = Path('C:/Windows/Fonts') / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-tree-alignment-')
        self.root = Path(self.area.name) / 'files'
        self.folder = self.root / '查詢颱風動態'
        self.nested = self.folder / '資料來源'
        self.nested.mkdir(parents=True)
        self.welcome = self.root / '歡迎使用.md'
        self.note = self.folder / '對話.md'
        self.reference = self.nested / '參考資料.md'
        for path in (self.welcome, self.note, self.reference):
            path.write_text('# ' + path.stem + '\n\n合成測試內容。\n', encoding='utf-8')
        self.originals = {path: path.read_bytes() for path in (self.welcome, self.note, self.reference)}
        self.w = WorkspaceShell(self.root, Path(self.area.name) / 'data')
        self.w.resize(1100, 760)
        self.w.sidebar_preference = True
        self.w.sync_sidebar()
        self.w.show()
        self.events()

    def tearDown(self):
        self.assertTrue(self.w.close())
        self.w.deleteLater()
        self.events()
        self.assertEqual({path: path.read_bytes() for path in self.originals}, self.originals)
        self.area.cleanup()

    def events(self):
        self.app.processEvents()
        QTest.qWait(20)
        self.app.processEvents()

    def item(self, path):
        return self.w.path_items[path_key(path)]

    def label_x(self, path):
        tree = self.w.tree
        index = tree.indexFromItem(self.item(path))
        option = QStyleOptionViewItem()
        option.initFrom(tree)
        option.widget = tree
        option.decorationSize = tree.iconSize()
        option.rect = tree.visualRect(index)
        tree.itemDelegate().initStyleOption(option, index)
        return tree.style().subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, tree).x()

    def test_root_file_aligns_with_root_when_neighboring_folder_opens_and_closes(self):
        tree = self.w.tree
        self.assertIs(self.item(self.welcome).parent(), self.item(self.root))
        self.assertIs(self.item(self.folder).parent(), self.item(self.root))
        self.assertIs(self.item(self.note).parent(), self.item(self.folder))
        for expanded in (False, True, False):
            self.item(self.folder).setExpanded(expanded)
            self.events()
            self.assertEqual(self.label_x(self.welcome), self.label_x(self.root))
            self.assertLess(self.label_x(self.welcome), self.label_x(self.folder))
            self.assertTrue(tree.visualItemRect(self.item(self.welcome)).isValid())
        self.assertEqual(self.item(self.welcome).data(0, Qt.ItemDataRole.UserRole), str(self.welcome))

    def test_nested_files_keep_parent_alignment_and_folder_depth(self):
        self.w.tree.expandAll()
        self.events()
        self.assertEqual(self.label_x(self.note), self.label_x(self.folder))
        self.assertEqual(self.label_x(self.reference), self.label_x(self.nested))
        self.assertEqual(self.label_x(self.folder) - self.label_x(self.root), self.w.tree.indentation())
        self.assertEqual(self.label_x(self.nested) - self.label_x(self.folder), self.w.tree.indentation())

    def test_visible_root_label_opens_correct_file_in_docked_and_floating_sidebar(self):
        tree = self.w.tree
        for floating in (False, True):
            if floating:
                self.w.sidebar_preference = False
                self.w.sync_sidebar()
                self.w.sidebar_peek.show_pinned()
                self.events()
            self.item(self.folder).setExpanded(True)
            self.events()
            self.assertEqual(self.label_x(self.welcome), self.label_x(self.root))
            point = QPoint(self.label_x(self.welcome) + 3, tree.visualItemRect(self.item(self.welcome)).center().y())
            QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.events()
            self.assertEqual(self.w.tabs_by_key[self.w.active_key].path, self.welcome)
        self.w.sidebar_peek.close()

    def test_rendered_expanded_and_collapsed_sidebar(self):
        destination = os.environ.get('PCS_TREE_SCREENSHOTS')
        if not destination:
            self.skipTest('screenshots supplied separately')
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        self.w.tree.expandAll()
        self.events()
        self.assertTrue(self.w.sidebar.grab().save(str(directory / 'tree-expanded.png')))
        self.item(self.folder).setExpanded(False)
        self.events()
        self.assertTrue(self.w.sidebar.grab().save(str(directory / 'tree-collapsed.png')))


if __name__ == '__main__':
    unittest.main()
