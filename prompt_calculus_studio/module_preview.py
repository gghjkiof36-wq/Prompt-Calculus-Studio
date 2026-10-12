"""Read-only default-node previews rendered by the real Canvas card classes."""
import copy
from types import SimpleNamespace
from PySide6.QtCore import Qt, QObject, Signal, QRectF, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QFrame


# Only the isolated example's layout changes. Production node defaults and the
# user's saved graph remain untouched; all painting and controls are shared.
PREVIEW_SIZES = {
    'canvas': (360, 340), 'output': (378, 340), 'clip': (360, 174),
    'reader': (360, 180), 'source': (360, 380), 'single': (360, 380),
    'image-input': (360, 180), 'preview': (368, 336),
    'schedule': (380, 360), 'stage': (360, 245),
}


class PreviewRuntime(QObject):
    stateChanged = Signal()

    def __init__(self):
        super().__init__()
        # Previewing a node cannot read a user's run history or initiate IO.
        store = SimpleNamespace(rows=lambda *a, **k: [], scheduler_paused=lambda *a: False)
        runner = SimpleNamespace(store=store, closed=False, results={},
                                 progress=lambda: '', expansion=lambda *a: '')
        self.input_flow = SimpleNamespace(chain=runner)
        self.images = SimpleNamespace(source=lambda key: None, errors={})


class PreviewCanvas:
    def __init__(self, actual, view):
        from .core import initial_state
        from .multi_canvas import FlowFunctions
        state = initial_state()
        state.update(settings=copy.deepcopy(actual.window.state['settings']), uses={},
                     generation={'profiles': []}, text_positions={}, text_sizes={},
                     canvas_functions=dict(images={}, preview=True),
                     multi_output=dict(version=7, canvases={}, outputs={}, clip_inputs={},
                                       image_inputs={}, schedulers={}, stages={}, bindings=[],
                                       connections=[], current_output=None, workflow_order={}))
        self.window = SimpleNamespace(state=state, store=actual.window.store,
            styleSheet=actual.window.styleSheet, builder_panel=actual.window.builder_panel,
            final=actual.window.final, changed=lambda *a, **k: None, comfy=PreviewRuntime())
        self.view = view
        self.font = actual.font
        self.cards = {}; self.containers = {}; self.outputs = {}; self.clips = {}; self.flow_cards = {}
        self.ports = {}; self.lines = {}; self.image_previews = {}; self.active_keys = set()
        self.preview_card = None; self.order_card = None
        self.functions = FlowFunctions(self)

    def data(self): return self.window.state['multi_output']
    def commit(self, *args, **kwargs): return False
    def update_lines(self):
        from .multi_canvas import MultiCanvas
        MultiCanvas.update_lines(self)
    def focus_node(self, *args): pass
    def order_sources(self, *args): pass

    def make_card(self, kind):
        from .multi_output import new_canvas, new_output
        from .multi_canvas import OutputCard
        from .clip_widgets import ClipCard
        from .flow_widgets import FlowCard
        from .flow_items import CanvasContainer, Port
        from .canvas_functions import SourceCard
        from .canvas_items import PreviewCard
        from .module_contracts import MODULES
        state = self.window.state; data = self.data(); key = 'preview_node'
        if kind == 'canvas':
            data['canvases'][key] = new_canvas('文字畫布')
            data['canvases'][key]['display_size'] = list(PREVIEW_SIZES[kind])
            card = CanvasContainer(self, key); self.containers[key] = card; card.refresh()
            contract = 'canvas'
        elif kind == 'output':
            data['outputs'][key] = new_output('Prompt 控制'); data['current_output'] = key
            card = OutputCard(self, key); self.outputs[key] = card; contract = 'prompt'
        elif kind == 'clip':
            data['clip_inputs'][key] = dict(name='CLIP 輸入')
            card = ClipCard(self, key); self.clips[key] = card; contract = 'clip_input'
        elif kind in ('stage', 'schedule', 'image-input'):
            from .stage_model import add
            from .flow_data import add_scheduler, add_image_input
            collection, factory, contract = {
                'stage':('stages', add, 'stage'), 'schedule':('schedulers', add_scheduler, 'scheduler'),
                'image-input':('image_inputs', add_image_input, 'image_input')}[kind]
            key = factory(state)
            card = FlowCard(self, key, collection); self.flow_cards[key] = card
        elif kind == 'preview':
            from .canvas_results import CanvasResults
            self.results = CanvasResults(self.window)
            card = PreviewCard(self); key = card.key; self.preview_card = card; contract = 'preview'
        else:
            value = dict(source=None, attached=False, enhanced=kind == 'source')
            if kind == 'reader': value.update(reader='text', name='讀取文字', show_image=False)
            state['canvas_functions']['images'][key] = value
            card = SourceCard(self.functions, key); self.functions.cards[key] = card
            contract = 'text_reader' if kind == 'reader' else 'image_source' if kind == 'source' else 'image'
        self.view.scene().addItem(card)
        if not isinstance(card, CanvasContainer):
            state['text_sizes'][key] = list(PREVIEW_SIZES[kind])
            card.attach(); card.update_text()
            # The catalog shows the primary controls at a readable size. Extra
            # batch tools stay in the full Canvas; only this isolated instance
            # receives the compact layout, with the real widgets and paint.
            if kind in ('source','single'):
                card.panel.preview.setMinimumHeight(120)
                if kind=='source':card.panel.source_controls.hide()
                card.layout_card()
            elif kind=='output':
                card.panel.order.setMinimumHeight(48)
                card.layout_card()
        spec = MODULES[contract]
        inputs = ('text','image') if kind == 'output' else ('control',) if kind == 'stage' else spec.inputs
        outputs = ('flow','image','clip') if kind == 'stage' else spec.outputs
        for output, kinds in ((False, inputs), (True, outputs)):
            for index, wire in enumerate(kinds):
                self.ports[(key, wire, output)] = Port(self, card, key, wire, output, index=index)
        # Schedulers have typed channel endpoints in the real graph.
        if kind == 'schedule':
            for index, channel in enumerate(data['schedulers'][key]['channels']):
                for output in (False, True):
                    endpoint = key + '::' + channel['id']
                    self.ports[(endpoint, channel['type'], output)] = Port(self, card, endpoint, channel['type'], output, index=index)
            for output in (False,True):
                endpoint=key+'::flow'
                self.ports[(endpoint,'flow',output)] = Port(self,card,endpoint,'flow',output,index=len(data['schedulers'][key]['channels']))
        self.update_lines()
        return card


