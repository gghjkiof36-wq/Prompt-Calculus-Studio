"""Pure presentation parsing; no Qt, model, filesystem writes or network."""
import unittest

from prompt_calculus_studio.local_ai.thinking import split_thinking


class ThinkingPresentationContracts(unittest.TestCase):
    def test_completed_envelope_separates_answer_without_mutating_source(self):
        source = '<think>\n先比較條件。\n\n再檢查答案。\n</think>\n\n**答覆**\n\n```py\nx = 1\n```'
        parsed = split_thinking(source)
        self.assertTrue(parsed.has_thinking and parsed.closed)
        self.assertEqual(parsed.thinking, '先比較條件。\n\n再檢查答案。')
        self.assertEqual(parsed.answer, '**答覆**\n\n```py\nx = 1\n```')
        self.assertTrue(source.startswith('<think>\n'))

    def test_all_stream_split_points_hold_partial_delimiters(self):
        source = '<think>推理🙂</think>答案'
        for offset in range(1, len(source) + 1):
            with self.subTest(offset=offset):
                accumulated = source[:offset]
                parsed = split_thinking(accumulated, receiving=True)
                if offset < len('<think>'):
                    self.assertTrue(parsed.pending_open)
                    self.assertFalse(parsed.has_thinking)
                    self.assertEqual(parsed.answer, '')
                elif offset < source.index('</think>') + len('</think>'):
                    self.assertTrue(parsed.has_thinking)
                    self.assertNotIn('<', parsed.thinking)
                    self.assertEqual(parsed.answer, '')
                else:
                    self.assertTrue(parsed.closed)
                    self.assertEqual(parsed.thinking, '推理🙂')
                    self.assertEqual(parsed.answer, accumulated.split('</think>', 1)[1])

    def test_terminal_incomplete_opening_is_literal_and_copyable(self):
        for prefix in ('<', '<t', '<thi', '<think'):
            with self.subTest(prefix=prefix):
                self.assertTrue(split_thinking(prefix, receiving=True).pending_open)
                final = split_thinking(prefix)
                self.assertFalse(final.has_thinking)
                self.assertEqual(final.answer, prefix)

    def test_unterminated_thinking_stays_readable_without_answer(self):
        for source in ('<think>尚在思考', '<think>尚在思考</thi', '<think>'):
            with self.subTest(source=source):
                parsed = split_thinking(source)
                self.assertTrue(parsed.has_thinking)
                self.assertFalse(parsed.closed)
                self.assertEqual(parsed.thinking, source[len('<think>'):])
                self.assertEqual(parsed.answer, '')

    def test_disambiguated_delimiter_prefixes_reappear_without_loss(self):
        for source in ('<thing>不是思考', '<think>推理</thing>仍然是內容'):
            parsed = split_thinking(source, receiving=True)
            if source.startswith('<think>'):
                self.assertEqual(parsed.thinking, '推理</thing>仍然是內容')
            else:
                self.assertEqual(parsed.answer, source)

    def test_literal_tags_inside_body_code_or_quotes_are_not_envelopes(self):
        samples = (
            '正常回答\n<think>例子</think>',
            '```xml\n<think>例子</think>\n```',
            '~~~xml\n<think>例子</think>\n~~~',
            '`<think>例子</think>`',
            '> <think>引用</think>',
            '    <think>縮排程式碼</think>',
            '\t<think>縮排程式碼</think>',
            '\n    <think>縮排程式碼</think>',
            '"<think>引用</think>"',
            '<thinking>普通文字</thinking>',
            '<THINK>大小寫不同</THINK>',
        )
        for source in samples:
            for receiving in (False, True):
                with self.subTest(source=source, receiving=receiving):
                    parsed = split_thinking(source, receiving=receiving)
                    self.assertFalse(parsed.has_thinking or parsed.pending_open)
                    self.assertEqual(parsed.answer, source)

    def test_bom_blank_lines_and_small_indentation_can_precede_envelope(self):
        for prefix in ('\ufeff', '\n\r\n', ' \t\n', '\ufeff\r\n  '):
            parsed = split_thinking(prefix + '<think>理由</think>答覆')
            self.assertEqual((parsed.thinking, parsed.answer), ('理由', '答覆'))
            self.assertTrue(parsed.closed)

    def test_only_first_envelope_is_extracted(self):
        source = '<think>理由</think>答覆中的 `<think>字面標記</think>`'
        self.assertEqual(split_thinking(source).answer, '答覆中的 `<think>字面標記</think>`')

    def test_indented_code_and_markdown_hard_break_spaces_are_preserved(self):
        parsed = split_thinking('<think>\n    reasoning_code()\n</think>\n    answer_code()\n\nnext line  \nlast line  ')
        self.assertEqual(parsed.thinking, '    reasoning_code()')
        self.assertEqual(parsed.answer, '    answer_code()\n\nnext line  \nlast line  ')

    def test_plain_responses_remain_byte_for_byte(self):
        for source in ('', '\n', '  答覆\n\n', '**重點**\n\n1. 第一項\n2. 第二項'):
            parsed = split_thinking(source, receiving=True)
            self.assertEqual(parsed.answer, source)
            self.assertFalse(parsed.has_thinking)


if __name__ == '__main__':
    unittest.main()
