"""Qt-free, single-model llama.cpp transport and owned-process supervisor.

Nothing starts on import/construction. All blocking methods belong on the
dedicated service thread. Cancellation is the sole thread-safe control method.
"""
from __future__ import annotations

import ctypes
from http.client import HTTPConnection, HTTPException
import json
import os
import secrets
import socket
import subprocess
import time

from .engine import Cancellation
from .registry import validate_model
from .settings import SAMPLING_KEYS, resolve_runtime


class WindowsJob:
    """Kill-on-close applies only to the process handle we created."""
    def __init__(self):
        self.handle = None
        if os.name != 'nt':
            return
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                        ('PerJobUserTimeLimit', ctypes.c_longlong),
                        ('LimitFlags', wintypes.DWORD),
                        ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t),
                        ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t),
                        ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                         'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IoCounters),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                  ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise RuntimeError('無法建立本地引擎的程序保護。')
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise RuntimeError('無法設定本地引擎的程序保護。')

    def assign(self, process):
        if self.handle and not self._kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise RuntimeError('無法管理本地引擎的生命週期，已停止載入。')

    def close(self):
        if self.handle:
            self._kernel.CloseHandle(self.handle)
            self.handle = None


class LlamaRuntime:
    def __init__(self):
        self.process = None
        self.job = None
        self.port = 0
        self.key = ''
        self.model = None
        self.settings = None
        self.context_size = 0

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def _connect(self, cancel, timeout=180):
        cancel.check()
        if not self.alive:
            raise RuntimeError('本地引擎已停止，請重新載入模型。')
        connection = HTTPConnection('127.0.0.1', self.port, timeout=timeout)
        try:
            connection.connect()
            cancel.bind(connection.sock)
        except Exception:
            connection.close()
            raise
        return connection

    def _json(self, path, payload=None, *, cancel=None, timeout=10, authenticated=True):
        cancel = cancel or Cancellation()
        connection = self._connect(cancel, timeout)
        try:
            # Retain the socket until finally, so Cancellation can interrupt
            # reads even after HTTPConnection has handed us the response.
            headers = {'Content-Type': 'application/json', 'Connection': 'keep-alive'}
            if authenticated:
                headers['Authorization'] = 'Bearer ' + self.key
            body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
            connection.request('GET' if payload is None else 'POST', path, body, headers)
            response = connection.getresponse()
            data = response.read(16 * 1024 * 1024 + 1)
            cancel.check()
            if len(data) > 16 * 1024 * 1024:
                raise RuntimeError('本地引擎回覆超過允許大小。')
            if response.status != 200:
                raise RuntimeError(f'本地引擎無法處理 {path}（HTTP {response.status}）。')
            return json.loads(data)
        finally:
            cancel.unbind()
            connection.close()

    def start(self, model, settings, cancel):
        self.stop()
        cancel.check()
        validate_model(model)
        executable = resolve_runtime(settings['runtime_path'])
        # Reserve a candidate, then authenticate the newly spawned server before
        # accepting it. Never attach to an arbitrary service on a familiar port.
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            self.port = reservation.getsockname()[1]
        self.key = secrets.token_hex(32)
        args = [str(executable), '--model', model['path'], '--alias', model['id'],
                '--host', '127.0.0.1', '--port', str(self.port),
                '--ctx-size', str(settings['context_size']),
                '--n-gpu-layers', 'auto' if settings['gpu_layers'] == -1 else str(settings['gpu_layers']),
                '--parallel', '1',
                '--no-context-shift', '--no-webui', '--slots', '--no-warmup', '--log-disable',
                # Keep reasoning-model text in the supported content stream.
                # Automatic extraction would hide it in reasoning_content.
                '--reasoning-format', 'none']
        # User/global llama environment must not enable an agent, remote model
        # download, alternate host or prompt logging behind these controls.
        environment = {k: v for k, v in os.environ.items()
                       if not k.upper().startswith('LLAMA_') and k.upper() != 'HF_TOKEN'}
        environment['LLAMA_API_KEY'] = self.key
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            self.job = WindowsJob()
            self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            cwd=str(executable.parent), env=environment,
                                            creationflags=flags, start_new_session=os.name != 'nt')
            self.job.assign(self.process)
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                cancel.check()
                if not self.alive:
                    raise RuntimeError('本地引擎啟動失敗。請確認引擎版本、GPU 驅動及記憶體是否足夠。')
                try:
                    health = self._json('/health', cancel=cancel, timeout=1)
                    if health.get('status') == 'ok':
                        break
                except (OSError, HTTPException, ValueError, RuntimeError):
                    cancel.check()
                cancel.event.wait(.15)
            else:
                raise RuntimeError('模型載入逾時，已停止這次載入。')
            cancel.check()
            self._verify_private_endpoint(model, cancel)
            slots = self._json('/slots', cancel=cancel)
            if not isinstance(slots, list) or len(slots) != 1 or type(slots[0].get('n_ctx')) is not int:
                raise RuntimeError('引擎不支援此版本需要的單一生成槽，請選擇相容的 llama-server。')
            self.context_size = min(settings['context_size'], slots[0]['n_ctx'])
            if self.context_size <= settings['max_tokens']:
                raise RuntimeError('實際上下文不足，請調低回覆長度後重新載入。')
            # Capability probe uses tokenizer/template only, never generation.
            self._prompt_tokens([{'role': 'user', 'content': 'Hello'}], cancel)
            self.model, self.settings = dict(model), dict(settings)
        except Exception:
            self.stop()
            raise

    def _verify_private_endpoint(self, model, cancel):
        info = self._json('/v1/models', cancel=cancel)
        if not isinstance(info, dict) or not any(m.get('id') == model['id'] for m in info.get('data', [])
                                                if isinstance(m, dict)):
            raise RuntimeError('本地引擎身分不符，已拒絕連線。')
        # Prove the key is enforced; /health itself intentionally is public.
        connection = self._connect(cancel, 5)
        try:
            connection.request('GET', '/v1/models', headers={'Connection': 'keep-alive'})
            response = connection.getresponse()
            if response.status not in (401, 403):
                raise RuntimeError('引擎未啟用必要的本機驗證，已停止載入。')
        finally:
            cancel.unbind()
            connection.close()
        if not self.alive:
            raise RuntimeError('本地引擎在載入時停止。')

    def _prompt_tokens(self, messages, cancel):
        # llama.cpp uses the same chat formatter for this endpoint and chat
        # completions. Its text completion path tokenizes with true/true too.
        # The actual engine build still needs a fixture-based parity check.
        rendered = self._json('/apply-template', {'messages': messages}, cancel=cancel, timeout=180)
        if not isinstance(rendered, dict) or not isinstance(rendered.get('prompt'), str):
            raise RuntimeError('引擎未提供聊天模板，無法安全組合上下文。')
        tokenized = self._json('/tokenize', {'content': rendered['prompt'], 'add_special': True,
                                          'parse_special': True}, cancel=cancel, timeout=180)
        tokens = tokenized.get('tokens') if isinstance(tokenized, dict) else None
        if not isinstance(tokens, list) or any(type(t) is not int for t in tokens):
            raise RuntimeError('引擎未提供有效的 token 計數，無法檢查上下文。')
        return len(tokens)

    def generate(self, messages, params, cancel, on_delta):
        validate_model(self.model)
        count = self._prompt_tokens(messages, cancel)
        if count + params['max_tokens'] > self.context_size:
            raise RuntimeError(f'上下文不足：輸入約 {count} tokens，回覆預留 {params["max_tokens"]}，'
                               f'目前上限 {self.context_size}。請縮短對話或重新載入較大的上下文。')
        payload = {'model': self.model['id'], 'messages': messages, 'stream': True,
                   **{key: params[key] for key in SAMPLING_KEYS}}
        connection = self._connect(cancel)
        pieces = []
        size = 0
        reason = None
        done = False
        deadline = time.monotonic() + 1800
        try:
            connection.request('POST', '/v1/chat/completions',
                               json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                               {'Content-Type': 'application/json', 'Accept': 'text/event-stream',
                                'Authorization': 'Bearer ' + self.key, 'Connection': 'keep-alive'})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError(f'本地模型無法開始回覆（HTTP {response.status}）。')
            if 'text/event-stream' not in response.getheader('Content-Type', '').lower():
                raise RuntimeError('引擎未使用預期的串流格式。')
            data_lines = []
            event_bytes = 0
            while True:
                cancel.check()
                if time.monotonic() > deadline:
                    raise RuntimeError('本次回覆逾時，已停止生成。')
                raw = response.readline(1024 * 1024 + 1)
                if not raw:
                    break
                event_bytes += len(raw)
                if event_bytes > 1024 * 1024:
                    raise RuntimeError('本地引擎串流區塊過大。')
                line = raw.decode('utf-8').rstrip('\r\n')
                if line.startswith('data:'):
                    data_lines.append(line[5:].lstrip(' '))
                    continue
                if line:
                    continue
                if not data_lines:
                    event_bytes = 0
                    continue
                data = '\n'.join(data_lines)
                data_lines, event_bytes = [], 0
                if data == '[DONE]':
                    done = True
                    break
                item = json.loads(data)
                if not isinstance(item, dict) or item.get('error'):
                    raise RuntimeError('模型在生成途中回報錯誤。')
                choices = item.get('choices', [])
                if not isinstance(choices, list):
                    raise RuntimeError('模型回覆格式無效。')
                for choice in choices:
                    if not isinstance(choice, dict) or choice.get('index', 0) != 0:
                        raise RuntimeError('模型回覆選項無效。')
                    delta = choice.get('delta') or {}
                    if not isinstance(delta, dict) or delta.get('tool_calls'):
                        raise RuntimeError('此版聊天尚未支援工具呼叫。')
                    text = delta.get('content')
                    if text is not None:
                        if not isinstance(text, str):
                            raise RuntimeError('模型未回傳純文字內容。')
                        size += len(text)
                        if size > 8 * 1024 * 1024:
                            raise RuntimeError('本次回覆過長，已停止生成。')
                        if text:
                            pieces.append(text)
                            on_delta(text)
                    if choice.get('finish_reason') is not None:
                        reason = choice['finish_reason']
            cancel.check()
            if not done or reason not in ('stop', 'length'):
                raise RuntimeError('模型回覆中斷或沒有正常結束，已保留收到的內容。')
            return ('limited' if reason == 'length' else 'completed'), ''.join(pieces)
        finally:
            cancel.unbind()
            connection.close()

    def ensure_idle(self, timeout=3):
        """A closed stream is not proof of cancelled inference. Verify or unload."""
        deadline = time.monotonic() + timeout
        while self.alive and time.monotonic() < deadline:
            try:
                slots = self._json('/slots', timeout=.5)
                if isinstance(slots, list) and len(slots) == 1 and slots[0].get('is_processing') is False:
                    return True
            except (OSError, HTTPException, ValueError, RuntimeError):
                pass
            time.sleep(.05)
        self.stop()
        return False

    def stop(self):
        process, job = self.process, self.job
        # Do not drop ownership until the child is known to have exited.
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        finally:
            if job is not None:
                job.close()
            if process is not None and process.poll() is None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            if process is None or process.poll() is not None:
                self.process, self.job = None, None
                self.model, self.settings = None, None
                self.context_size, self.port, self.key = 0, 0, ''
        if self.alive:
            raise RuntimeError('本地引擎尚未完全停止，仍在清理程序。')
