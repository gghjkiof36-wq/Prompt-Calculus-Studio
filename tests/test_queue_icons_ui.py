"""Real typed queue refresh, selection and owner changes; no backend."""
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage,QColor
import test_close_barrier as fixture
from prompt_calculus_studio.flow_widgets import SchedulePanel
from prompt_calculus_studio.queue_icons import read_icon


class QueueIconsUiTests(fixture.CloseBarrierTests):
    def panel(self):
        rows=[]
        for n in range(16):
            path=self.w.store.directory/f'{n}.png'
            image=QImage(1024,768,QImage.Format.Format_RGB32);image.fill(QColor(n*12,40,170));image.save(str(path))
            source=dict(relative=path.name,name=path.name,sha256=str(n),width=1024,height=768)
            rows.append(dict(id=str(n),state='waiting',error='',label='Synthetic',modified=1,route={},
                             inputs={'input::image':dict(type='image',value=source)}))
        store=SimpleNamespace(rows=lambda _:rows,control=lambda _:dict(paused=True,feed=None),
                              read=lambda key:next(r for r in rows if r['id']==key))
        runner=SimpleNamespace(store=store,message='')
        prop=patch.object(SchedulePanel,'runner',property(lambda _:runner));prop.start();self.addCleanup(prop.stop)
        nodes={key:dict(channels=[dict(id='image',name='Image',type='image')]) for key in ('first','second')}
        canvas=SimpleNamespace(window=self.w,data=lambda:dict(schedulers=nodes))
        panel=SchedulePanel(canvas,'first');panel.setParent(self.w);panel.show()
        return panel,rows

    def test_status_and_source_refresh_preserve_selection_and_read_only_changed_entry(self):
        panel,rows=self.panel()
        with patch('prompt_calculus_studio.queue_icons.read_icon',wraps=read_icon) as reader:
            panel.refresh();self.assertEqual(reader.call_count,16)
            panel.listing.setCurrentRow(3)
            rows[0]['state']='preparing';rows[0]['modified']+=1
            panel.refresh();panel.refresh();self.assertEqual(reader.call_count,16)
            self.assertEqual(panel.listing.currentItem().data(Qt.ItemDataRole.UserRole),'3')
            panel.listing.setCurrentRow(-1);panel.refresh();self.assertEqual(panel.listing.currentRow(),0)
            path=self.w.store.directory/rows[0]['inputs']['input::image']['value']['relative']
            image=QImage(1024,768,QImage.Format.Format_RGB32);image.fill(QColor('green'));image.save(str(path))
            rows[0]['modified']+=1;panel.refresh();self.assertEqual(reader.call_count,17)
        self.assertEqual(panel.listing.item(0).icon().pixmap(52,52).toImage().pixelColor(0,0),QColor('green'))

    def test_page_and_document_owner_invalidate_current_icons(self):
        panel,rows=self.panel();panel.refresh()
        with patch('prompt_calculus_studio.queue_icons.read_icon',wraps=read_icon) as reader:
            panel.hide();panel.key='second';panel.refresh();panel.show()
            self.assertEqual(reader.call_count,16)
            self.w.canvas_edit_context=object();panel.refresh();self.assertEqual(reader.call_count,32)
        self.assertFalse(getattr(self.w,'_preview_loader',None))

    def test_control_only_refresh_updates_progress_without_rereads(self):
        panel,rows=self.panel();panel.refresh()
        control=dict(paused=False,feed=dict(key='image',batch='synthetic',next=1,total=10))
        with patch.object(panel.runner.store,'control',return_value=control),patch(
                'prompt_calculus_studio.queue_icons.read_icon',side_effect=AssertionError('control reread')):
            panel.refresh();self.assertIn('尚有 9 張',panel.status.text());self.assertNotIn('已暫停',panel.status.text())
            control['feed']['next']=2;panel.refresh();self.assertIn('尚有 8 張',panel.status.text())

    def test_late_refresh_after_close_never_reads_images(self):
        panel,rows=self.panel();panel.refresh();self.assertTrue(self.w.close())
        rows[0]['modified']+=1
        with patch('prompt_calculus_studio.queue_icons.read_icon',side_effect=AssertionError('late read')):
            panel.refresh()
        self.assertFalse(panel._icons.cache)
