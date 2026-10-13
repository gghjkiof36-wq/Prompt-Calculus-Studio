"""Editor list actions on top of the shared notification stack motion."""
from .prompt_editor import MaterialList
from .stacked_card_list import StackedCardListMixin
from .views import widget_colors


class EditorMaterialList(StackedCardListMixin,MaterialList):
    def __init__(self, owner):
        super().__init__()
        self.owner=owner
        self.init_stack(lambda:owner.window.state['settings'],lambda:widget_colors(self))
        from .material_editor_interaction import MaterialRowActions
        self.row_actions=MaterialRowActions(self)
