"""Offline reinstall/auth evidence; all transport is intercepted, credentials synthetic."""
import hashlib
import io
import json
import tempfile
import threading
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.response import addinfourl
import urllib.request as transport

from prompt_calculus_studio import civitai
from prompt_calculus_studio.credentials import save_token, load_token, token_path, clear_token
from prompt_calculus_studio.civitai_assets import make_plan, DownloadReceipts, perform_download, register_download
from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.media import Catalog


TOKEN = 'synthetic-reinstall-secret'
PAYLOAD = b'offline model fixture only'
URL = 'https://civitai.com/api/download/models/22'


class ReinstallSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.target = self.root / 'models'
        self.target.mkdir()
        self.store = Storage(self.root / 'data')
        self.catalog = Catalog(self.store)
        self.receipts = DownloadReceipts(self.store.directory)
        self.version = dict(id=22, modelId=11, files=[dict(id=33, name='fixture.safetensors',
            downloadUrl=URL, hashes={'SHA256': hashlib.sha256(PAYLOAD).hexdigest()})])
        self.parent = dict(id=11, name='Offline fixture', type='LORA')
        self.cancel = threading.Event()
        # A missed transport interception must fail rather than access the network.
        self.no_socket = patch('socket.socket.connect', side_effect=AssertionError('Network forbidden'))
        self.no_socket.start()

    def tearDown(self):
        self.no_socket.stop()
        self.store.close()
        self.tmp.cleanup()

    def plan(self):
        return make_plan(self.parent, self.version, self.version['files'][0], self.target,
                         'fixture.safetensors', '', self.target)

    def response(self, request, code=200, location=None, message='Fixture'):
        headers = Message()
        if location:
            headers['Location'] = location
        headers['Content-Length'] = str(len(PAYLOAD))
        result = addinfourl(io.BytesIO(PAYLOAD), headers, request.full_url, code)
        result.msg = message
        return result

    def test_delete_and_reinstall_keeps_dpapi_token_and_initial_authorization(self):
        save_token(self.store.directory, TOKEN)
        encrypted = token_path(self.store.directory).read_bytes()
        self.assertNotIn(TOKEN.encode(), encrypted)
        seen = []
        def respond(handler, request):
            seen.append((request.full_url, request.get_header('Authorization')))
            return self.response(request)
        with patch.object(transport.HTTPSHandler, 'https_open', respond):
            for attempt in range(2):
                record = perform_download(self.receipts, self.plan(), load_token(self.store.directory), self.cancel)
                row = register_download(self.catalog, record)
                if attempt == 0:
                    # Isolated substitute for moving the model to the Windows recycle bin.
                    Path(row['path']).unlink()
                    row['missing'] = True
                    self.catalog.put('model', row, row['root'])
        self.assertEqual(seen, [(URL, 'Bearer ' + TOKEN)] * 2)
        self.assertEqual(token_path(self.store.directory).read_bytes(), encrypted)
        self.assertEqual(load_token(self.store.directory), TOKEN)
        self.assertNotIn(TOKEN, json.dumps(self.receipts.rows()))
        clear_token(self.store.directory)
        self.assertEqual(load_token(self.store.directory), '')

    def test_redirect_authorization_and_fresh_request_after_failure(self):
        for destination, expected in [('https://civitai.com/next', 'Bearer ' + TOKEN),
                ('https://civitai.red/next', None), ('https://cdn.invalid/model', None),
                ('https://civitai.com:444/next', None)]:
            with self.subTest(destination=destination):
                seen = []
                def respond(handler, request):
                    seen.append(request.get_header('Authorization'))
                    return self.response(request, 302, destination) if len(seen) % 2 else self.response(request, 401)
                with patch.object(transport.HTTPSHandler, 'https_open', respond):
                    for _ in range(2):
                        with self.assertRaises((HTTPError, civitai.CivitAIError)):
                            perform_download(self.receipts, self.plan(), TOKEN, self.cancel)
                self.assertEqual(seen, ['Bearer ' + TOKEN, expected] * 2)
                self.assertFalse(list(self.target.iterdir()))

    def test_receipt_retry_reuses_url_snapshot_even_when_server_expires_it(self):
        plan = self.plan()
        seen = []
        def respond(handler, request):
            seen.append(request.full_url)
            return self.response(request, 401)
        with patch.object(transport.HTTPSHandler, 'https_open', respond):
            for _ in range(2):
                with self.assertRaises((HTTPError, civitai.CivitAIError)):
                    perform_download(self.receipts, plan, TOKEN, self.cancel)
                plan = self.receipts.get(plan['id'])
        self.assertEqual(seen, [URL, URL])

    def test_http_error_reason_cannot_reach_receipt_or_user_message(self):
        for status in (401, 403, 404, 429, 500):
            with self.subTest(status=status):
                plan = self.plan()
                def respond(handler, request):
                    return self.response(request, status, message='echoed ' + TOKEN)
                with patch.object(transport.HTTPSHandler, 'https_open', respond):
                    with self.assertRaises(civitai.CivitAIError) as raised:
                        perform_download(self.receipts, plan, TOKEN, self.cancel)
                self.assertEqual(raised.exception.status, status)
                self.assertNotIn(TOKEN, str(raised.exception))
                self.assertNotIn(TOKEN, json.dumps(self.receipts.get(plan['id'])))
                self.assertFalse(list(self.target.iterdir()))

    def test_transport_error_reason_cannot_reach_receipt_or_user_message(self):
        plan = self.plan()
        with patch.object(civitai, '_open_download', side_effect=URLError('https://host/?token=' + TOKEN)):
            with self.assertRaises(civitai.CivitAIError) as raised:
                perform_download(self.receipts, plan, TOKEN, self.cancel)
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertNotIn(TOKEN, json.dumps(self.receipts.get(plan['id'])))
        self.assertFalse(list(self.target.iterdir()))


if __name__ == '__main__':
    unittest.main()
