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
        owner=getattr(self.owner.window,'workspace_owner',None)
        drag=getattr(getattr(owner,'material_transfer',None),'drag',None)
        if drag and drag.page is self.owner:
            from .workspace_material_drag import MIME as COPY_MIME
            mime.setData(COPY_MIME,drag.token)
        return mime

    def startDrag(self,actions):
        from PySide6.QtCore import Qt
        from .material_category_drag import MaterialDrag
        current=self.currentItem()
        if current is None:return
        self.category_drag=MaterialDrag(self.owner,current.data(Qt.ItemDataRole.UserRole))
        owner=getattr(self.owner.window,'workspace_owner',None)
        transfer=getattr(owner,'material_transfer',None)
        if transfer:
            from .workspace_material_drag import MaterialCopyDrag
            from . import materials, prompt_materials
            ident=current.data(Qt.ItemDataRole.UserRole)
            if prompt_materials.kind(materials.item(self.owner.window.state,ident))=='text':
                try:transfer.drag=MaterialCopyDrag(transfer,self.owner,ident)
                except ValueError as error:
                    transfer.error(error); self.category_drag=None; return
        try:super().startDrag(actions)
        finally:
            if transfer and transfer.drag:
                transfer.drag.finish(); transfer.drag=None
            self.category_drag=None; self.owner.categories._clear_drop()
