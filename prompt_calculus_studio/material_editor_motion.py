"""Editor list actions on top of the shared notification stack motion."""
from .prompt_editor import MaterialList
from .stacked_card_list import StackedCardListMixin
from .views import widget_colors


class EditorMaterialList(StackedCardListMixin,MaterialList):
    def __init__(self, owner):
        super().__init__()
        self.owner=owner
        self.category_drag=None
        self.init_stack(lambda:owner.window.state['settings'],lambda:widget_colors(self))
        from .material_editor_interaction import MaterialRowActions
        self.row_actions=MaterialRowActions(self)

    def drag_mime(self,current):
        from .material_category_drag import MIME
        mime=super().drag_mime(current)
        if self.category_drag:mime.setData(MIME,self.category_drag.token)
        return mime

    def startDrag(self,actions):
        from PySide6.QtCore import Qt
        from .material_category_drag import MaterialDrag
        current=self.currentItem()
        if current is None:return
        self.category_drag=MaterialDrag(self.owner,current.data(Qt.ItemDataRole.UserRole))
        try:super().startDrag(actions)
        finally:
            self.category_drag=None; self.owner.categories._clear_drop()
