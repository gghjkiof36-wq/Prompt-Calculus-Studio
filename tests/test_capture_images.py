"""PNG capture publication, immutable originals and receipt recovery (no Qt)."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import uuid
import zlib

from prompt_calculus_studio import capture_images as images
from prompt_calculus_studio import connector_capture, connector_store
from prompt_calculus_studio import conversation_review, markdown_portable
from prompt_calculus_studio.conversation_archive import Cancelled, canonical, load_archive
from prompt_calculus_studio.conversation_import import Destination, RECEIPT, import_selected


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)


def png(width=2, height=2, *, raw=None, interlace=0, extra=b''):
    raw = raw if raw is not None else (b'\0' + b'\x12\x34\x56\xff' * width) * height
    return (images.PNG + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, interlace))
            + extra + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def uri(data):
    return images.PREFIX + base64.b64encode(data).decode('ascii')


def capture(content):
    return dict(version=1, provider='chatgpt', conversation_id='synthetic-png', title='合成圖表',
                url='https://chatgpt.com/c/synthetic-png', method='dom', scope='selection',
                adapter_version='1', captured_at='2026-10-24T00:00:00Z',
                messages=[dict(id='chart-1', role='assistant', content=content)])


class CaptureImageTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-capture-png-')
        self.addCleanup(self.area.cleanup)
        self.base = Path(self.area.name)
        self.root = self.base / 'workspace'; self.root.mkdir()
        self.source = self.base / 'capture.json'
        self.destination = Destination.choose(self.root)
        self.data = png()
        self.target = 'assets/' + hashlib.sha256(self.data).hexdigest() + '.png'

    def archive(self, text, *, ordinary=False):
        value = capture(text)
        if ordinary:
            value = connector_capture.validate_capture(value)
            value.pop('pcs_capture')
        self.source.write_bytes(canonical(value))
        return load_archive(self.source)

    def publish(self, text=None, **kwargs):
        archive = self.archive(text if text is not None else '數值 9.2\n\n![圖表](' + uri(self.data) + ')')
        before = self.source.read_bytes()
        raw = copy.deepcopy(archive.conversations[0].raw)
        result = import_selected(archive, (0,), self.destination, Event(), **kwargs)
        self.assertFalse(result.failed)
        self.assertEqual(len(result.completed), 1)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(archive.conversations[0].raw, raw)
        return archive, result.completed[0]

    def test_publication_deduplicates_png_and_preserves_original_json(self):
        text = '9.2\n\n![圖表](' + uri(self.data) + ')\n\n![同圖](' + uri(self.data) + ')'
        archive, note = self.publish(text)
        self.assertEqual((note.parent / self.target).read_bytes(), self.data)
        self.assertEqual(len(list((note.parent / 'assets').iterdir())), 1)
        saved = note.read_text(encoding='utf-8')
        self.assertEqual(saved.count(self.target), 2)
        self.assertNotIn('data:image', saved)
        self.assertIn('9.2', saved)
        self.assertIn(self.target, (note.parent / '分支-001-001.md').read_text(encoding='utf-8'))
        raw = json.loads((note.parent / '原始對話.json').read_bytes())
        self.assertEqual(raw, archive.conversations[0].raw)
        self.assertIn(uri(self.data), canonical(raw).decode())
        receipt = json.loads((note.parent / RECEIPT).read_bytes())
        self.assertEqual(receipt['capture_images'], {self.target: hashlib.sha256(self.data).hexdigest()})
        self.assertIn(self.target, receipt['files'])
        self.assertEqual(receipt['original_hash'], archive.conversations[0].digest)
        self.assertEqual(receipt['content_hash'], hashlib.sha256(canonical([receipt['original_hash'], {}])).hexdigest())
        reopened = conversation_review.Source.load(note.parent / '原始對話.json')
        self.assertEqual(reopened.item.raw, raw)

    def test_source_ranges_leave_code_examples_and_other_links_unchanged(self):
        image = '![圖表](' + uri(self.data) + ')'
        text = '`' + image + '`\n\n```markdown\n' + image + '\n```\n\n' + image
        _, note = self.publish(text)
        saved = note.read_text(encoding='utf-8')
        self.assertEqual(saved.count(uri(self.data)), 2)
        self.assertEqual(saved.count(self.target), 1)
        self.assertIn('```markdown\n' + image + '\n```', saved)

    def test_footnote_continuation_localizes_only_prose_image_source_ranges(self):
        value = uri(self.data)
        source = ('正文[^n]\n\n[^n]: Source\n\n'
                  '    - **Wiz Blog** · 2026-10-24  \n'
                  '      [文章](https://example.com/fix?version=002.50)\n'
                  '      ![縮圖](' + value + ')\n\n'
                  '    ```markdown\n    ![fenced](' + value + ')\n    ```\n\n'
                  '        ![indented](' + value + ')\n\n'
                  '    `![inline](' + value + ')`\n')
        archive, note = self.publish(source)
        saved = note.read_text(encoding='utf-8')
        self.assertEqual(saved.count(self.target), 1)
        self.assertEqual(saved.count(value), 3)
        self.assertIn('      ![縮圖](' + self.target + ')', saved)
        self.assertIn('[文章](https://example.com/fix?version=002.50)', saved)
        self.assertEqual(images.capture_image_hashes(archive.conversations[0].raw),
                         {self.target: hashlib.sha256(self.data).hexdigest()})
        self.assertEqual(json.loads((note.parent/'原始對話.json').read_bytes()), archive.conversations[0].raw)

    def test_footnote_invalid_image_rejects_before_any_publication(self):
        for invalid in (images.PREFIX+'AAAA BBBB', images.PREFIX+'%%%bad',
                        'data:image/svg+xml;base64,PHN2Zz4='):
            with self.subTest(invalid=invalid):
                archive = self.archive('正文[^n]\n\n[^n]: Source\n\n    - **Site**\n      ![image]('+invalid+')\n')
                result = import_selected(archive, (0,), self.destination, Event())
                self.assertTrue(result.failed)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_footnote_first_line_and_continuation_keep_crlf_source_offsets(self):
        value = uri(self.data)
        source = ('中文😀[^first] 與第二個[^second]\r\n\r\n'
                  '[^first]: ![首行]('+value+')\r\n\r\n'
                  '[^second]: Source\r\n\r\n    ![換行]('+value+')\r\n')
        archive, note = self.publish(source)
        saved = note.read_text(encoding='utf-8')
        self.assertEqual(saved.count(self.target), 2)
        self.assertNotIn(value, saved)
        self.assertIn('[^first]: ![首行]('+self.target+')', saved)
        self.assertIn('    ![換行]('+self.target+')', saved)
        self.assertEqual(json.loads((note.parent/'原始對話.json').read_bytes()), archive.conversations[0].raw)

    def test_plain_markdown_portable_footnote_asset_keeps_exact_source_bytes(self):
        (self.root/'thumb.png').write_bytes(self.data)
        note = self.root/'footnote.md'
        text = '正文[^n]\r\n\r\n[^n]: Source\r\n\r\n    - **Site**\r\n      ![縮圖](thumb.png)\r\n'
        before = text.encode('utf-8');note.write_bytes(before)
        package = markdown_portable.prepare(self.root, note, text)
        files = dict(package.bundle.files)
        self.assertEqual(files['footnote.md'], before)
        self.assertEqual(files['thumb.png'], self.data)
        self.assertEqual(note.read_bytes(), before)

    def test_footnote_like_yaml_and_fenced_examples_are_not_assets(self):
        image = '![example]('+uri(self.data)+')'
        source = '---\n[^n]: literal\n    '+image+'\n---\n\n```markdown\n[^n]: Example\n    '+image+'\n```\n'
        archive = self.archive(source)
        projected, assets = images.prepare_capture_images(archive.conversations[0])
        self.assertEqual(assets, {})
        self.assertEqual(projected.raw, archive.conversations[0].raw)

    def test_ordinary_json_keeps_existing_data_uri_behavior(self):
        archive = self.archive('![圖表](' + uri(self.data) + ')', ordinary=True)
        result = import_selected(archive, (0,), self.destination, Event())
        self.assertFalse(result.failed)
        folder = result.completed[0].parent
        self.assertFalse((folder / 'assets').exists())
        self.assertIn(uri(self.data), (folder / '分支-001-001.md').read_text(encoding='utf-8'))
        self.assertNotIn('capture_images', json.loads((folder / RECEIPT).read_bytes()))

    def test_bad_image_is_rejected_before_any_folder_even_after_a_valid_image(self):
        bad = [images.PREFIX + '%%%bad', images.PREFIX + 'AAAA BBBB',
               'data:image/svg+xml;base64,' + base64.b64encode(b'<svg/>').decode(),
               'data:text/html;base64,' + base64.b64encode(b'<script>bad()</script>').decode(),
               uri(b'not PNG'), uri(self.data[:-1]), uri(self.data + b'trailer'),
               uri(png(raw=b'\0')), uri(png(raw=b'\x05' + b'\0' * 8 + b'\0' * 9)),
               uri(png(raw=b'\0' * 19)), uri(png(4001, 4000, raw=b'\0')),
               uri(png(extra=chunk(b'zTXt', b'bad')))]
        damaged = bytearray(self.data); damaged[-1] ^= 1; bad.append(uri(bytes(damaged)))
        for value in bad:
            with self.subTest(value=value[:40]):
                archive = self.archive('![good](' + uri(self.data) + ')\n\n![bad](' + value + ')')
                result = import_selected(archive, (0,), self.destination, Event())
                self.assertTrue(result.failed)
                self.assertEqual(result.completed, [])
                self.assertEqual(list(self.root.iterdir()), [])

    def test_png_interlaced_single_pixel_and_limits(self):
        value = png(1, 1, interlace=1)
        self.assertEqual(images.png_bytes(uri(value)), value)
        with patch.object(images, 'MAX_IMAGE_BYTES', len(self.data) - 1), self.assertRaises(ValueError):
            images.png_bytes(uri(self.data))
        archive = self.archive('![chart](' + uri(self.data) + ')')
        with patch.object(images, 'MAX_CAPTURE_BYTES', 16):
            result = import_selected(archive, (0,), self.destination, Event())
        self.assertTrue(result.failed)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cancel_during_validation_and_after_payload_never_publishes_completion(self):
        archive = self.archive('![chart](' + uri(self.data) + ')')
        cancelled = Event(); cancelled.set()
        with self.assertRaises(Cancelled):
            import_selected(archive, (0,), self.destination, cancelled)
        self.assertEqual(list(self.root.iterdir()), [])
        cancel = Event()
        original = images.verify_capture_images
        def cancel_after_write(folder, receipt, event=None, **kwargs):
            self.assertTrue((folder / self.target).is_file())
            cancel.set()
            return original(folder, receipt, cancel, **kwargs)
        with patch.object(images, 'verify_capture_images', side_effect=cancel_after_write):
            result = import_selected(archive, (0,), self.destination, cancel)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.completed)
        incomplete = list(self.root.iterdir())[0]
        self.assertTrue((incomplete / '匯入開始.json').is_file())
        self.assertFalse((incomplete / RECEIPT).exists())
        retry = import_selected(archive, (0,), self.destination, Event())
        self.assertFalse(retry.failed)
        self.assertNotEqual(retry.completed[0].parent, incomplete)
        self.assertFalse((incomplete / RECEIPT).exists())

    def test_duplicate_does_not_republish_and_rejects_damaged_or_missing_manifest(self):
        archive, note = self.publish()
        second = import_selected(archive, (0,), self.destination, Event())
        self.assertEqual(second.skipped, [note])
        asset = note.parent / self.target
        asset.write_bytes(b'corrupt')
        damaged = import_selected(archive, (0,), self.destination, Event())
        self.assertTrue(damaged.failed)
        self.assertEqual(damaged.skipped + damaged.completed, [])
        asset.write_bytes(self.data)
        path = note.parent / RECEIPT
        receipt = json.loads(path.read_bytes()); receipt.pop('capture_images')
        path.write_bytes(canonical(receipt))
        missing = import_selected(archive, (0,), self.destination, Event())
        self.assertTrue(missing.failed)
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_portable_entry_keeps_local_png_with_exact_bytes(self):
        _, note = self.publish()
        text = note.read_text(encoding='utf-8')
        package = markdown_portable.prepare(self.root, note, text)
        files = dict(package.bundle.files)
        self.assertEqual(files[(note.parent / self.target).relative_to(self.root).as_posix()], self.data)
        self.assertEqual(files[note.relative_to(self.root).as_posix()].decode(), text)

    def test_portable_selected_source_regenerates_png_branches_without_editing_json(self):
        _, note = self.publish()
        source_path = note.parent / '原始對話.json'
        before = source_path.read_bytes()
        source = conversation_review.Source.load(source_path)
        package = markdown_portable.prepare(self.root, note, note.read_text(encoding='utf-8'),
                                            source=source, branches=(source.item.current,))
        files = dict(package.bundle.files)
        relative = note.parent.relative_to(self.root)
        self.assertEqual(files[(relative / self.target).as_posix()], self.data)
        branch = files[(relative / '分支-001-001.md').as_posix()].decode()
        self.assertIn(self.target, branch)
        self.assertNotIn('data:image', branch)
        original = json.loads(files[(relative / '原始對話.json').as_posix()])
        self.assertIn(uri(self.data), canonical(original).decode())
        self.assertEqual(original['mapping'], source.item.raw['mapping'])
        self.assertEqual(source_path.read_bytes(), before)

    def test_store_completion_and_recovery_verify_png_against_original(self):
        store = connector_store.TransferStore(self.base / 'spool')
        operation = uuid.uuid4().hex
        data = canonical(capture('![chart](' + uri(self.data) + ')'))
        store.begin(operation, len(data), hashlib.sha256(data).hexdigest())
        store.chunk(operation, 0, base64.b64encode(data).decode())
        record = store.finish(operation)
        store.accept(operation, self.destination)
        result = import_selected(load_archive(record['source']), (0,), self.destination, Event(),
                                 operation_id=operation, on_started=store.started, on_reused=store.reused)
        self.assertFalse(result.failed)
        store.complete(operation, result)
        self.assertEqual(store.get(operation)['state'], 'committed')
        folder = result.completed[0].parent
        record = store.get(operation)
        self.assertTrue(store._receipt(record, folder)['capture_images'])
        record['state'] = 'committing'; store.put(record)
        store.recover()
        self.assertEqual(store.get(operation)['state'], 'committed')
        for mode in ('corrupt', 'missing', 'manifest'):
            asset = folder / self.target
            receipt_path = folder / RECEIPT
            receipt = receipt_path.read_bytes()
            asset.write_bytes(self.data)
            if mode == 'corrupt':asset.write_bytes(b'corrupt')
            elif mode == 'missing':asset.unlink()
            else:
                value = json.loads(receipt); value.pop('capture_images')
                receipt_path.write_bytes(canonical(value))
            with self.subTest(mode=mode), self.assertRaises((ValueError, FileNotFoundError)):
                store._receipt(record, folder)
            record['state'] = 'committing'; store.put(record)
            store.recover()
            self.assertEqual(store.get(operation)['state'], 'unknown')
            receipt_path.write_bytes(receipt)

    def test_materializer_and_receipt_import_without_qt(self):
        code = '''import sys
class NoQt:
 def find_spec(self, fullname, *args):
  if fullname.startswith(('PySide6', 'PyQt', 'shiboken6')): raise RuntimeError('PNG materializer imported Qt')
sys.meta_path.insert(0, NoQt())
from prompt_calculus_studio import capture_images, connector_store
from prompt_calculus_studio.markdown_links import links
assert links('![x](assets/x.png)')[0].embed
'''
        run = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).resolve().parents[1],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout.decode('utf-8', errors='replace'))


if __name__ == '__main__':
    unittest.main()
