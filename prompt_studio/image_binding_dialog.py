"""Image selection uses the same workflow-first picker as CLIP binding."""
import copy
from .clip_widgets import ClipBindingDialog
from .workflow_flow import image_nodes,bind_image
from .generation import options


class ImageBindingDialog(ClipBindingDialog):
    manual_choice=True
    def __init__(self,canvas,key):
        self.image_binding=copy.deepcopy(canvas.window.state['canvas_functions']['images'][key].get('binding'))
        super().__init__(canvas,key); self.setWindowTitle('綁定加載圖片'); self.nodes_label.setText('圖片節點')
    def refresh_targets(self):
        self.target.clear(); self.target.addItem('選擇圖片節點',None); profile=self.profile()
        self.target.setEnabled(profile is not None)
        if profile:
            for key,node in image_nodes(profile):self.target.addItem(node.get('_meta',{}).get('title',node['class_type'])+' · #'+key,key)
            if self.image_binding and self.image_binding['workflow']==profile['id']:
                self.target.setCurrentIndex(max(0,self.target.findData(self.image_binding['node'])))
        self.hint.setText('此工作流没有加載、預覽或保存圖片節點。' if profile and self.target.count()==1 else '')
        self.hint.setVisible(bool(self.hint.text()))
    def apply(self):
        w=self.canvas.window
        try:profile=self.binding_profile()
        except ValueError as exc:w.notice(str(exc));return
        def bind(state):
            from .generation import store_profile
            if profile:store_profile(state,profile)
            bind_image(state,self.key,profile['id'] if profile else None,self.target.currentData())
        self.canvas.commit(bind)
        w.comfy.images.poll()
