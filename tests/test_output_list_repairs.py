"""Composition-list actions use the clicked row and graphics-view coordinates."""
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QPointF
from PySide6.QtGui import QImage,QColor
from PySide6.QtTest import QTest
import test_multi_canvas as canvas_tests
from prompt_studio import multi_output
from prompt_studio.widgets import RoundMenu
from prompt_studio.image_bindings import import_source


class ListInteractionTests(unittest.TestCase):
    setUp=canvas_tests.MultiCanvasTests.setUp
    tearDown=canvas_tests.MultiCanvasTests.tearDown
    add=canvas_tests.MultiCanvasTests.add
    def test_batch_grid_paints_all_images_in_order_and_click_only_opens_view(self):
        from PySide6.QtGui import QPixmap
        from prompt_studio.canvas_results import ResultImage
        widget=ResultImage();widget.resize(480,360)
        colors=('red','blue','green','yellow');pictures=[]
        for color in colors:
            picture=QPixmap(48,64);picture.fill(QColor(color));pictures.append(picture)
        activated=[];widget.imageActivated.connect(activated.append)
        for count in (2,4):
            widget.set_pictures(pictures[:count]);widget.show();canvas_tests.APP.processEvents()
            rendered=widget.grab().toImage();cells=widget.image_rects()
            for index,cell in enumerate(cells):
                center=cell.adjusted(0,0,0,-20).center().toPoint()
                self.assertEqual(rendered.pixelColor(center),QColor(colors[index]))
                QTest.mouseClick(widget,Qt.MouseButton.LeftButton,pos=center)
                self.assertEqual(activated[-1],index)
            self.assertLess(cells[0].x(),cells[1].x())
            if count==4:self.assertLess(cells[1].y(),cells[2].y())
        widget.set_picture(QPixmap());self.assertFalse(widget.pictures);widget.close()
    def test_unconnected_preview_never_reads_recent_and_disconnect_clears_picture(self):
        from PySide6.QtGui import QPixmap
        preview=self.canvas.results
        # Even an existing displayed result cannot survive losing its input.
        preview.image_key='old-result'; preview.record={'id':'old-result'}
        picture=QImage(20,20,QImage.Format.Format_RGB32); picture.fill(QColor('red'))
        preview.preview.set_picture(QPixmap.fromImage(picture))
        with patch.object(self.w.catalog,'rows',side_effect=AssertionError('must not fall back to recent')):
            preview.refresh()
        self.assertIsNone(preview.record); self.assertTrue(preview.preview.picture.isNull())
        self.assertEqual(preview.images.count(),0)
        key=self.canvas.functions.add_image()
        self.canvas.commit(lambda s:multi_output.connect(s,key,multi_output.PREVIEW,'image'))
        preview.refresh(); self.assertTrue(preview.input_active)
        line=next(c for c in self.canvas.data()['connections'] if c['destination']==multi_output.PREVIEW)
        self.canvas.commit(lambda s:multi_output.disconnect(s,line['id']))
        preview.refresh(); self.assertFalse(preview.input_active)
        self.assertTrue(preview.preview.picture.isNull()); self.assertEqual(preview.images.count(),0)

    def test_clip_text_source_choice_preserves_prompt_and_supports_undo(self):
        self.add('keep this prompt')
        key=next(iter(self.canvas.clips)); panel=self.canvas.clips[key].panel
        before=multi_output.compile_output(self.w.state,self.oid)['final_prompt']
        self.assertEqual(panel.text_source.currentData(),'pcs')
        panel.text_source.setCurrentIndex(panel.text_source.findData('web'))
        self.assertEqual(self.canvas.data()['clip_inputs'][key]['text_source'],'web')
        self.assertEqual(multi_output.compile_output(self.w.state,self.oid)['final_prompt'],before)
        self.canvas.undo(); self.assertEqual(self.canvas.data()['clip_inputs'][key].get('text_source','pcs'),'pcs')

    def test_two_bound_images_arrive_through_input_and_selection_survives_refresh(self):
        from test_multi_output import workflow
        from prompt_studio.workflow_flow import bind_image
        profile=workflow(); profile['graph']['9']=dict(class_type='PreviewImage',inputs={})
        key=self.canvas.functions.add_image()
        def bind(state):
            state.setdefault('generation',dict(mode='txt2img',profiles=[],chosen={},source=None))['profiles']=[profile]
            bind_image(state,key,'flow','9'); multi_output.connect(state,key,multi_output.PREVIEW,'image')
        with patch.object(self.w,'notice') as notice:
            self.assertTrue(self.canvas.commit(bind),str(notice.call_args))
        items=[]
        for color in ('red','blue'):
            path=Path(self.tmp.name)/(color+'.png'); image=QImage(32,24,QImage.Format.Format_RGB32)
            image.fill(QColor(color)); image.save(str(path))
            source=import_source(path,self.w.store.directory)
            source['reference']=dict(workflow='flow',node='9',prompt_id='p',image=dict(filename=path.name,type='temp',subfolder=''))
            items.append(source)
        images=self.w.comfy.images; images.owner=images.identity()
        self.assertIsNotNone(images.scope(key))
        images.collections[key]=(images.scope(key),dict(collection='a'*64,items=items))
        card=self.canvas.results; card.refresh()
        self.assertTrue(card.input_active)
        self.assertEqual(card.images.count(),2); self.assertIsNone(images.source(key))
        for index,color in enumerate(('red','blue')):
            with patch.object(self.w,'notice') as notice:
                card.images.setCurrentRow(index); canvas_tests.APP.processEvents()
                self.assertIsNotNone(images.source(key),str(notice.call_args))
            self.assertEqual([p.toImage().pixelColor(0,0) for p in card.preview.pictures],[QColor('red'),QColor('blue')])
        card.refresh(); self.assertEqual(card.images.currentRow(),1)
        self.assertEqual(images.source(key)['sha256'],items[1]['sha256'])

    def test_clip_picker_saves_explicit_image_input_for_same_workflow(self):
        from test_multi_output import workflow
        from prompt_studio.clip_widgets import ClipBindingDialog
        profile=workflow()
        for key in ('20','21'):profile['graph'][key]=dict(class_type='LoadImage',inputs=dict(image='existing.png'))
        self.w.generation_panel.save_profile(profile)
        key=next(iter(self.canvas.clips))
        with patch.object(self.w.comfy,'request'):
            dialog=ClipBindingDialog(self.canvas,key)
        dialog.target.setCurrentIndex(dialog.target_index(('6','text')))
        dialog.image_input.setCurrentIndex(dialog.image_input.findData('21'))
        dialog.apply(); dialog.close()
        self.assertEqual(self.w.state['generation']['profiles'][0]['image'],'21')
        self.assertEqual(self.canvas.data()['clip_inputs'][key]['workflow'],'flow')

    def select_row(self,key):
        order=self.canvas.outputs[self.oid].panel.order
        order.setCurrentRow(next(i for i in range(order.count()) if order.item(i).data(Qt.ItemDataRole.UserRole)==key))
        return order
    def test_delete_and_backspace_remove_list_target_keep_other_canvas_selection_and_undo(self):
        first=self.add('first'); second=self.add('second')
        for key in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):
            self.canvas.view.scene().clearSelection(); self.canvas.cards[second].setSelected(True)
            order=self.select_row(first); order.setFocus(); QTest.keyClick(order,key)
            self.assertNotIn(first,self.w.state['uses']); self.assertIn(second,self.w.state['uses'])
            self.assertNotIn(first,self.canvas.data()['canvases'][self.cid]['members'])
            self.canvas.undo(); self.assertEqual(self.canvas.data()['canvases'][self.cid]['members'],[first,second])
            self.canvas.redo(); self.assertNotIn(first,self.w.state['uses']); self.canvas.undo()
    def test_context_menu_maps_zoom_and_pan_and_captures_list_target(self):
        first=self.add('first'); second=self.add('second')
        panel=self.canvas.outputs[self.oid].panel; order=self.select_row(first)
        for scale in (.5,1.4):
            self.canvas.view.resetTransform(); self.canvas.view.scale(scale,scale)
            self.canvas.view.centerOn(self.canvas.outputs[self.oid])
            canvas_tests.APP.processEvents()
            point=order.visualItemRect(order.currentItem()).center()
            proxy=self.canvas.outputs[self.oid].proxy
            mapped=proxy.mapToScene(QPointF(order.viewport().mapTo(panel,point)))
            expected=self.canvas.view.viewport().mapToGlobal(self.canvas.view.mapFromScene(mapped))
            with patch.object(RoundMenu,'open_at',autospec=True) as opened:
                panel.context(point)
                self.assertEqual(opened.call_args.args[1],expected)
                menu=opened.call_args.args[0]
                self.canvas.view.scene().clearSelection(); self.canvas.cards[second].setSelected(True)
                next(a for a in menu.actions() if a.text().startswith('刪除選取')).trigger()
                self.assertNotIn(first,self.w.state['uses']); self.assertIn(second,self.w.state['uses'])
                menu.deleteLater()
            self.canvas.undo(); order=self.select_row(first)
    def test_two_image_updates_reach_both_source_and_connected_preview(self):
        key=self.canvas.functions.add_image()
        self.canvas.commit(lambda s:multi_output.connect(s,key,multi_output.PREVIEW,'image'))
        for color in ('red','blue'):
            path=Path(self.tmp.name)/(color+'.png')
            image=QImage(20,20,QImage.Format.Format_RGB32); image.fill(QColor(color)); image.save(str(path))
            source=import_source(path,self.w.store.directory)
            self.canvas.commit(lambda s:s['canvas_functions']['images'][key].update(source=source))
            self.canvas.results.refresh()
            self.assertEqual(self.canvas.results.input_record['path'],str(self.w.store.directory/source['relative']))
            self.assertEqual(self.canvas.results.preview.picture.toImage().pixelColor(0,0),QColor(color))