class ModulePreview(QGraphicsView):
    def __init__(self, canvas):
        super().__init__(); self.actual = canvas; self.context = None; self.card = None
        self.setScene(QGraphicsScene(self)); self.setInteractive(False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus); self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing |
                            QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMinimumWidth(380)
        self.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self.setStyleSheet('QGraphicsView {background:transparent;border:0;padding:0;}')
        self.fitting = False
        self.refit=QTimer(self);self.refit.setSingleShot(True)
        self.refit.timeout.connect(self.fit_node)

    def show_kind(self, kind):
        self.refit.stop()
        self.scene().clear(); self.card = None; self.context = None
        if kind:
            self.context = PreviewCanvas(self.actual, self)
            self.card = self.context.make_card(kind)
            proxy=getattr(self.card,'proxy',None)
            if proxy is not None:proxy.geometryChanged.connect(lambda:self.refit.start(0))
        self.fit_node()
        # Proxy widgets settle their minimum height after the first layout.
        # Refit on that change, never on every scene paint or pointer move.
        self.refit.start(0)

    def fit_node(self):
        if self.fitting:return
        self.fitting = True
        try:
            if self.card:
                rect = self.scene().itemsBoundingRect().adjusted(-4,-4,4,4)
                # Tall content scrolls instead of shrinking every label and
                # control. The floor also protects large-font/narrow windows.
                scale=max(.85,min(1,max(1,self.viewport().width()-4)/rect.width()))
                self.setFixedHeight(min(420,round(rect.height()*scale)+4))
                self.scene().setSceneRect(rect); self.resetTransform();self.scale(scale,scale)
                self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())
            else:self.setFixedHeight(0)
        finally:self.fitting = False

    def resizeEvent(self, event):
        super().resizeEvent(event); self.fit_node()
