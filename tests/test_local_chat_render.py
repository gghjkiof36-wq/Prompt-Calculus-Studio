"""Chat projection has no note/file resolver; browser checks live in isolated QA."""
import base64
import struct
import unittest
from unittest.mock import patch
import zlib

from prompt_calculus_studio.local_ai.chat_render import MAX_FORMULA_CHARS, MAX_RENDER_CHARS, needs_rich, project_chat


def png_uri(width=1, height=1):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    data += chunk(b'IDAT', zlib.compress(b'\0\x70\xa0\xc0')) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')


class ChatRenderContracts(unittest.TestCase):
    def test_fenced_code_is_escaped_and_copy_keeps_exact_source(self):
        source = '<script>alert(1)</script>\n<img src="file:///C:/secret.png">\n'
        value = project_chat('```html\n' + source + '```')
        self.assertEqual(value.codes, [source])
        self.assertNotIn('<script>', value.body)
        self.assertNotIn('<img ', value.body)
        self.assertIn('pcs-chat:copy/0', value.body)

    def test_partial_fences_render_code_without_rewriting_input(self):
        source = '```python\nprint(1)'
        value = project_chat(source)
        self.assertEqual(value.codes, ['print(1)'])
        self.assertTrue(needs_rich(source))

    def test_shared_math_delimiters_and_code_protection(self):
        source = r'$x_i^2$ 與 \(\alpha\)' + '\n\n' + r'\[\frac{1}{2}\]' + '\n\n`$literal$`'
        value = project_chat(source)
        self.assertEqual([m['source'] for m in value.math], ['x_i^2', r'\alpha', r'\frac{1}{2}'])
        self.assertEqual([m['block'] for m in value.math], [False, False, True])
        self.assertIn('$literal$', value.body)

    def test_formula_bounds_keep_literal_source(self):
        formula = 'x' * (MAX_FORMULA_CHARS + 1)
        value = project_chat('$' + formula + '$')
        self.assertEqual(value.math, [])
        self.assertIn(formula, value.body)

    def test_tables_and_superscript_use_document_grammar(self):
        value = project_chat('H<sub>2</sub>O, 10<sup>3</sup>\n\n|項目|值|\n|---|---|\n|x|1|')
        self.assertIn('<sub', value.body)
        self.assertIn('<sup', value.body)
        self.assertIn('<table>', value.body)
        self.assertIn('pcs-table-scroll', value.body)

    def test_active_html_is_literal_not_executable(self):
        for source in ('<script>alert(1)</script>', '<iframe src="https://example.test"></iframe>',
                       'text <img src="file:///secret" onerror="alert(1)">'):
            value = project_chat(source)
            self.assertNotIn('<script>', value.body)
            self.assertNotIn('<iframe ', value.body)
            self.assertNotIn('<img ', value.body)

    def test_remote_and_local_image_references_never_touch_files(self):
        targets = ('https://example.test/a.png', 'file:///C:/secret.png', 'C:/secret.png',
                   '../assets/secret.png', 'data:image/svg+xml;base64,PHN2Zz4=', 'data:image/png;base64,broken')
        with patch('pathlib.Path.open', side_effect=AssertionError('no file reads')):
            for target in targets:
                value = project_chat(f'![圖]({target})')
                self.assertNotIn('<img ', value.body)

    def test_only_bounded_verified_png_is_embedded(self):
        safe = project_chat('![一像素](' + png_uri() + ')')
        self.assertIn('<img src="data:image/png;base64,', safe.body)
        oversized = project_chat('![過大](' + png_uri(100000, 100000) + ')')
        self.assertNotIn('<img ', oversized.body)
        repeated = project_chat('\n'.join('![](' + png_uri() + ')' for _ in range(10)))
        self.assertEqual(repeated.body.count('<img '), 8)

    def test_long_source_falls_back_to_selectable_literal(self):
        source = '<script>' + 'a' * MAX_RENDER_CHARS
        value = project_chat(source)
        self.assertFalse(needs_rich(source))
        self.assertIn('&lt;script&gt;', value.body)
        self.assertEqual(value.math, [])

    def test_jpeg_webp_are_canonical_png_and_mime_must_match(self):
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage, QColor
        for kind, mime in (('JPEG', 'jpeg'), ('WEBP', 'webp')):
            stream = QBuffer()
            stream.open(QIODevice.OpenModeFlag.WriteOnly)
            image = QImage(6, 4, QImage.Format.Format_RGB32)
            image.fill(QColor('#70a0c0'))
            self.assertTrue(image.save(stream, kind))
            data = base64.b64encode(bytes(stream.data())).decode('ascii')
            self.assertIn('<img src="data:image/png;base64,',
                project_chat(f'![圖](data:image/{mime};base64,{data})').body)
            self.assertNotIn('<img ', project_chat(f'![圖](data:image/png;base64,{data})').body)

    def test_animated_and_oversized_rasters_are_not_loaded(self):
        from prompt_calculus_studio.local_ai.chat_render import MAX_IMAGE_BYTES, _raster_png
        with patch('PySide6.QtGui.QImageReader', side_effect=AssertionError('oversize must fail before decoder')):
            self.assertEqual(_raster_png('data:image/jpeg;base64,' + 'A' * (MAX_IMAGE_BYTES * 2)), '')
        with patch('PySide6.QtGui.QImageReader') as Reader:
            Reader.return_value.format.return_value = b'png'
            Reader.return_value.imageCount.return_value = 2
            Reader.return_value.size.return_value.width.return_value = 1
            Reader.return_value.size.return_value.height.return_value = 1
            _raster_png.cache_clear()
            self.assertEqual(_raster_png(png_uri()), '')
            Reader.return_value.read.assert_not_called()
        _raster_png.cache_clear()

    def test_large_code_preserves_copy_without_highlight_work(self):
        source = 'print(1)\n' * 3000
        with patch('prompt_calculus_studio.local_ai.chat_render.highlight', side_effect=AssertionError('bounded highlighting')):
            value = project_chat('```python\n' + source + '```')
        self.assertEqual(value.codes, [source])
        self.assertIn('pcs-chat:copy/0', value.body)


if __name__ == '__main__':
    unittest.main()
