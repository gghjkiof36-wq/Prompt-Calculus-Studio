"""Bounded Qt contracts for compact model rows and scoped model presets."""
import hashlib
import json
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.local_ai.model_page import LocalModelPage
from prompt_calculus_studio.local_ai.settings import DEFAULT_SETTINGS, normalize_settings
from prompt_calculus_studio.core import initial_state
from prompt_calculus_studio.theme import stylesheet

APP = QApplication.instance() or QApplication([])
if APP.platformName() == 'offscreen':
    for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)
KEYS = ('context_size','max_tokens','temperature','top_p','seed','gpu_layers')


class FakeService(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__(); self.settings = dict(DEFAULT_SETTINGS); self.presets = {}
        self.models = [dict(id='b',name='Beta',path='D:/qa/Beta.gguf',size=200,mtime_ns=10),
                       dict(id='a',name='Alpha',path='D:/qa/Alpha.gguf',size=100,mtime_ns=20)]
        self.selected_model = ''; self.loaded_model = ''; self.state = 'unloaded'; self.error = ''
        self.model_folder = 'D:/qa'; self.model_folder_busy = False; self.model_folder_state = 'idle'
        self.model_folder_message = ''; self.scans = 0
    def select_model(self, model_id):
        self.selected_model = model_id; self.changed.emit(); return True
    def configure(self, values):
        self.settings.update(values); self.changed.emit(); return True
    def model_preset(self, model_id):
        if model_id not in [m['id'] for m in self.models]: raise ValueError('模型已移除')
        saved = self.presets.get(model_id, {})
        params = {key: saved.get('parameters',{}).get(key,self.settings[key]) for key in KEYS}
        prompt = saved.get('system_prompt','')
        revision = hashlib.sha256(json.dumps([model_id,params,prompt]).encode()).hexdigest()
        return dict(model_id=model_id,parameters=params,overrides=saved.get('parameters',{}),system_prompt=prompt,revision=revision)
    def configure_model(self, model_id, values, *, system_prompt, expected_revision):
        if expected_revision != self.model_preset(model_id)['revision']:
            self.error='模型設定已變更'; return False
        values=normalize_settings(values,base=self.settings)
        self.presets[model_id]=dict(parameters={key:values[key] for key in KEYS},system_prompt=system_prompt)
        self.changed.emit(); return True
    def reset_model_preset(self, model_id, *, expected_revision):
        if expected_revision != self.model_preset(model_id)['revision']: return False
        self.presets.pop(model_id,None); self.changed.emit(); return True
    def refresh_model_folder(self): self.scans += 1; return True
    def reset_model_folder(self): self.scans += 1; return True
    def cancel_model_folder_scan(self): return True


class LocalModelPageTests(unittest.TestCase):
    def setUp(self):
        self.old_style = APP.styleSheet(); APP.setStyleSheet(stylesheet(initial_state()['settings']))
        self.service = FakeService(); self.page = LocalModelPage(self.service, initial_state()['settings'])
        self.page.resize(1200,800); self.page.show(); APP.processEvents()
    def tearDown(self):
        self.page.close(); self.page.deleteLater(); APP.processEvents(); APP.setStyleSheet(self.old_style)
    def ids(self):
        return [self.page.list.item(i).data(Qt.ItemDataRole.UserRole)['id'] for i in range(self.page.list.count())]
    def test_centered_pages_and_no_implicit_scan(self):
        self.assertEqual(self.page.column.width(),740)
        self.page.open_engine(); APP.processEvents(); self.assertEqual(self.page.column.width(),740)
        self.assertFalse(hasattr(self.page.engine_settings,'context_size'))
        self.page.open_preset('a'); APP.processEvents(); self.assertEqual(self.page.column.width(),740)
        self.assertEqual(self.service.scans,0); self.assertFalse(hasattr(self.page,'load_button'))
    def test_name_latest_size_sort_and_search_are_real(self):
        self.assertEqual(self.ids(),['a','b'])
        self.page.sort.setCurrentIndex(1); self.assertEqual(self.ids(),['a','b'])
        self.page.sort.setCurrentIndex(2); self.assertEqual(self.ids(),['b','a'])
        self.page.search.setText('Alpha'); self.assertEqual(self.ids(),['a'])
        self.page.search.clear(); self.assertEqual(self.ids(),['b','a'])
    def test_click_doubleclick_enter_never_expands_or_selects_model(self):
        item=self.page.list.item(0); compact=self.page._rows['a']
        pos=self.page.list.visualItemRect(item).center()
        QTest.mouseClick(self.page.list.viewport(),Qt.MouseButton.LeftButton,pos=pos)
        QTest.mouseDClick(self.page.list.viewport(),Qt.MouseButton.LeftButton,pos=pos)
        self.page.list.setCurrentItem(item); QTest.keyClick(self.page.list,Qt.Key.Key_Return); APP.processEvents()
        self.assertIs(self.page.list.item(0),item); self.assertIs(self.page._rows['a'],compact)
        self.assertEqual(compact.height(),80); self.assertEqual(self.service.selected_model,'')
        self.assertIs(self.page.stack.currentWidget(),self.page.library)
    def test_selection_status_keeps_controls_and_full_name_tooltip(self):
        compact=self.page._rows['a']; compact.more.setFocus(); self.service.select_model('a'); APP.processEvents()
        self.assertIs(self.page._rows['a'],compact); self.assertIn('已選用',compact.meta.text())
        self.assertEqual(compact.name.toolTip(),'Alpha')
    def test_status_refresh_reuses_icons_until_theme_color_changes(self):
        from prompt_calculus_studio.local_ai import model_page
        with patch.object(model_page,'icon',wraps=model_page.icon) as icons:
            self.page.refresh(); self.service.select_model('a'); self.page.refresh()
            self.assertEqual(icons.call_count,0)
            self.page.tokens=dict(self.page.tokens,secondary='#123456'); self.page.refresh()
            self.assertEqual(icons.call_count,6)
    def test_menu_actions_gear_and_no_api_or_delete(self):
        compact=self.page._rows['a']
        QTest.mouseClick(compact.more,Qt.MouseButton.LeftButton); APP.processEvents()
        actions=[a for a in self.page._model_menu.actions() if not a.isSeparator()]
        self.assertEqual([a.text() for a in actions],['選用此模型','選用並開新對話','在檔案總管中顯示'])
        actions[0].trigger(); self.assertEqual(self.service.selected_model,'a')
        created=[]; self.page.createChatRequested.connect(lambda:created.append(True)); actions[1].trigger(); self.assertEqual(created,[True])
        with patch.object(self.page,'reveal_model') as reveal: actions[2].trigger(); reveal.assert_called_once_with('a')
        self.page._model_menu.close(); QTest.mouseClick(compact.gear,Qt.MouseButton.LeftButton); APP.processEvents()
        self.assertEqual(self.page._presets['a'][0].model_id,'a'); self.assertEqual(self.page.heading.text(),'Alpha')
    def test_preset_bound_id_draft_preserved_conflict_and_reset(self):
        self.page.open_preset('a'); a=self.page._presets['a'][0]
        a.temperature.setValue(.31); a.system_prompt.setPlainText('A 指示')
        self.page.show_library(); self.page.open_preset('b'); b=self.page._presets['b'][0]
        b.temperature.setValue(.62); self.assertTrue(b.apply())
        self.service.select_model('b'); self.page.open_preset('a'); self.assertEqual(a.temperature.value(),.31)
        self.assertTrue(a.apply()); self.assertEqual(self.service.presets['a']['system_prompt'],'A 指示')
        self.assertEqual(self.service.presets['b']['parameters']['temperature'],.62)
        a.temperature.setValue(.4)
        current=self.service.model_preset('a'); self.service.configure_model('a',dict(current['parameters'],temperature=.5),system_prompt='外部',expected_revision=current['revision'])
        self.assertFalse(a.apply()); self.assertEqual(a.temperature.value(),.4)
        a.reload(); self.assertEqual(a.temperature.value(),.5); self.assertTrue(a.reset())
        self.assertEqual(a.system_prompt.toPlainText(),''); self.assertNotIn('a',self.service.presets)
    def test_success_scan_quiet_and_warning_visible(self):
        self.service.model_folder_message='掃描完成'; self.page.refresh(); self.assertFalse(self.page.status.isVisible())
        self.service.model_folder_state='warning'; self.service.model_folder_message='已略過無法讀取的檔案'; self.page.refresh()
        self.assertTrue(self.page.status.isVisible())
    def test_narrow_row_controls_and_preset_reachable(self):
        self.service.models[1]['name']='DeepSeek-R1-0528-Qwen3-8B-Q4_K_M'
        self.page.refresh(); APP.processEvents()
        self.page.resize(560,640); APP.processEvents(); compact=self.page._rows['a']
        self.assertLessEqual(self.page.column.width(),528); self.assertEqual(compact.height(),80)
        self.assertFalse(compact.more.visibleRegion().isEmpty()); self.assertFalse(compact.gear.visibleRegion().isEmpty())
        self.assertLess(compact.gear.mapTo(self.page.list.viewport(),compact.gear.rect().bottomRight()).x(),self.page.list.viewport().width())
        QTest.mouseClick(compact.gear,Qt.MouseButton.LeftButton); APP.processEvents()
        panel,page=self.page._presets['a']; page.ensureWidgetVisible(panel.apply_button); APP.processEvents()
        self.assertFalse(panel.apply_button.visibleRegion().isEmpty())

if __name__ == '__main__': unittest.main()
