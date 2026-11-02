"""Pixel, interaction and original-preservation checks for shared image editing."""
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QEvent,QPoint,QPointF,QRectF,QSize,Qt
from PySide6.QtGui import QColor,QFont,QFontDatabase,QImage,QInputMethodEvent,QPainter,QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QPushButton,QWidget
from prompt_calculus_studio.image_edits import ImageEdits,file_stamp,save_copy
from prompt_calculus_studio.image_viewer import ImageViewerDialog
from prompt_calculus_studio.pnginfo import png_metadata

APP=QApplication.instance() or QApplication([])
for font in ('msjh.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)


def pixels(image):
    return [[image.pixelColor(x,y).name(QColor.NameFormat.HexArgb) for x in range(image.width())] for y in range(image.height())]


def render(edits,image):
    result=QImage(QSize(*edits.state.size),QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent);painter=QPainter(result)
    edits.paint(painter,image);painter.end()
    return result


class ImageEditTests(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.root=Path(self.area.name)
        self.image=QImage(6,4,QImage.Format.Format_ARGB32)
        for y in range(4):
            for x in range(6):self.image.setPixelColor(x,y,QColor(x*40,y*70,(x+y)*20,120+x*20))
        self.source=self.root/'source.png'
        self.metadata={'prompt':{'1':{'class_type':'CLIPTextEncode','inputs':{'text':'測試'}}},'workflow':{'nodes':[]},'parameters':'原始提示詞'}
        for key,value in self.metadata.items():self.image.setText(key,value if isinstance(value,str) else json.dumps(value,ensure_ascii=False))
        self.assertTrue(self.image.save(str(self.source)))

    def tearDown(self):self.area.cleanup()

    def test_geometry_and_crop_match_pixel_reference_with_undo_branch(self):
        original=pixels(render(ImageEdits(self.image.size()),self.image))
        for operation,expected in (
            ('right',[list(row) for row in zip(*original[::-1])]),
            ('left',[list(row) for row in zip(*original)][::-1]),
            ('half',[row[::-1] for row in original[::-1]]),
            ('horizontal',[row[::-1] for row in original]),('vertical',original[::-1])):
            with self.subTest(operation=operation):
                edits=ImageEdits(self.image.size());edits.geometry(operation)
                self.assertEqual(pixels(render(edits,self.image)),expected)
        edits=ImageEdits(self.image.size());edits.geometry('right');edits.crop(QRectF(1,1,2,4))
        rotated=[list(row) for row in zip(*original[::-1])]
        self.assertEqual(pixels(render(edits,self.image)),[row[1:3] for row in rotated[1:5]])
        edits.undo();edits.redo();self.assertEqual(edits.state.size,(2,4))
        edits.undo();edits.geometry('horizontal');self.assertFalse(edits.redo())
        edits.reset();self.assertEqual(pixels(render(edits,self.image)),original)

    def test_marks_added_after_rotation_stay_upright_then_follow_geometry(self):
        image=QImage(160,100,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.white)
        edits=ImageEdits(image.size());edits.geometry('right')
        edits.mark('text',((10,45),),'#000000',24,'ABC')
        edits.mark('arrow',((10,70),(70,70)),'#ff0000',3)
        edits.mark('rectangle',((10,90),(60,140)),'#0000ff',3)
        edited=render(edits,image)
        text_pixels=[(x,y) for y in range(5,60) for x in range(95) if edited.pixelColor(x,y).lightness()<100]
        self.assertTrue(text_pixels)
        xs,ys=zip(*text_pixels);self.assertGreater(max(xs)-min(xs),max(ys)-min(ys))
        self.assertEqual(edited.pixelColor(40,70),QColor('#ff0000'))
        edits.geometry('half')
        turned=render(edits,image)
        # Re-rasterized vector glyphs have small edge-coverage differences.
        difference=0
        for y in range(turned.height()):
            for x in range(turned.width()):
                a=turned.pixelColor(x,y).getRgb();b=edited.pixelColor(edited.width()-x-1,edited.height()-y-1).getRgb()
                difference+=sum(abs(i-j) for i,j in zip(a,b))
        self.assertLess(difference/(turned.width()*turned.height()*4),.1)

    def test_save_copy_preserves_original_metadata_and_transparency(self):
        before=self.source.read_bytes();edits=ImageEdits(self.image.size());edits.geometry('right')
        destination=self.root/'copy.png';save_copy(self.source,destination,edits,file_stamp(self.source))
        self.assertEqual(self.source.read_bytes(),before);self.assertFalse(edits.dirty)
        self.assertEqual(png_metadata(destination)['raw'],self.metadata)
        actual=QImage(str(destination));expected=render(edits,self.image)
        self.assertEqual(actual.size(),expected.size())
        for y in range(actual.height()):
            for x in range(actual.width()):
                a=actual.pixelColor(x,y).getRgb();b=expected.pixelColor(x,y).getRgb()
                self.assertEqual(a[3],b[3])
                self.assertLessEqual(max(abs(i-j) for i,j in zip(a,b)),2)
        self.assertIn('source image',QImage(str(destination)).text('pcs_image_edit'))
        edits.undo();self.assertTrue(edits.dirty);edits.redo();self.assertFalse(edits.dirty)

    def test_failed_or_stale_export_never_replaces_original_or_destination(self):
        edits=ImageEdits(self.image.size());edits.geometry('right');stamp=file_stamp(self.source)
        digest=hashlib.sha256(self.source.read_bytes()).digest()
        with self.assertRaisesRegex(ValueError,'保留原圖'):save_copy(self.source,self.source,edits,stamp)
        destination=self.root/'existing.png';destination.write_bytes(b'existing copy')
        with patch('prompt_calculus_studio.image_edits.QImageWriter') as writer:
            writer.return_value.write.return_value=False;writer.return_value.errorString.return_value='failed'
            with self.assertRaisesRegex(OSError,'failed'):save_copy(self.source,destination,edits,stamp)
        self.assertEqual(destination.read_bytes(),b'existing copy');self.assertTrue(edits.dirty)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(),digest)
        self.source.write_bytes(self.source.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError,'已變更'):save_copy(self.source,destination,edits,stamp)
        self.assertEqual(destination.read_bytes(),b'existing copy')

    def test_exif_orientation_uses_visual_dimensions_on_load_and_export(self):
        from prompt_calculus_studio.image_viewport import ImageViewport
        jpeg=self.root/'rotated.jpg';self.image.save(str(jpeg),'JPEG')
        tiff=b'II'+struct.pack('<HIH',42,8,1)+struct.pack('<HHI',274,3,1)+struct.pack('<H',6)+b'\0\0'+struct.pack('<I',0)
        payload=b'Exif\0\0'+tiff;raw=jpeg.read_bytes();jpeg.write_bytes(raw[:2]+b'\xff\xe1'+struct.pack('>H',len(payload)+2)+payload+raw[2:])
        view=ImageViewport();view.resize(400,300);self.assertTrue(view.load(jpeg))
        self.assertEqual(view.edits.state.size,(4,6));self.assertEqual(view.image.size(),QSize(4,6))
        view.edits.geometry('right');destination=self.root/'exif-copy.png'
        save_copy(jpeg,destination,view.edits,file_stamp(jpeg))
        self.assertEqual(QImage(str(destination)).size(),QSize(6,4));view.deleteLater()

    def test_text_frame_wraps_clips_and_follows_image_geometry(self):
        from prompt_calculus_studio.image_edits import text_document
        image=QImage(260,180,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.white)
        edits=ImageEdits(image.size());edits.mark('text',((20,20),(180,140)),'#ff0000',24,'First line\n第二行文字，接續換行。')
        result=render(edits,image)
        marked=[(x,y) for y in range(180) for x in range(260) if result.pixelColor(x,y)!=QColor('white')]
        self.assertTrue(marked)
        self.assertTrue(all(20<=x<180 and 20<=y<140 for x,y in marked))
        self.assertGreater(max(y for _,y in marked)-min(y for _,y in marked),40)
        document=text_document(edits.state.marks[0].text,24,160.)
        self.assertIs(document,text_document(edits.state.marks[0].text,24,160.))
        edits.geometry('right');rotated=render(edits,image)
        self.assertEqual(rotated.size(),QSize(180,260))
        expected=QRectF(40,20,120,160)
        self.assertTrue(all(expected.contains(QPointF(x,y)) for y in range(260) for x in range(180) if rotated.pixelColor(x,y)!=QColor('white')))


class ImageViewerTests(unittest.TestCase):
    def setUp(self):
        self.area=tempfile.TemporaryDirectory();self.root=Path(self.area.name)
        image=QImage(1600,1000,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkCyan)
        self.path=self.root/'image.png';image.save(str(self.path))
        self.host=QWidget();self.host.resize(1000,740);self.host.show()
        self.viewer=ImageViewerDialog(self.host,str(self.path),'圖片',records=[
            {'id':'a','path':str(self.path),'name':'圖片'},
            {'id':'b','path':str(self.root/'missing.png'),'name':'找不到圖片'},
            {'id':'c','path':str(self.path),'name':'第三張'}],selected_id='a')
        self.viewer.show();QTest.qWait(30)

    def tearDown(self):
        with patch('prompt_calculus_studio.image_viewer.ask',return_value=True):self.viewer.close()
        self.viewer.deleteLater();self.host.close();self.host.deleteLater()
        APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents();self.area.cleanup()

    def test_ctrl_wheel_zoom_anchor_decode_reuse_and_hide_stop(self):
        view=self.viewer.display;view.zoom(3);APP.processEvents()
        anchor=QPointF(view.width()*.4,view.height()*.4);before=view.image_point(anchor);old=view.scale
        wheel=QWheelEvent(anchor,QPointF(view.mapToGlobal(anchor.toPoint())),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.ControlModifier,Qt.ScrollPhase.NoScrollPhase,False)
        APP.sendEvent(view,wheel)
        self.assertAlmostEqual(view.scale,old*1.2)
        self.assertLess((view.image_point(anchor)-before).manhattanLength(),.01)
        view.decode(1)
        with patch('prompt_calculus_studio.image_viewport.QImageReader',side_effect=AssertionError('cached pixels')):
            view.zoom(1.1);view.refine_image();view.zoom(1/1.1);view.refine_image()
        self.assertTrue(view.refine.isActive());self.viewer.hide();self.assertFalse(view.refine.isActive())

    def test_crop_gesture_enter_and_undo_and_edit_mode_suppress_navigation(self):
        viewer=self.viewer;view=viewer.display
        viewer.edit_button.click();viewer.crop_button.click();APP.processEvents()
        image_rect=view.view_transform().mapRect(QRectF(0,0,*view.edits.state.size))
        start=image_rect.topLeft()+QPointF(40,40);end=image_rect.bottomRight()-QPointF(60,60)
        QTest.mousePress(view,Qt.MouseButton.LeftButton,pos=start.toPoint());QTest.mouseMove(view,end.toPoint());QTest.mouseRelease(view,Qt.MouseButton.LeftButton,pos=end.toPoint())
        self.assertTrue(viewer.apply_crop.isEnabled());QTest.keyClick(view,Qt.Key.Key_Return)
        self.assertLess(view.edits.state.size[0],1600);self.assertFalse(viewer.crop_button.isChecked());self.assertTrue(viewer.apply_crop.isHidden())
        QTest.keyClick(view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(view.edits.state.size,(1600,1000))
        QTest.keyClick(view,Qt.Key.Key_Right);self.assertEqual(viewer.index,0)
        viewer.mark_tool.setCurrentIndex(viewer.mark_tool.findData('pen'));viewer.edit_button.click();viewer.edit_button.click()
        self.assertEqual(view.tool,'move');self.assertEqual(viewer.mark_tool.currentData(),'move')

    def test_side_navigation_focus_fades_reduced_motion_and_no_duplicate_close(self):
        viewer=self.viewer;edge=viewer.right_edge
        self.assertLess(viewer.previous.x(),viewer.display.width()/2)
        self.assertEqual(edge.geometry().right(),viewer.display.width()-1)
        self.assertFalse(any(b.text()=='關閉' for b in viewer.findChildren(QPushButton)))
        with patch('prompt_calculus_studio.dialog_presentation.reduced_motion',return_value=False):
            edge.button.setFocus(Qt.FocusReason.TabFocusReason);APP.processEvents();edge.reveal()
            edge.motion.setCurrentTime(160);self.assertEqual(edge.opacity.opacity(),1.)
            viewer.display.setFocus();APP.processEvents();edge.reveal();edge.motion.setCurrentTime(120)
            self.assertEqual(edge.opacity.opacity(),0.);self.assertFalse(edge.motion._timer.isActive())
        with patch('prompt_calculus_studio.dialog_presentation.reduced_motion',return_value=True):
            edge.button.setFocus();APP.processEvents();edge.reveal()
            self.assertEqual(edge.opacity.opacity(),1.);self.assertFalse(edge.motion._timer.isActive())
        QTest.mouseClick(viewer.next,Qt.MouseButton.LeftButton);self.assertEqual(viewer.index,1);self.assertTrue(viewer.display.text())
        QTest.keyClick(viewer,Qt.Key.Key_Right);self.assertEqual(viewer.index,2);self.assertFalse(viewer.next.isEnabled())

    def test_discard_cancel_and_save_copy_guard_close_and_navigation(self):
        viewer=self.viewer;viewer.rotate_button.click()
        with patch('prompt_calculus_studio.image_viewer.ask',return_value=False):
            QTest.keyClick(viewer.display,Qt.Key.Key_Escape);self.assertTrue(viewer.isVisible())
            viewer.step(1);self.assertEqual(viewer.index,0)
        source=self.path.read_bytes();saved=[];viewer.on_save=saved.append
        destination=self.root/'copy.png';viewer.save_to(destination)
        self.assertEqual(saved,[destination]);self.assertEqual(self.path.read_bytes(),source)
        with patch('prompt_calculus_studio.image_viewer.ask',side_effect=AssertionError('already saved')):
            QTest.keyClick(viewer.display,Qt.Key.Key_Escape);self.assertFalse(viewer.isVisible())

    def draw_text_frame(self):
        viewer=self.viewer;view=viewer.display
        if not viewer.editing:viewer.edit_button.click()
        viewer.mark_tool.setCurrentIndex(viewer.mark_tool.findData('text'));APP.processEvents()
        start=view.view_transform().map(QPointF(150,150)).toPoint()
        end=view.view_transform().map(QPointF(800,450)).toPoint()
        QTest.mousePress(view,Qt.MouseButton.LeftButton,pos=start);QTest.mouseMove(view,end);QTest.mouseRelease(view,Qt.MouseButton.LeftButton,pos=end)
        self.assertIsNotNone(view.text_box);return view.text_box

    def test_inline_text_selection_resize_ime_color_and_atomic_undo(self):
        viewer=self.viewer;view=viewer.display;box=self.draw_text_frame()
        QTest.keyClicks(box.entry,'First line');QTest.keyClick(box.entry,Qt.Key.Key_Return)
        ime=QInputMethodEvent();ime.setCommitString('第二行文字');APP.sendEvent(box.entry,ime)
        self.assertEqual(box.text(),'First line\n第二行文字')
        self.assertTrue(viewer.save_button.isEnabled());self.assertFalse(view.edits.dirty)
        QTest.keyClick(box.entry,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(box.entry.textCursor().hasSelection())
        before=QRectF(box.image_rect);corner=box.corners()[2].toPoint()
        QTest.mousePress(box,Qt.MouseButton.LeftButton,pos=corner)
        QTest.mouseMove(box,corner+QPoint(60,40));QTest.mouseRelease(box,Qt.MouseButton.LeftButton,pos=box.corners()[2].toPoint())
        self.assertGreater(box.image_rect.width(),before.width());self.assertGreater(box.image_rect.height(),before.height())
        self.assertEqual(box.font_size,32)
        viewer.pick_color();popup=viewer.color_popup;APP.processEvents();popup.presets[4].click()
        self.assertEqual(box.color,'#4dabf7')
        QTest.keyClick(popup.plane,Qt.Key.Key_Escape);APP.processEvents()
        self.assertTrue(viewer.isVisible());self.assertIs(view.text_box,box)
        self.assertTrue(box.entry.hasFocus());self.assertFalse(view.edits.dirty)
        destination=self.root/'text-copy.png';viewer.save_to(destination)
        self.assertIsNone(view.text_box);self.assertEqual(view.edits.index,1)
        mark=view.edits.state.marks[0];self.assertEqual(mark.text,'First line\n第二行文字')
        self.assertEqual(mark.color,'#4dabf7');self.assertEqual(len(mark.points),2)
        self.assertFalse(view.edits.dirty);viewer.history(-1);self.assertFalse(view.edits.state.marks)
        viewer.history(1);self.assertEqual(view.edits.state.marks[0],mark)

    def test_text_escape_cancels_only_draft_and_click_outside_commits(self):
        view=self.viewer.display;box=self.draw_text_frame();QTest.keyClicks(box.entry,'cancel me')
        QTest.keyClick(box.entry,Qt.Key.Key_Escape)
        self.assertIsNone(view.text_box);self.assertFalse(view.edits.dirty);self.assertTrue(self.viewer.isVisible())
        box=self.draw_text_frame();QTest.keyClicks(box.entry,'keep me')
        point=view.view_transform().map(QPointF(1100,700)).toPoint()
        QTest.mouseClick(view,Qt.MouseButton.LeftButton,pos=point)
        self.assertIsNone(view.text_box);self.assertEqual(view.edits.state.marks[0].text,'keep me')
        self.assertEqual(view.edits.index,1)

    def test_color_hex_and_control_heights_follow_ui_font(self):
        viewer=self.viewer;viewer.edit_button.click()
        for size in (11,15,19):
            font=QFont(viewer.font());font.setPointSize(size);viewer.setFont(font)
            for tool,control in (('pen',viewer.stroke_width),('text',viewer.text_size)):
                viewer.mark_tool.setCurrentIndex(viewer.mark_tool.findData(tool));APP.processEvents()
                self.assertEqual(control.height(),viewer.mark_tool.height())
        viewer.pick_color();popup=viewer.color_popup;APP.processEvents()
        popup.hex.setFocus();popup.hex.selectAll();QTest.keyClicks(popup.hex,'#12AB34')
        self.assertEqual(viewer.display.color,'#12ab34');self.assertFalse(viewer.display.edits.dirty)
        popup.hex.selectAll();QTest.keyClicks(popup.hex,'#ZZZZZZ');QTest.keyClick(popup.hex,Qt.Key.Key_Return)
        self.assertTrue(popup.isVisible());self.assertEqual(viewer.display.color,'#12ab34')
        QTest.keyClick(popup.hex,Qt.Key.Key_Escape);self.assertTrue(viewer.isVisible())


if __name__=='__main__':unittest.main()
