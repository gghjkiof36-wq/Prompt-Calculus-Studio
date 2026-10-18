"""Document-bound text placement; a single transaction and the normal writer."""
import copy
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF

from . import composition, materials, prompt_materials, multi_output
from .core import uid
from .local_documents import assets, io, paths, schema
from .workspace_locations import path_key


@dataclass
class MaterialSource:
    document: object
    document_id: str
    ident: str
    value: dict


@dataclass
class MaterialTarget:
    path: Path
    document_id: str
    document: object = None


class WorkspaceMaterials(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.w = workspace
        self.drag = None
        self.notification = None

    def error(self, reason):
        if self.notification is None:
            from .workspace_transfer_notice import TransferNotice
            self.notification = TransferNotice(self.w.workspace_stack, self.w)
        self.notification.present(str(reason))

    def capture(self, page, ident):
        document = page.window.document
        if document not in self.w.documents or page.active_dialog:
            raise ValueError('請先完成或取消文字模塊的編輯。')
        value = materials.item(document.state, ident)
        if prompt_materials.kind(value) != 'text':
            raise ValueError('目前只支援加入文字模塊。')
        return MaterialSource(document, document.key, ident, copy.deepcopy(value))

    def validate_source(self, source):
        document = source.document
        if document not in self.w.documents or document.key != source.document_id:
            raise ValueError('來源文件已關閉或變更，請重新選擇。')
        if any(getattr(getattr(h, 'prompt_editor', None), 'active_dialog', None)
               for h in document.hosts):
            raise ValueError('請先完成或取消文字模塊的編輯。')
        if materials.item(document.state, source.ident) != source.value:
            raise ValueError('來源文字已更新，請重新選擇。')

    def target(self, path):
        path = self.w.locations.file(path)
        document = next((d for d in self.w.documents if path_key(d.path) == path_key(path)), None)
        loaded = io.load(path.parent, path.name)
        if loaded.document['kind'] != 'workflow':
            raise ValueError('請選擇 Canvas 文件。')
        if document and (document.key != loaded.document['document_id'] or document.receipt != loaded.receipt):
            raise ValueError('目標文件已在外部變更，請先處理文件衝突。')
        return MaterialTarget(path, loaded.document['document_id'], document)

    def resolve(self, target):
        document = target.document
        if document is not None:
            if document not in self.w.documents or document.key != target.document_id:
                raise ValueError('目標文件已關閉或另存，請重新選擇。')
            path = document.path
        else:
            path = target.path
        current = self.target(path)
        if current.document_id != target.document_id:
            raise ValueError('目標已換成另一份文件，請重新選擇。')
        return current

    def canvases(self, target):
        target = self.resolve(target)
        if target.document:
            state = target.document.state
        else:
            from .local_documents import workflow
            state = workflow.to_state(io.load(target.path.parent, target.path.name).document)
        return [(key, value['name']) for key, value in state['multi_output']['canvases'].items()]

    def candidates(self):
        found = {}
        for root in self.w.locations.roots:
            try:
                for path, kind in self.w.locations.inventory(root):
                    if kind == 'workflow': found[path_key(path)] = path
            except (ValueError, OSError):
                continue
        for document in self.w.documents:
            if document.kind == 'workflow': found[path_key(document.path)] = document.path
        return sorted(found.values(), key=lambda p: (p.stem.casefold(), str(p).casefold()))

    def copy_root(self, source):
        root = composition.clone_node(composition.prototype(source.value))
        prompt_materials.reset(root)
        root.update(source_id=source.ident, source_module=source.value['module'],
                    source_document=source.document_id,
                    source_revision=materials.fingerprint(source.value),
                    content_kind=source.value.get('content_kind', 'tags'), collapsed=True)
        if not root.get('preview'):root['preview'] = source.value.get('preview', '')
        # Read every required attachment before touching the destination. Only
        # known preview fields are copied; no catalog or arbitrary path export.
        pending = {}
        for part in composition.walk(root):
            reference = part.get('preview')
            if not reference: continue
            raw, _ = paths.read_bytes(paths.checked(source.document.data, reference), assets.MAX_ASSET_BYTES)
            entry = next((a for a in source.document.document['assets'] if a['reference'] == reference), None)
            digest = schema.digest(raw)
            if entry and digest != entry['sha256']:
                raise ValueError('來源附件與保存時不同，未加入文字。')
            suffix = Path(reference).suffix.lower()
            if not suffix[1:].isalnum() or len(suffix) > 11: suffix = '.bin'
            name = 'transfers/'+digest+suffix
            pending[name] = raw
            part['preview'] = name
        if sum(map(len, pending.values())) > assets.MAX_TOTAL_BYTES:
            raise ValueError('這個文字模塊的附件太大，未加入。')
        return root, pending

    @staticmethod
    def size(canvas, root, canvas_id):
        # Use the real card's geometry without adding an item or rebuilding
        # the scene. The temporary entry is removed immediately afterwards.
        from .canvas_items import NodeCard
        key = 'measure:'+uid()
        canvas.entries[key] = (key, root['id'])
        try:
            card = NodeCard(canvas, key, copy.deepcopy(root), '模組' if canvas_id else '未分配')
            return card.width, card.height
        finally:
            canvas.entries.pop(key, None)
            canvas.cards.pop(key, None)

    def position(self, canvas, root, canvas_id, point=None):
        width, height = self.size(canvas, root, canvas_id)
        if point is None:
            if canvas_id:
                x, y = canvas.data()['canvases'][canvas_id]['position']
                point = QPointF(x+24, y+108)
            else:
                saved = getattr(canvas.window, 'local_canvas_view', None)
                center = QPointF(*saved[1:]) if saved else canvas.view.mapToScene(canvas.view.viewport().rect().center())
                point = center-QPointF(width/2, height/2)
        occupied = []
        for collection in (canvas.cards, canvas.containers, canvas.outputs, canvas.clips,
                           canvas.flow_cards, canvas.preview_cards, canvas.functions.cards):
            for key, card in collection.items():
                if card.parentItem() is not None or key == canvas_id: continue
                occupied.append(card.sceneBoundingRect())
        # A hidden view has no populated scene yet. Its stored positions still
        # prevent new text from silently covering another saved text card.
        for key, value in canvas.window.state.get('uses', {}).items():
            if key not in canvas.cards:
                x, y = canvas.window.state.get('text_positions', {}).get(key, [-1500, 0])
                w, h = self.size(canvas, value, multi_output.owner(canvas.window.state, key))
                occupied.append(QRectF(x, y, w, h))
        for key, value in canvas.data()['canvases'].items():
            if key != canvas_id and key not in canvas.containers:
                occupied.append(QRectF(*value['position'], *value['display_size']))
        offsets = [(0, 0)]
        for radius in range(1, 81):
            for x in range(-radius, radius+1):
                offsets.extend(((x*32, -radius*32), (x*32, radius*32)))
            for y in range(1-radius, radius):
                offsets.extend(((-radius*32, y*32), (radius*32, y*32)))
        for dx, dy in offsets:
            candidate = point+QPointF(dx, dy)
            if canvas_id:
                x, y = canvas.data()['canvases'][canvas_id]['position']
                if candidate.x() < x+24 or candidate.y() < y+108:continue
            rect = QRectF(candidate.x(), candidate.y(), width, height).adjusted(-8, -8, 8, 8)
            if not any(rect.intersects(other) for other in occupied):return candidate
        raise ValueError('附近沒有可用空間，請選擇另一個位置。')

    def add(self, source, target, canvas_id=None, point=None, host=None):
        created_files = []
        try:
            self.validate_source(source)
            target = self.resolve(target)
            if canvas_id is not None and canvas_id not in dict(self.canvases(target)):
                raise ValueError('目的畫布已移除，請重新選擇。')
            root, attachments = self.copy_root(source)
            document = target.document
            tab = next((t for t in self.w.all_pages() if t.document is document), None) if document else None
            if tab is None:
                tab = self.w.open_path(target.path, True, new_tab=True, background=True, quiet=True)
                if tab is None: raise ValueError(self.w.last_open_error)
                document = tab.document
            if document.key != target.document_id:
                raise ValueError('目標已換成另一份文件，請重新選擇。')
            if document.error or getattr(document, 'operation_error', False):
                raise ValueError(document.error or '目標文件目前不能寫入。')
            if self.w.autosave.editing_dialog(tab):
                raise ValueError('請先完成或取消目標文件的編輯。')
            host = host if host in document.hosts else tab.host
            canvas = host.canvas
            # Only the chosen file needs geometry. Build its hidden scene once
            # to avoid every node type, without showing it or changing focus.
            if not canvas.isVisible() and getattr(canvas, '_scene_refresh_pending', True):
                canvas.refresh(prepare_hidden=True)
            position = self.position(canvas, root, canvas_id, point)
            for name, raw in attachments.items():
                path = paths.checked(document.data, name)
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.exists():
                    if path.read_bytes() != raw: raise ValueError('附件目的地內容不符。')
                else:
                    with path.open('xb') as stream: stream.write(raw)
                    created_files.append(path)
            key = uid()
            def apply(state):
                state.setdefault('uses', {})[key] = copy.deepcopy(root)
                state.setdefault('output_order', []).append(key)
                state.setdefault('text_positions', {})[key] = [position.x(), position.y()]
                if canvas_id is not None:
                    multi_output.assign(state, key, canvas_id)
                    # Extend only the destination's lower/right bounds, so
                    # ensure_bounds cannot clamp the new text onto a member.
                    container = state['multi_output']['canvases'][canvas_id]
                    x, y = container['position']
                    width, height = self.size(canvas, root, canvas_id)
                    old_width, old_height = container['display_size']
                    container['display_size'] = [max(old_width, position.x()-x+width+24),
                                                 max(old_height, position.y()-y+height+78)]
            host._material_transfer_reporting = True
            try:
                if not canvas.commit(apply, label='加入文字模塊'):
                    for path in created_files: path.unlink(missing_ok=True)
                    return False
            finally:
                host._material_transfer_reporting = False
            document.pending_material_uses = getattr(document, 'pending_material_uses', set()) | {key}
            self.w.autosave.schedule(tab)
            return key
        except (ValueError, OSError, KeyError) as error:
            for path in created_files: path.unlink(missing_ok=True)
            self.error(error)
            return False
