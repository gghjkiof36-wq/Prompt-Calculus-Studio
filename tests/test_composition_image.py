import copy,hashlib,json,os,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QImage,QColor
from PySide6.QtTest import QTest
from prompt_studio.composition_image import document,layer,render_image,freeze_source,PreviewCache,validate_image
from prompt_studio.window import Window
from prompt_studio import multi_output as model
from prompt_studio.snapshots import make_snapshot,restore_snapshot
from prompt_studio.backup import archive_data
from prompt_studio.pnginfo import png_metadata
APP=QApplication.instance() or QApplication([])


class CompositionImageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'}); self.env.start(); self.w=Window(self.root/'data')
        self.w.state['settings'].update(online=False,material='solid',reduce_motion=True); self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(15)
        self.canvas=self.w.canvas; self.cid=next(iter(self.canvas.containers)); self.doc=document(120,80)
        from test_multi_output import workflow
        self.oid=self.canvas.data()['current_output']; self.w.generation_panel.save_profile(workflow())
        from prompt_studio.clip_flow import set_binding,source
        clip=next(k for k in self.canvas.data()['clip_inputs'] if source(self.w.state,k)==self.oid)
        set_binding(self.w.state,'flow',clip,('6','text'))
    def tearDown(self): self.w.close(); APP.processEvents(); self.env.stop(); self.temp.cleanup()
    def test_clean_render_contains_shapes_strokes_and_erasure_only(self):
        rect=layer('rect',10,10,80,60); rect['fill']='#ff0000'; self.doc['layers'].append(rect)
        stroke=layer('stroke',0,0,120,80,points=[[20,30],[90,30]]); stroke.update(stroke='#0000ff',stroke_width=8); self.doc['layers'].append(stroke)
        erase=layer('erase',0,0,120,80,points=[[40,10],[40,70]]); erase['stroke_width']=10; self.doc['layers'].append(erase)
        image=render_image(self.doc,self.w.store.directory)
        self.assertEqual(image.pixelColor(15,15).name(),'#ff0000'); self.assertEqual(image.pixelColor(20,30).name(),'#0000ff')
        self.assertEqual(image.pixelColor(40,30).name(),'#ffffff'); self.assertEqual(image.pixelColor(0,0).name(),'#ffffff')
        self.canvas.commit(lambda s:s['multi_output']['canvases'][self.cid].update(image=self.doc))
        self.canvas.add_tag('never appear in PNG',self.canvas.containers[self.cid].pos()+QPointF(30,70))
        model.connect(self.w.state,self.cid,self.oid,'image'); source=freeze_source(self.w.state,self.w.store.directory)
        path=self.w.store.directory/source['relative']; actual=QImage(str(path)); self.assertEqual(actual,image)
        self.assertNotIn('prompt_studio',png_metadata(path)['raw']); self.assertEqual(source['width'],120)
    def test_crop_layer_order_hidden_lock_and_bounded_cache(self):
        source=self.root/'image.png'; image=QImage(100,50,QImage.Format.Format_RGB32); image.fill(QColor('red'))
        for x in range(50,100):
            for y in range(50): image.setPixelColor(x,y,QColor('blue'))
        image.save(str(source)); original=source.read_bytes(); stored=self.w.generation_panel.import_source(source)
        photo=layer('image',0,0,120,80,source=stored,crop=[.5,0,1,1]); photo['locked']=True; self.doc['layers'].append(photo)
        cache=PreviewCache(1000); rendered=render_image(self.doc,self.w.store.directory,cache,100,True)
        self.assertEqual(rendered.pixelColor(50,30).name(),'#0000ff'); self.assertLessEqual(cache.bytes,cache.max_bytes)
        photo['visible']=False; self.assertEqual(render_image(self.doc,self.w.store.directory).pixelColor(50,30).name(),'#ffffff')
        self.assertEqual(source.read_bytes(),original)
    def test_resize_outer_save_zip_restore_and_frozen_source(self):
        source=self.root/'image.png'; image=QImage(120,80,QImage.Format.Format_RGB32); image.fill(QColor('green')); image.save(str(source))
        stored=self.w.generation_panel.import_source(source); self.doc['layers'].append(layer('image',0,0,120,80,source=stored))
        self.canvas.commit(lambda s:s['multi_output']['canvases'][self.cid].update(image=self.doc))
        self.canvas.commit(lambda s:model.connect(s,self.cid,self.oid,'image')); frozen=freeze_source(self.w.state,self.w.store.directory)
        self.canvas.commit(lambda s:s['multi_output']['canvases'][self.cid].update(display_size=[1000,900]))
        self.assertEqual(self.canvas.data()['canvases'][self.cid]['image']['width'],120)
        self.w.persist(); backup=self.w.store.backup(); target=self.root/'backup.zip'; archive_data(self.w.store.directory,backup,target)
        with zipfile.ZipFile(target) as archive:
            self.assertIn('data/'+stored['relative'],archive.namelist()); self.assertIn('data/'+frozen['relative'],archive.namelist())
            archive.extractall(self.root/'restored')
        from prompt_studio.core import Storage,build_prompt
        restored_store=Storage(self.root/'restored/data'); loaded=restored_store.load(); restored_store.close()
        self.assertEqual(build_prompt(loaded),build_prompt(self.w.state))
        self.assertEqual(loaded['multi_output'],self.w.state['multi_output'])
        self.assertEqual(render_image(loaded['multi_output']['canvases'][self.cid]['image'],self.root/'restored/data'),render_image(self.doc,self.w.store.directory))
        snapshot=make_snapshot(self.w.state); restored=restore_snapshot(self.w.state,snapshot)
        self.assertEqual(restored['multi_output']['canvases'][self.cid]['image'],self.doc)
        self.doc['background']='#000000'; self.doc['layers']=[]
        self.assertEqual(hashlib.sha256((self.w.store.directory/frozen['relative']).read_bytes()).hexdigest(),frozen['sha256'])
    def test_editor_shape_gesture_and_return_restores_outer_view(self):
        self.canvas.commit(lambda s:s['multi_output']['canvases'][self.cid].update(image=self.doc))
        transform=self.canvas.view.transform(); center=self.canvas.view.mapToScene(self.canvas.view.viewport().rect().center())
        self.canvas.open_editor(self.cid); QTest.qWait(20); editor=self.canvas.editor_page; editor.view.fit(); editor.set_tool('rect')
        start=editor.view.mapFromScene(QPointF(10,10)); end=editor.view.mapFromScene(QPointF(80,60))
        QTest.mousePress(editor.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,start)
        QTest.mouseMove(editor.view.viewport(),end,25); QTest.mouseRelease(editor.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,end)
        self.assertEqual(len(editor.image()['layers']),1); self.assertAlmostEqual(editor.image()['layers'][0]['width'],70,delta=1)
        editor.undo(); self.assertEqual(editor.image()['layers'],[]); editor.redo(); self.assertEqual(len(editor.image()['layers']),1)
        editor.close_editor(); QTest.qWait(5); self.assertEqual(self.canvas.view.transform(),transform)
        new_center=self.canvas.view.mapToScene(self.canvas.view.viewport().rect().center()); self.assertLess((new_center-center).manhattanLength(),5)
    def test_unreadable_source_and_excessive_dimensions_stop_generation(self):
        invalid=document(16000,16000)
        with self.assertRaises(ValueError): validate_image(invalid)
        with self.assertRaisesRegex(ValueError,'連到'): freeze_source(self.w.state,self.w.store.directory)
        model.connect(self.w.state,self.cid,self.oid,'image')
        with self.assertRaisesRegex(ValueError,'尺寸'): freeze_source(self.w.state,self.w.store.directory)
