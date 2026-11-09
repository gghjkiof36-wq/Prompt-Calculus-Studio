"""Every current card uses the same menu sizing actions and retained openers."""
import copy
from unittest.mock import patch
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtWidgets import QPushButton, QDoubleSpinBox
from _dialog_driver import drive_dialog
from stage_fixture import StageFixture
from prompt_calculus_studio.widgets import RoundMenu, StudioDialog
from prompt_calculus_studio.flow_data import add_scheduler, add_image_input
from prompt_calculus_studio.prompt_sources import add as add_groups
from prompt_calculus_studio.result_data import add_text_reader


class CardMenuTests(StageFixture):
    def all_cards(self):
        keys={}
        def setup(state):
            keys['schedule']=add_scheduler(state)
            keys['image']=add_image_input(state)
            keys['groups']=add_groups(state)
            keys['reader']=add_text_reader(state,(0,0))
        self.c.commit(setup)
        keys['source']=self.c.functions.add_image()
        keys['single']=self.c.functions.add_image(enhanced=False)
        self.c.show_workflow_order()
        return [
            self.c.cards['root'],self.c.containers[self.cid],self.c.outputs[self.out],
            self.c.clips[self.clip],self.c.flow_cards[keys['schedule']],
            next(c for c in self.c.flow_cards.values() if c.kind=='stages'),
            self.c.flow_cards[keys['image']],self.c.functions.cards[keys['source']],
            self.c.functions.cards[keys['single']],self.c.functions.cards[keys['reader']],
            self.c.functions.cards[keys['groups']],self.c.order_card,self.c.preview_card,
        ]

    def menu(self,card):
        menus=[]
        with patch.object(RoundMenu,'open_at',lambda menu,*_:menus.append(menu)):
            if card.key in self.c.containers:self.c.container_menu(card.key,QPoint())
            else:card.open_menu(QPoint())
        self.assertEqual(len(menus),1)
        self.addCleanup(menus[0].deleteLater)
        return menus[0]

    def test_all_thirteen_cards_open_real_size_dialog_and_target_reset(self):
        cards=self.all_cards()
        before=copy.deepcopy(self.c.history_state())
        def cancel_size(dialog):
            self.assertEqual(dialog.windowTitle(),'調整模組尺寸')
            self.assertEqual(len(dialog.findChildren(QDoubleSpinBox)),2)
            dialog.reject()
        for card in cards:
            with self.subTest(card=type(card).__name__,key=card.key):
                self.assertIn('more',card.actions());self.assertNotIn('size',card.actions())
                actions={a.text():a for a in self.menu(card).actions() if not a.isSeparator()}
                self.assertIn('調整模組尺寸…',actions);self.assertIn('恢復自動尺寸',actions)
                with patch.object(self.c,'size_dialog',wraps=self.c.size_dialog) as size:
                    drive_dialog(actions['調整模組尺寸…'].trigger,StudioDialog,cancel_size)
                    size.assert_called_once_with(card.key)
                with patch.object(self.c,'reset_size',wraps=self.c.reset_size) as reset:
                    actions['恢復自動尺寸'].trigger();reset.assert_called_once_with(card.key)
        # Reset is a real existing layout operation; cancelled dialogs cannot edit content.
        self.assertEqual(self.w.state['uses']['root'],before['uses']['root'])

    def test_secondary_panel_buttons_are_removed_and_primary_openers_remain(self):
        cards=self.all_cards()
        schedule=next(card for card in cards if getattr(card,'kind',None)=='schedulers')
        output=self.c.outputs[self.out]
        self.assertEqual(schedule.panel.open_button.text(),'開啟預排程')
        self.assertEqual(output.panel.open_button.text(),'開啟 Prompt 輸出')
        self.assertFalse(schedule.panel.open_button.isHidden())
        self.assertFalse(output.panel.open_button.isHidden())
        page=schedule.panel.open_page()
        panels=[card.panel for card in cards if hasattr(getattr(card,'panel',None),'findChildren')]
        panels.extend([page.content,self.c.execution_bar])
        for panel in panels:
            with self.subTest(panel=type(panel).__name__):
                self.assertFalse({'管理','圖片管理','更多'} & {b.text() for b in panel.findChildren(QPushButton)})
        self.assertIs(schedule.panel.open_page(),page)
        page.finish()
        output_page=output.panel.open_page()
        self.assertIs(output.panel.open_page(),output_page)
        output_page.finish()

    def test_legacy_source_menu_also_uses_uniform_sizes(self):
        from prompt_calculus_studio.canvas_functions import CanvasFunctions
        menus=[]
        # Exercise the old owner implementation on the same isolated scene,
        # without changing the document version or constructing another app.
        owner=CanvasFunctions(self.c)
        key='__result_preview__'
        with patch.object(RoundMenu,'open_at',lambda menu,*_:menus.append(menu)):
            owner.context(key,QPoint())
        actions={a.text():a for a in menus[0].actions()}
        with patch.object(self.c,'size_dialog') as size,patch.object(self.c,'reset_size') as reset:
            actions['調整模組尺寸…'].trigger();size.assert_called_once_with(key)
            actions['恢復自動尺寸'].trigger();reset.assert_called_once_with(key)
        menus[0].deleteLater()
