"""Repair 5 product regressions, using synthetic content and offscreen input."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from prompt_calculus_studio import composition
from prompt_calculus_studio.canvas_items import CanvasView

APP = QApplication.instance() or QApplication([])
LEFT = Qt.MouseButton.LeftButton


class PromptBoundaryTests(unittest.TestCase):
    def test_join_removes_duplicate_separator_without_editing_source(self):
        root = composition.node('root', '1girl,', children=[composition.node('child', 'red clothes,')], grouped=True)
        before = copy.deepcopy(root)
        self.assertEqual(composition.render(root), '(1girl, red clothes,:1.0)')
        self.assertEqual(root, before)

    def test_final_comma_weight_overlay_and_literal_escape_survive(self):
        root = composition.node('root', 'a,,  ', children=[composition.node('child', 'b,')])
        self.assertEqual(composition.render(root), 'a, b,')
        root['children'][0]['weight'] = 12
        self.assertEqual(composition.render(root), 'a, (b,:1.2)')
        root['prompt'] = r'literal\,'
        self.assertEqual(composition.render(root), r'literal\,, (b,:1.2)')
        root['overlays'] = [composition.node('override', 'c,')]
        self.assertEqual(composition.render(root), 'c,')


class BlankCanvasGestureTests(unittest.TestCase):
    def setUp(self):
        self.canvas = QWidget()
        self.canvas.palette = Mock()
        self.canvas.cards = {}
        self.view = CanvasView(self.canvas)
        self.view.resize(600, 400)
        self.view.show()
        APP.processEvents()
        self.target = self.view.viewport()
        self.point = QPoint(150, 120)

    def tearDown(self):
        self.canvas.close()
        self.canvas.deleteLater()
        APP.processEvents()

    def double_release(self):
        QTest.mouseDClick(self.target, LEFT, pos=self.point)
        self.canvas.palette.assert_not_called()
        QTest.mouseRelease(self.target, LEFT, pos=self.point)
        APP.processEvents()

    def test_two_stationary_clicks_open_once_after_second_release(self):
        QTest.mouseClick(self.target, LEFT, pos=self.point)
        self.double_release()
        self.canvas.palette.assert_called_once()
        self.assertIsNone(self.view.pan)

    def test_drag_then_platform_double_click_does_not_open(self):
        QTest.mousePress(self.target, LEFT, pos=self.point)
        QTest.mouseMove(self.target, self.point + QPoint(40, 0))
        QTest.mouseMove(self.target, self.point)
        QTest.mouseRelease(self.target, LEFT, pos=self.point)
        self.double_release()
        self.canvas.palette.assert_not_called()

    def test_second_press_becoming_drag_cancels_pending_palette(self):
        QTest.mouseClick(self.target, LEFT, pos=self.point)
        QTest.mouseDClick(self.target, LEFT, pos=self.point)
        QTest.mouseMove(self.target, self.point + QPoint(40, 0))
        QTest.mouseRelease(self.target, LEFT, pos=self.point + QPoint(40, 0))
        APP.processEvents()
        self.canvas.palette.assert_not_called()

    def test_expired_first_click_does_not_open(self):
        QTest.mouseClick(self.target, LEFT, pos=self.point)
        self.view.last_blank_click = (self.point, 0)
        self.double_release()
        self.canvas.palette.assert_not_called()


class RecentSheetTests(unittest.TestCase):
    def test_child_overlay_hide_restores_recent_page_once_without_changing_canvas(self):
        from prompt_calculus_studio.window import Window
        from prompt_calculus_studio.recent_overlay import RecentOverlay
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root/'qa') as directory, patch('prompt_calculus_studio.comfy_client.ComfyClient.request'):
            window = Window(Path(directory)/'data')
            window.state['settings']['online'] = False
            window.resize(1000, 700)
            window.show()
            window.enter_canvas()
            APP.processEvents()
            window.display_recovery.stop()
            window.canvas.view.setFocus()
            APP.processEvents()
            transform = window.canvas.view.transform()
            before = copy.deepcopy(window.state)
            original_index = window.tabs.indexOf(window.recent)
            original_title = window.tabs.tabText(original_index)
            original_count = window.tabs.count()
            try:
                window.show_recent_sheet()
                APP.processEvents()
                sheet = window.recent_sheet
                self.assertIsInstance(sheet, RecentOverlay)
                self.assertFalse(sheet.isWindow())
                self.assertIs(sheet.parentWidget(), window.surface_stack)
                self.assertEqual(sheet.geometry(), window.surface_stack.rect())
                self.assertTrue(sheet.isAncestorOf(window.recent))
                self.assertEqual(window.tabs.indexOf(window.recent), -1)
                surface = sheet.surface.geometry()
                self.assertGreater(surface.left(), 0)
                self.assertGreater(surface.top(), 0)
                self.assertLess(surface.right(), sheet.rect().right())
                self.assertLess(surface.bottom(), sheet.rect().bottom())
                finished = Mock()
                sheet.finished.connect(finished)
                sheet.hide()  # Hiding the borrowed child also returns its page.
                sheet.reject()  # A second dismissal cannot emit/restore twice.
                self.assertIsNone(window.recent_sheet)
                finished.assert_called_once_with(0)
                self.assertEqual(window.tabs.indexOf(window.recent), original_index)
                self.assertEqual(window.tabs.tabText(original_index), original_title)
                self.assertEqual(window.tabs.count(), original_count)
                self.assertEqual(window.canvas.view.transform(), transform)
                self.assertEqual(window.state, before)
                self.assertIs(APP.focusWidget(), window.canvas.view)
            finally:
                window.close()
                APP.processEvents()


class ModelLibraryTests(unittest.TestCase):
    def test_category_root_import_scan_and_reversible_missing_cleanup(self):
        from prompt_calculus_studio.core import Storage
        from prompt_calculus_studio.media import Catalog, model_root, copy_model, scan_models
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=root/'qa') as directory:
            base = Path(directory)
            models = base/'models'
            loras = models/'loras'
            loras.mkdir(parents=True)
            source = base/'fixture.safetensors'
            source.write_bytes(b'synthetic, not model weights')
            copied = Path(copy_model(source, loras, 'loras'))
            self.assertEqual(copied, loras/source.name)
            self.assertFalse((loras/'loras').exists())
            self.assertEqual(model_root(loras), models)
            rows = scan_models(loras)
            self.assertEqual(len(rows), 1)
            store = Storage(base/'data')
            try:
                catalog = Catalog(store)
                catalog.merge_models(rows, loras)
                record = catalog.get(rows[0]['id'])
                record['notes'] = 'keep personal note'
                catalog.put('model', record, str(models))
                self.assertEqual(catalog.archive_missing_models(models), [])
                copied.unlink()  # Only this test's synthetic fixture.
                self.assertEqual(catalog.archive_missing_models(models), [record['id']])
                self.assertEqual(catalog.rows('model', str(models)), [])
                self.assertEqual(catalog.get(record['id'])['notes'], 'keep personal note')
                # A worker started before cleanup must not resurrect an archived
                # record, even if its file reappears before the callback.
                copied.write_bytes(source.read_bytes())
                catalog.update_identified_models(rows)
                self.assertEqual(catalog.rows('model', str(models)), [])
                self.assertEqual(catalog.get(record['id'])['notes'], 'keep personal note')
                copied.unlink()
                self.assertEqual(catalog.restore_missing_models(models), 1)
                self.assertEqual(catalog.rows('model', str(models))[0]['notes'], 'keep personal note')
                self.assertFalse(copied.exists())
                # A missing containing folder is not proof of a deleted file.
                loras.rmdir()
                self.assertEqual(catalog.archive_missing_models(models), [])
                with self.assertRaises(ValueError): model_root('')
            finally:
                store.db.close()


class ResultSelectionTests(unittest.TestCase):
    from test_multi_canvas import MultiCanvasTests as _Fixture
    setUp = _Fixture.setUp
    tearDown = _Fixture.tearDown

    def test_unbound_preview_does_not_take_images_from_recent_records(self):
        from PySide6.QtGui import QImage, QColor
        from prompt_calculus_studio import multi_output
        from prompt_calculus_studio.media import import_image
        records=[]
        for color in ('red','blue'):
            path=Path(self.tmp.name)/f'{color}.png'
            image=QImage(32,24,QImage.Format.Format_RGB32); image.fill(QColor(color))
            self.assertTrue(image.save(str(path)))
            record=import_image(path,self.w.store.directory,'')
            record['metadata']['raw']={'prompt_studio':{'texts':[{'output':self.oid}]}}
            self.w.catalog.put('recent',record); records.append(record)
        card=self.canvas.results; card.refresh(); APP.processEvents()
        self.assertEqual(card.images.count(),0)
        self.assertIsNone(card.record)


if __name__ == '__main__':
    unittest.main()
