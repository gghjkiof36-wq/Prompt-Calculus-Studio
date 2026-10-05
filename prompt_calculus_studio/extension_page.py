"""Companion installation is available before connecting or binding a workflow."""
import os
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QBoxLayout, QFileDialog, QCheckBox, QLineEdit

from .extension_install import InstallPreferences, available_package, build_info, comfy_root, installed, update_state
from .widgets import label, button, panel, row


class ExtensionPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.preferences = InstallPreferences(window.store.directory)
        self.base, self.package = available_package()
        self.candidate = build_info(self.package)
        self.process = QProcess(self)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.output = bytearray()
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.readyReadStandardError.connect(self.read_output)
        self.busy = False
        self.waiting = False
        self.stopped = False
        self.message = self.preferences.error
        self.retry_timer = QTimer(self)
        self.retry_timer.setInterval(15000)
        self.retry_timer.timeout.connect(self.retry_when_stopped)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        frame, body = panel('SettingsGroup')
        frame.setMaximumWidth(920)
        body.addWidget(label('PCS 配套擴充', 'DialogTitle'))
        body.addWidget(label('選擇 ComfyUI 資料夾後，自動安裝到 custom_nodes。不需要先連線或綁定工作流。', 'Subtle', True))
        self.location = QLineEdit()
        self.location.setReadOnly(True)
        self.location.setPlaceholderText('尚未指定 ComfyUI 資料夾')
        self.version = label('', None, True)
        self.status = label('', None, True)
        body.addWidget(self.location)
        body.addWidget(self.version)
        body.addWidget(self.status)
        self.choose = button('選擇資料夾並安裝', self.select_folder, 'Primary')
        self.update = button('安裝／更新', self.install)
        self.check = button('重新檢查', self.check_now, 'Quiet')
        self.actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        for control in (self.choose, self.update, self.check):
            self.actions.addWidget(control)
        self.actions.addStretch()
        body.addLayout(self.actions)
        self.automatic = QCheckBox('隨 PCS 自動更新配套擴充')
        self.automatic.setChecked(self.preferences.value['automatic'])
        self.automatic.toggled.connect(self.set_automatic)
        body.addWidget(self.automatic)
        body.addWidget(label('自動更新只在 ComfyUI 關閉時進行，會先備份原擴充並保留設定。完成後請啟動 ComfyUI，並重新整理已開啟的頁面。', 'Subtle', True))
        self.log = button('檢視安裝紀錄', self.open_log, 'Quiet')
        body.addLayout(row(self.log, None))
        layout.addWidget(frame)
        layout.addStretch()
        window.comfy.stateChanged.connect(self.render)
        self.render()
        QTimer.singleShot(0, self.startup)

    def selection(self):
        root = comfy_root(self.preferences.value['root'])
        previous = installed(root)
        return root, previous, build_info(previous) if previous else {}

    def startup(self):
        if not self.stopped and self.preferences.value['root'] and not self.preferences.error:
            self.check_now(automatic=True)

    def render(self):
        self.location.setText(self.preferences.value['root'])
        self.location.setToolTip(self.preferences.value['root'])
        self.location.setAccessibleName('ComfyUI 安裝位置')
        previous_info = {}
        state = 'unavailable'
        detail = self.preferences.error
        try:
            if self.preferences.value['root']:
                root, previous, previous_info = self.selection()
                state = update_state(self.candidate, previous_info) if not previous or previous_info else 'unknown'
                loaded = getattr(self.window.comfy, 'extension_info', {}) if getattr(self.window.comfy, 'connected', False) else {}
                if state == 'current':
                    same_path = bool(loaded.get('root') and os.path.normcase(os.path.abspath(loaded['root'])) == os.path.normcase(str(previous)))
                    loaded_current = same_path and loaded.get('git_head') == self.candidate['git_head']
                    detail = '已安裝並載入配套版本' if loaded_current else '已安裝配套版本；啟動或重啟 ComfyUI 後確認載入'
                    if loaded_current and self.message == '配套擴充已安裝；請啟動 ComfyUI 並重新整理頁面。':
                        self.message = ''
                elif state == 'newer':
                    detail = '已安裝較新版本，這份 PCS 不會將它降版。'
                elif state == 'unknown':
                    detail = '無法確認現有擴充版本，已停止自動覆寫；請保留備份並核對安裝來源。'
                else:
                    detail = '有配套更新' if previous else '尚未安裝 PCS 擴充'
        except (ValueError, OSError) as exc:
            detail = str(exc)
        if not self.candidate:
            detail = '這份程式未附配套擴充，請使用完整的 PCS 安裝包。'
        self.version.setText('隨附版本：' + self.candidate.get('version', '未提供') + '\n已安裝：' + previous_info.get('version', '未確認'))
        self.status.setText(self.message or detail)
        self.choose.setEnabled(bool(self.candidate) and not self.busy)
        self.update.setEnabled(not self.busy and state in ('install', 'update'))
        self.update.setText('更新配套擴充' if state == 'update' else '安裝配套擴充')
        self.check.setEnabled(not self.busy)
        self.automatic.setEnabled(not self.busy)
        self.log.setEnabled((self.window.store.directory / 'extension-install.log').is_file())

    def open_log(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.window.store.directory / 'extension-install.log')))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.actions.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 680 else QBoxLayout.Direction.LeftToRight)

    def select_folder(self):
        path = QFileDialog.getExistingDirectory(self, '選擇 ComfyUI 或 custom_nodes 資料夾', self.preferences.value['root'])
        if not path:
            return
        try:
            root = comfy_root(path)
            installed(root)
            self.waiting = False
            self.retry_timer.stop()
            self.preferences.value['root'] = str(root)
            self.preferences.save()
            self.message = ''
            self.install()
        except (ValueError, OSError) as exc:
            self.message = str(exc)
        self.render()

    def set_automatic(self, enabled):
        self.preferences.value['automatic'] = enabled
        try:
            self.preferences.save()
        except OSError:
            self.message = '無法儲存安裝設定。'
            self.render()
            return
        if not enabled:
            if self.waiting:
                self.message = '請先關閉 ComfyUI，再按安裝／更新。'
            self.waiting = False
            self.retry_timer.stop()
        elif self.preferences.value['root']:
            self.check_now(automatic=True)
        self.render()

    def check_now(self, automatic=False):
        if self.busy or self.stopped:
            return
        self.message = ''
        self.render()
        if automatic and self.preferences.value['automatic']:
            self.install(automatic=True)

    def install(self, automatic=False):
        if self.busy or self.stopped or not self.candidate:
            return
        try:
            root, previous, previous_info = self.selection()
            state = update_state(self.candidate, previous_info) if not previous or previous_info else 'unknown'
            if state not in ('install', 'update'):
                self.render()
                return
            if os.name != 'nt':
                raise ValueError('目前的自動安裝支援 Windows。')
            script = self.base / 'install_comfyui.ps1'
            if not script.is_file():
                raise ValueError('安裝程式缺失，請使用完整的 PCS 安裝包。')
            self.preferences.save()
            self.output.clear()
            self.busy = True
            self.waiting = False
            self.retry_timer.stop()
            self.message = '正在核對並安裝配套擴充…'
            env = QProcessEnvironment.systemEnvironment()
            system = Path(os.environ.get('SystemRoot', 'C:/Windows'))
            env.insert('PSModulePath', str(system / 'System32/WindowsPowerShell/v1.0/Modules'))
            self.process.setProcessEnvironment(env)
            self.process.setProgram(str(system / 'System32/WindowsPowerShell/v1.0/powershell.exe'))
            self.process.setArguments(['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                '-Source', str(self.package), '-ComfyUIRoot', str(root), '-DesktopData', str(self.window.store.directory),
                '-Update', '-PreserveLibrary', '-RequireStopped'])
            self.process.start()
        except (ValueError, OSError) as exc:
            self.message = str(exc)
        self.render()

    def read_output(self):
        self.output.extend(bytes(self.process.readAllStandardOutput()))
        self.output.extend(bytes(self.process.readAllStandardError()))
        self.output[:] = self.output[-32768:]

    def finished(self, code, status):
        self.read_output()
        self.busy = False
        if code == 20 and status == QProcess.ExitStatus.NormalExit:
            self.message = '請先關閉 ComfyUI；關閉後會自動接續安裝。' if self.preferences.value['automatic'] else '請先關閉 ComfyUI，再按安裝／更新。'
            self.waiting = self.preferences.value['automatic']
            if self.waiting:
                self.retry_timer.start()
        elif code == 0 and status == QProcess.ExitStatus.NormalExit:
            self.message = '配套擴充已安裝；請啟動 ComfyUI 並重新整理頁面。'
        elif code == 21:
            self.message = '上次安裝結果尚未確認，請檢視安裝紀錄與備份；已停止自動更新。'
        else:
            self.message = '安裝未完成，請查看安裝紀錄；不會自動重試。'
        try:
            (self.window.store.directory / 'extension-install.log').write_bytes(bytes(self.output))
        except OSError:
            pass
        self.render()

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.busy = False
            self.message = '無法啟動安裝程式；請檢查完整安裝包與 Windows PowerShell。'
            self.render()

    def retry_when_stopped(self):
        if self.waiting and self.preferences.value['automatic'] and not self.busy:
            self.install(automatic=True)

    def shutdown(self):
        self.stopped = True
        self.retry_timer.stop()
