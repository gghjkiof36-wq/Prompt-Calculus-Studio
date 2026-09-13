"""Qt completion: debounced background requests, IME-safe insertion, local cache."""
import json
import time
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Qt, QStringListModel
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QCompleter
from .core import current_token, insertion, contains_chinese, format_candidate
from .network import danbooru, google_translate, LookupError


class Signals(QObject):
    done = Signal(int, object, str)


class RequestTask(QRunnable):
    def __init__(self, serial, token, provider, api_key):
        super().__init__()
        self.serial, self.token, self.provider, self.api_key = serial, token, provider, api_key
        self.signals = Signals()

    def run(self):
        try:
            if contains_chinese(self.token.query) and self.provider == "google" and not self.token.artist:
                rows = google_translate(self.token.query, self.api_key)
            else:
                rows = danbooru(self.token.query, self.token.artist)
            self.signals.done.emit(self.serial, rows, "")
        except LookupError as exc:
            self.signals.done.emit(self.serial, [], str(exc))
        except Exception:
            self.signals.done.emit(self.serial, [], "查詢失敗，請稍後再試。")


class PromptEdit(QPlainTextEdit):
    accepted = Signal(object)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.composing = False
        self.inserting = False
        self.expected = None
        self.candidates = {}
        self.model = QStringListModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setWidget(self)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setMaxVisibleItems(10)
        self.completer.popup().setObjectName("CompletionPopup")
        self.completer.activated[str].connect(self.insert_completion)
        self.textChanged.connect(self.schedule)
        self.cursorPositionChanged.connect(self.schedule)

    def context(self):
        # QTextCursor positions use UTF-16 units; Python strings use code points.
        text = self.toPlainText()
        pos = len(text.encode("utf-16-le")[:self.textCursor().position()*2].decode("utf-16-le", errors="ignore"))
        return current_token(text, pos)

    def schedule(self):
        if self.inserting or self.composing or self.isReadOnly() or not self.hasFocus():
            return
        self.completer.popup().hide()
        self.service.schedule(self)

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)
        if self.composing:
            self.completer.popup().hide()
            self.service.cancel(self)
        else:
            self.schedule()

    def keyPressEvent(self, event):
        popup = self.completer.popup()
        if popup.isVisible() and not self.composing:
            if event.key() == Qt.Key.Key_Escape:
                popup.hide(); return
            if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab):
                index = popup.currentIndex()
                if index.isValid():
                    self.insert_completion(index.data()); return
        super().keyPressEvent(event)

    def display(self, token, rows):
        if not self.hasFocus() or self.composing or token != self.context():
            return
        self.expected = (self.toPlainText(), token)
        self.candidates = {}
        settings = self.service.window.state["settings"]
        for row in rows[:12]:
            value = format_candidate(row, settings, token.artist)
            label = f"{value}    · {row['source']}" + (" · 繪師" if row.get("category") == 1 else "")
            self.candidates[label] = (value, row)
        if not self.candidates:
            self.completer.popup().hide(); return
        self.model.setStringList(list(self.candidates))
        self.completer.setCompletionPrefix("")
        rect = self.cursorRect()
        screen = self.screen().availableGeometry()
        rect.setWidth(min(500, max(280, self.width()), screen.width()-40))
        self.completer.complete(rect)
        self.completer.popup().setCurrentIndex(self.model.index(0))

    def insert_completion(self, label):
        if not self.expected or label not in self.candidates:
            return
        text, token = self.expected
        if self.toPlainText() != text or self.context() != token or self.composing:
            self.completer.popup().hide(); return
        value, row = self.candidates[label]
        start, end, replacement = insertion(text, token, value)
        self.inserting = True
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.setPosition(len(text[:start].encode("utf-16-le"))//2)
        cursor.setPosition(len(text[:end].encode("utf-16-le"))//2, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(replacement)
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.completer.popup().hide()
        self.inserting = False
        self.service.window.store.use(self.service.window.state["workspace"], row["value"],row)
        self.service.window.notice(f"已加入 {value}，可繼續輸入下一個片段。")
        self.accepted.emit(row)


class CompletionService(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.lookup)
        self.serial = 0
        self.editor = None
        self.active = None
        self.next_request = 0
        self.tasks = {}
        self.api_key = ""

    def cancel(self, editor):
        if self.editor is editor:
            self.serial += 1
            self.timer.stop()

    def schedule(self, editor):
        self.serial += 1
        self.editor = editor
        self.timer.start(600)

    def local(self, token):
        familiar=self.window.store.familiar(self.window.state["workspace"],token.query,token.artist)
        if token.artist:
            return familiar
        dictionary = self.window.state["dictionary"]
        if token.query in dictionary:
            return [dict(value=dictionary[token.query],category=None,count=0,source="自訂字典")]
        return [dict(value=v,category=None,count=0,source="自訂字典") for k,v in dictionary.items() if token.query in k][:8]+familiar

    def lookup(self):
        editor = self.editor
        try:
            if not editor or not editor.hasFocus() or editor.composing:
                return
        except RuntimeError:
            return
        token = editor.context()
        if token is None:
            return
        settings = self.window.state["settings"]
        local = self.local(token)
        if local:
            editor.display(token, local)
        # Exact personal glossary entries avoid unnecessary external translation.
        if token.query in self.window.state["dictionary"] and not token.artist:
            self.window.notice("自訂字典候選；點選或按 Enter／Tab 插入。")
            return
        provider = settings.get("translator","dictionary") if contains_chinese(token.query) else "dictionary"
        key = json.dumps([provider,token.query.casefold(),token.artist],ensure_ascii=False)
        cached = self.window.store.cached(key, 86400 if settings["online"] else None)
        if cached is not None:
            self.show(editor, token, local+cached)
            self.window.notice("已顯示快取候選。")
            return
        if not settings["online"]:
            self.window.notice("離線模式：此片段沒有快取，可手動輸入。")
            return
        if contains_chinese(token.query) and provider == "google" and not self.api_key:
            self.window.notice("Google 尚未設定金鑰；先查 Danbooru 的其他名稱。")
            provider = "dictionary"
            key = json.dumps([provider,token.query.casefold(),token.artist],ensure_ascii=False)
        if self.tasks or time.monotonic() < self.next_request:
            self.timer.start(max(200, int((self.next_request-time.monotonic())*1000)))
            return
        serial = self.serial
        task = RequestTask(serial, token, provider, self.api_key)
        self.active = (serial,editor,token,key,local)
        self.tasks[serial] = task
        task.signals.done.connect(self.finished)
        self.next_request = time.monotonic()+1.1
        self.window.notice("正在查詢…可繼續輸入，過時結果會略過。")
        self.pool.start(task)

    def show(self, editor, token, rows):
        seen = set()
        rows = [r for r in rows if not (r["value"] in seen or seen.add(r["value"]))]
        usage = self.window.store.usage(self.window.state["workspace"])
        rows.sort(key=lambda r:(r["source"]=="自訂字典",usage.get(r["value"],(0,0)),r.get("count") or 0),reverse=True)
        editor.display(token, rows)

    def finished(self, serial, rows, error):
        self.tasks.pop(serial,None)
        active = self.active
        if not active or active[0] != serial:
            return
        _, editor, token, key, local = active
        if not error:
            try:
                self.window.store.cache(key, rows)
            except Exception:
                self.window.notice("候選已取得，但快取未能寫入。")
        if serial != self.serial:
            return
        try:
            if error:
                self.window.notice(error)
            else:
                self.show(editor,token,local+rows)
                self.window.notice(f"找到 {len(rows)} 個候選；點選或 Enter／Tab 加入。" if rows else "沒有對應候選，可手動輸入或加入中文字典。")
        except RuntimeError:
            pass
