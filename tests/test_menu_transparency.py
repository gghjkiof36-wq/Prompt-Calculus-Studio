"""Menu translucency is a saved appearance choice, independent of the shell."""
import copy,os,tempfile,unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QColor,QFontDatabase,QPalette,QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout
from prompt_calculus_studio.core import DEFAULT_SETTINGS,Storage,initial_state,validate_state
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.popup_surface import PopupSurface,refresh_popup_surfaces
from prompt_calculus_studio.theme import stylesheet,visual_tokens,widget_palette
from prompt_calculus_studio.widgets import ComboBox

APP=QApplication.instance() or QApplication([])
if APP.platformName()=='offscreen':
    for name in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)


class MenuTransparencyDataTests(unittest.TestCase):
    def test_legacy_default_and_integer_range_do_not_change_source_document(self):
        state=initial_state();state['settings'].pop('menu_transparency');before=copy.deepcopy(state)
        self.assertEqual(prepare_state(state)['settings']['menu_transparency'],6)
        self.assertEqual(state,before)
        for value in (0,6,40):
            state['settings']['menu_transparency']=value;validate_state(state)
        for value in (-1,41,True,6.0,'6',None):
            state['settings']['menu_transparency']=value
            with self.assertRaisesRegex(ValueError,'選單透明度'):validate_state(state)

    def test_saved_menu_value_does_not_change_window_material_values(self):
        state=initial_state();state['settings'].update(menu_transparency=40,mica_transparency=17,acrylic_transparency=83)
        with tempfile.TemporaryDirectory() as directory:
            store=Storage(directory);store.save(state);loaded=store.load_current();store.db.close()
        self.assertEqual([loaded['settings'][k] for k in ('menu_transparency','mica_transparency','acrylic_transparency')],[40,17,83])


class MenuTransparencySurfaceTests(unittest.TestCase):
    def setUp(self):
        self.host=QWidget();self.host.state={'settings':dict(DEFAULT_SETTINGS,material='solid')}
        self.host.setPalette(widget_palette(self.host.state['settings']));self.host.setGeometry(20,20,420,300)
        self.host.show();APP.processEvents()

    def tearDown(self):
        self.host.close();APP.processEvents()

    def test_zero_default_and_maximum_change_only_sample_tint_with_solid_fallback(self):
        popup=QWidget(self.host,Qt.WindowType.Popup);popup.resize(220,140)
        surface=PopupSurface(popup,self.host);popup.move(self.host.mapToGlobal(QPoint(30,40)));popup.show();APP.processEvents()
        self.assertIsNotNone(surface.backdrop)
        sample=QColor('#e1a53d');backdrop=QPixmap(popup.size());backdrop.fill(sample);surface.backdrop=backdrop
        raised=surface.surface_palette().color(QPalette.ColorRole.ToolTipBase)
        cache_key=surface.backdrop.cacheKey()
        for value in (0,6,40):
            self.host.appearance_preview={'menu_transparency':value};refresh_popup_surfaces(self.host);APP.processEvents()
            image=popup.grab().toImage();actual=image.pixelColor(110,70)
            alpha=round(255*(100-value)/100)
            for component in ('red','green','blue'):
                expected=round((getattr(raised,component)()*alpha+getattr(sample,component)()*(255-alpha))/255)
                self.assertLessEqual(abs(getattr(actual,component)()-expected),2)
            self.assertEqual(actual.alpha(),255)
            self.assertEqual(image.pixelColor(0,0).alpha(),0)
            self.assertEqual(surface.backdrop.cacheKey(),cache_key)
        self.assertEqual(self.host.state['settings']['menu_transparency'],6)
        surface.backdrop=None;popup.update();APP.processEvents()
        self.assertEqual(popup.grab().toImage().pixelColor(110,70),raised)
        popup.close()

    def test_selected_row_stays_opaque_at_maximum_transparency(self):
        layout=QVBoxLayout(self.host);combo=ComboBox();combo.addItems(['First choice','Second choice']);layout.addWidget(combo)
        for palette in ('graphite','paper'):
            settings=dict(DEFAULT_SETTINGS,menu_transparency=40,visual_palette=palette)
            self.host.state['settings']=settings;self.host.setPalette(widget_palette(settings));self.host.setStyleSheet(stylesheet(settings))
            combo.showPopup();APP.processEvents();view=combo.view();index=view.model().index(0,0)
            view.setCurrentIndex(index);APP.processEvents()
            rect=view.visualRect(index);image=view.viewport().grab().toImage()
            pixel=image.pixelColor(rect.right()-16,rect.center().y())
            self.assertEqual(pixel.name(),visual_tokens(settings)['popup_active']);self.assertEqual(pixel.alpha(),255)
            combo.hidePopup()

    def test_canvas_embedded_owner_reads_its_containing_window_setting(self):
        from PySide6.QtWidgets import QGraphicsScene,QGraphicsView
        scene=QGraphicsScene(self.host);view=QGraphicsView(scene,self.host);view.resize(300,200)
        owner=QWidget();scene.addWidget(owner);popup=QWidget(self.host,Qt.WindowType.Popup)
        surface=PopupSurface(popup,owner);self.host.state['settings']['menu_transparency']=40
        self.assertIs(surface.surface_host(),self.host);self.assertEqual(surface.tint_alpha(),153)
        self.host.appearance_preview={'menu_transparency':0};self.assertEqual(surface.tint_alpha(),255)


class MenuTransparencySettingsTests(unittest.TestCase):
    def setUp(self):
        from prompt_calculus_studio.window import Window
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':self.tmp.name});self.env.start()
        self.network=patch('prompt_calculus_studio.comfy_client.ComfyClient.request');self.network.start()
        self.w=Window(self.tmp.name);self.w.display_recovery.stop();self.w.first_models=False
        self.w.state['settings'].update(online=False,material='solid');self.w.apply_theme();self.w.show();self.w.settings('appearance')

    def tearDown(self):
        self.w.close();APP.processEvents();self.network.stop();self.env.stop();self.tmp.cleanup()

    def test_live_preview_save_restore_and_reset_keep_window_transparency_independent(self):
        page=self.w.settings_page;prefs=page.preferences
        old_material={key:self.w.state['settings'][key] for key in ('mica_transparency','acrylic_transparency')}
        self.assertTrue(prefs.menu_transparency.isVisible());self.assertTrue(prefs.menu_transparency.isEnabled())
        self.assertEqual(prefs.transparency.accessibleName(),'視窗透明度')
        with patch.object(self.w,'apply_theme',wraps=self.w.apply_theme) as apply:
            for value in (0,20,40):
                prefs.menu_transparency.setValue(value)
                self.assertEqual(self.w.appearance_preview['menu_transparency'],value)
                self.assertEqual(prefs.menu_transparency_value.value(),value)
                self.assertEqual(self.w.state['settings']['menu_transparency'],6)
            apply.assert_not_called()
        page.flush();self.assertEqual(self.w.store.load()['settings']['menu_transparency'],40)
        self.assertEqual({key:self.w.state['settings'][key] for key in old_material},old_material)
        page.reset_appearance(restore=True);self.assertEqual(page.preferences.menu_transparency.value(),6)
        page.preferences.menu_transparency_value.setValue(29);page.flush()
        self.assertEqual(self.w.store.load()['settings']['menu_transparency'],29)
        page.reset_appearance();self.assertEqual(page.preferences.menu_transparency.value(),6)


if __name__=='__main__':unittest.main()
