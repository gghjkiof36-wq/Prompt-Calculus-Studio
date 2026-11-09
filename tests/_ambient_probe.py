"""Fixed-frame paint probe shared by the ambient regression and B1-7 evidence."""
import time
from contextlib import nullcontext
from unittest.mock import patch

from PySide6.QtCore import QEvent, QObject


class PaintCounter(QObject):
    def __init__(self, widget):
        super().__init__()
        self.count = 0
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            self.count += 1
        return False


def canvas_fixture(case):
    tab = case.open('Street.pcs-workflow')
    canvas = tab.host.canvas
    from prompt_calculus_studio import materials
    key = next(iter(canvas.data()['canvases']))
    def add_card(state):
        source = materials.save(state, dict(name='Ambient probe', prompt='sample text', module=None))
        materials.add_to_canvas(state, source, key)
    for _ in range(2):
        canvas.commit(add_card)
    case.events()
    case.assertGreaterEqual(len(canvas.cards), 3)
    return canvas


def measure(case, content, effect, frames=60, suppress_update=False):
    ambient = case.w.ambient
    case.w.set_ambient_effect(effect)
    case.events()
    if effect != 'off':
        case.assertTrue(ambient.isVisible())
    ambient.timer.stop()  # Exactly one advance per iteration, with no timer races.
    ambient.random.seed(7)
    ambient.particles.clear()
    for _ in range(4):
        case.app.processEvents()
    content_paints, ambient_paints = PaintCounter(content), PaintCounter(ambient)
    advances = 0
    started = time.monotonic()
    try:
        with patch.object(ambient, 'update') if suppress_update else nullcontext():
            for _ in range(frames):
                if effect != 'off':
                    ambient.last_frame = time.monotonic() - 1/30
                    ambient.advance()
                    advances += 1
                for _ in range(3):
                    case.app.processEvents()
        return dict(effect=effect, frames=frames, advances=advances,
                    content_paints=content_paints.count, ambient_paints=ambient_paints.count,
                    particles=len(ambient.particles), suppress_update=suppress_update,
                    seconds=round(time.monotonic()-started, 4))
    finally:
        content.removeEventFilter(content_paints)
        ambient.removeEventFilter(ambient_paints)
