"""Qt-free, single-model llama.cpp transport and owned-process supervisor.

Nothing starts on import/construction. All blocking methods belong on the
dedicated service thread. Cancellation is the sole thread-safe control method.
"""
from __future__ import annotations

import ctypes
import hashlib
from http.client import HTTPConnection, HTTPException
import json
import os
import secrets
import socket
import subprocess
import time

from .engine import Cancellation
from .registry import validate_model
from .reasoning import normalize_reasoning
from .settings import SAMPLING_KEYS, resolve_runtime


# Verified against Qwen/Qwen3.5-9B's published template and llama.cpp b11900.
# Do not infer a model capability from an editable display name or file name.
QWEN35_TEMPLATE_SHA256 = 'a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715'
# Actual local Gemma 4 E4B template; on/off rendering verified on b11900.
GEMMA4_TEMPLATE_SHA256 = '55572b8d3c8342044e25874c73fe5234b661fa0a57a57f6ef75b58e03d7d959a'
REASONING_BUILD = 'b11900-'


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
        self._reasoning_props = None
        self._thinking_checked = False
        self._budget_checked = False

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def _connect(self, cancel, timeout=180):
        cancel.check()
        if not self.alive:
            raise RuntimeError('本地引擎已停止，請重新傳送訊息。')
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
                raise RuntimeError('實際上下文不足，請調低回覆上限後重新傳送。')
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

    def _reasoning_options(self, params, cancel):
        """Resolve the saved policy on this owned engine, without inference.

        Null budget is a versioned model default, not a claimed measured token
        count. Explicit controls fail closed rather than rely on ignored keys.
        """
        if 'reasoning' not in params:
            return {}
        policy = normalize_reasoning(params['reasoning'], max_tokens=params['max_tokens'])
        enabled, budget = policy['enable_thinking'], policy['reasoning_budget_tokens']
        if enabled is None and budget == -1:
            return {}  # Explicitly keep the engine's unlimited/default budget.
        if self._reasoning_props is None:
            try:
                props = self._json('/props', cancel=cancel)
            except (OSError, HTTPException, ValueError, RuntimeError):
                cancel.check()
                if enabled is None and budget is None:
                    return {}  # Unknown model: follow its existing behavior.
                raise RuntimeError('無法確認思考控制支援。請改用相容引擎，或回復依模型設定。') from None
            self._reasoning_props = props if isinstance(props, dict) else {}
        props = self._reasoning_props
        template = props.get('chat_template', '')
        template_hash = hashlib.sha256(template.encode('utf-8')).hexdigest() if isinstance(template, str) else ''
        known_qwen = template_hash == QWEN35_TEMPLATE_SHA256
        known_gemma = template_hash == GEMMA4_TEMPLATE_SHA256
        if enabled is None and budget is None and not known_qwen:
            return {}
        build = props.get('build_info', '')
        if not isinstance(build, str) or not build.startswith(REASONING_BUILD):
            raise RuntimeError('此引擎尚未確認支援思考控制。請使用 llama.cpp b11900，或將思考上限設為不限。')
        if enabled is not None and not (known_qwen or known_gemma):
            raise RuntimeError('此模型尚未確認支援思考切換。請將思考模式設為依模型。')
        if known_gemma and budget is not None and budget >= 0:
            raise RuntimeError('此模型尚未確認支援思考上限。請將思考上限設為依模型或不限。')
        probe = [{'role': 'user', 'content': 'Hello'}]
        if (known_qwen or known_gemma) and not self._thinking_checked:
            on = self._json('/apply-template', dict(messages=probe, chat_template_kwargs={'enable_thinking': True}), cancel=cancel)
            off = self._json('/apply-template', dict(messages=probe, chat_template_kwargs={'enable_thinking': False}), cancel=cancel)
            valid = (isinstance(on, dict) and isinstance(off, dict)
                     and isinstance(on.get('prompt'), str) and isinstance(off.get('prompt'), str)
                     and on['prompt'] != off['prompt'])
            if valid:
                valid = (on['prompt'].rstrip().endswith('<think>') and off['prompt'].rstrip().endswith('</think>')) if known_qwen else (
                    '<|turn>system\n<|think|>' in on['prompt'] and '<|think|>' not in off['prompt'])
            if not valid:
                raise RuntimeError('引擎未套用思考模式。請確認模型與引擎版本。')
            self._thinking_checked = True
        options = {}
        if enabled is not None:
            options['chat_template_kwargs'] = {'enable_thinking': enabled}
        if enabled is False:
            return options
        if budget is None:
            budget = min(512, params['max_tokens'] - 1) if known_qwen else -1
        if budget >= 0:
            if not known_qwen and not self._budget_checked:
                rendered = self._json('/apply-template', {'messages': probe}, cancel=cancel)
                if (not isinstance(rendered, dict) or not isinstance(rendered.get('prompt'), str)
                        or not rendered['prompt'].rstrip().endswith('<think>')):
                    raise RuntimeError('此模型尚未確認支援思考上限。請將思考上限設為不限。')
                # b11900's differential parser must discover an end marker;
                # an open prefill alone is not evidence of a working budget.
                marker = 'PCS_REASONING_CAPABILITY_PROBE'
                assistant = {'role': 'assistant', 'content': 'Answer'}
                plain = self._json('/apply-template', dict(messages=probe + [assistant], add_generation_prompt=False), cancel=cancel)
                reasoned = self._json('/apply-template', dict(messages=probe + [dict(assistant, reasoning_content=marker)], add_generation_prompt=False), cancel=cancel)
                text = reasoned.get('prompt') if isinstance(reasoned, dict) else None
                if (not isinstance(plain, dict) or not isinstance(plain.get('prompt'), str)
                        or not isinstance(text, str) or marker in plain['prompt']
                        or marker not in text or '<think>' not in text.split(marker, 1)[0]
                        or '</think>' not in text.split(marker, 1)[1]):
                    raise RuntimeError('此模型尚未確認支援思考上限。請將思考上限設為不限。')
                self._budget_checked = True
            options['reasoning_budget_tokens'] = budget
        return options

    def _prompt_tokens(self, messages, cancel, options=None):
        # llama.cpp uses the same chat formatter for this endpoint and chat
        # completions. Its text completion path tokenizes with true/true too.
        # The actual engine build still needs a fixture-based parity check.
        rendered = self._json('/apply-template', {'messages': messages, **(options or {})}, cancel=cancel, timeout=180)
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
        options = self._reasoning_options(params, cancel)
        count = self._prompt_tokens(messages, cancel, options)
        if count + params['max_tokens'] > self.context_size:
            raise RuntimeError(f'上下文不足：輸入約 {count} tokens，回覆預留 {params["max_tokens"]}，'
                               f'目前上限 {self.context_size}。請縮短對話，或調高上下文容量後重新傳送。')
        payload = {'model': self.model['id'], 'messages': messages, 'stream': True,
                   **{key: params[key] for key in SAMPLING_KEYS}, **options}
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

    def _tool_options(self, params, cancel, tools, final):
        validate_model(self.model)
        if self._reasoning_props is None:
            self._reasoning_props = self._json('/props', cancel=cancel)
        props = self._reasoning_props
        caps = props.get('chat_template_caps', {})
        if caps.get('supports_tools') is not True or caps.get('supports_tool_calls') is not True:
            raise RuntimeError('此模型的聊天模板未支援工具呼叫。請改選支援工具的本地模型。')
        options = self._reasoning_options(params, cancel)
        return dict(options, tools=tools, tool_choice='none' if final else 'auto', parallel_tool_calls=False)

    def tool_budget(self, messages, params, cancel, tools, *, final=False):
        """Count the actual engine template; reuse this exact preflight on send."""
        cancel.check()
        options = self._tool_options(params, cancel, tools, final)
        key = hashlib.sha256(json.dumps([self.model['id'], messages, options], ensure_ascii=False,
                                      sort_keys=True).encode('utf8')).hexdigest()
        cached = getattr(self, '_tool_token_cache', None)
        if cached is None or cached[0] != key:
            cached = (key, self._prompt_tokens(messages, cancel, options))
            self._tool_token_cache = cached
        return dict(input=cached[1], output=params['max_tokens'], capacity=self.context_size)

    def tool_turn(self, messages, params, cancel, tools, *, final=False):
        """One structured tool round, on the same owned server and single slot."""
        budget = self.tool_budget(messages, params, cancel, tools, final=final)
        count = budget['input']
        if count + params['max_tokens'] > self.context_size:
            raise RuntimeError(f'Agent 上下文不足：輸入 {count} tokens，回覆預留 {params["max_tokens"]}，'
                               f'上限 {self.context_size}。請減少規則／讀取內容或調高模型上下文。')
        payload = dict(model=self.model['id'], messages=messages, stream=False,
                       **{key: params[key] for key in SAMPLING_KEYS}, **self._tool_options(params, cancel, tools, final))
        value = self._json('/v1/chat/completions', payload, cancel=cancel, timeout=180)
        choices = value.get('choices') if isinstance(value, dict) else None
        if not isinstance(choices, list) or len(choices) != 1:
            raise RuntimeError('模型工具回覆格式無效。')
        choice = choices[0]
        if not isinstance(choice, dict):
            raise RuntimeError('模型工具回覆格式無效。')
        message, reason = choice.get('message'), choice.get('finish_reason')
        if not isinstance(message, dict) or message.get('role') != 'assistant' or reason not in ('stop', 'length', 'tool_calls'):
            raise RuntimeError('模型工具回覆未正常結束。')
        text = message.get('content') or ''
        calls = message.get('tool_calls') or []
        if not isinstance(text, str) or len(text) > 1_000_000 or not isinstance(calls, list) or len(calls) > 8:
            raise RuntimeError('模型工具回覆過大或格式無效。')
        ids = set()
        for call in calls:
            function = call.get('function') if isinstance(call, dict) else None
            if (not isinstance(call, dict) or call.get('type') != 'function' or not isinstance(call.get('id'), str)
                    or not 1 <= len(call['id']) <= 128 or call['id'] in ids or not isinstance(function, dict)
                    or not isinstance(function.get('name'), str) or len(function['name']) > 128
                    or not isinstance(function.get('arguments'), str) or len(function['arguments']) > 8192):
                raise RuntimeError('模型工具參數格式無效。')
            ids.add(call['id'])
        if bool(calls) != (reason == 'tool_calls'):
            raise RuntimeError('模型工具回覆狀態不一致。')
        if final and calls:
            raise RuntimeError('模型在收尾階段仍要求工具，本輪未完成。')
        return ('limited' if reason == 'length' else 'completed'), dict(role='assistant', content=text, **({'tool_calls': calls} if calls else {}))

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
                self._reasoning_props, self._thinking_checked, self._budget_checked = None, False, False
                self._tool_token_cache = None
        if self.alive:
            raise RuntimeError('本地引擎尚未完全停止，仍在清理程序。')
