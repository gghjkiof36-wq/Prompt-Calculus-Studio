"""Structured response parsing and unsupported capabilities; no real inference."""
import unittest
from unittest.mock import patch

from prompt_calculus_studio.local_ai.runtime import LlamaRuntime
from prompt_calculus_studio.local_ai.engine import Cancellation
from prompt_calculus_studio.local_ai.settings import DEFAULT_SETTINGS


class StructuredRuntime(unittest.TestCase):
    def setUp(self):
        self.runtime = LlamaRuntime()
        self.runtime.model = {'id':'synthetic'}
        self.runtime.context_size = 8192
        self.runtime._reasoning_props = {'chat_template_caps':{'supports_tools':True,'supports_tool_calls':True}}
        self.validate = patch('prompt_calculus_studio.local_ai.runtime.validate_model')
        self.validate.start();self.addCleanup(self.validate.stop)
        self.tokenize = patch.object(self.runtime,'_prompt_tokens',return_value=5)
        self.tokenize.start();self.addCleanup(self.tokenize.stop)

    def turn(self, value):
        with patch.object(self.runtime,'_json',return_value=value):
            return self.runtime.tool_turn([dict(role='user',content='hello')],dict(DEFAULT_SETTINGS),Cancellation(),[])

    def test_unsupported_template_is_explicit_failure(self):
        self.runtime._reasoning_props = {'chat_template_caps':{}}
        with self.assertRaisesRegex(RuntimeError,'未支援工具'):
            self.turn({})

    def test_malformed_or_mismatched_calls_never_become_actions(self):
        variants=[None,[],{'choices':[None]}, {'choices':[{'message':{'role':'assistant','tool_calls':['shell']},'finish_reason':'tool_calls'}]},
            {'choices':[{'message':{'role':'assistant','tool_calls':[]},'finish_reason':'tool_calls'}]}]
        for value in variants:
            with self.subTest(value=value),self.assertRaises(RuntimeError):
                self.turn(value)

    def test_context_limit_and_truncated_reply_stay_honest(self):
        self.runtime.context_size=10
        with self.assertRaisesRegex(RuntimeError,'上下文不足'):
            self.turn({})
        self.runtime.context_size=8192
        result=self.turn({'choices':[dict(message=dict(role='assistant',content='partial'),finish_reason='length')]})
        self.assertEqual(result,('limited',dict(role='assistant',content='partial')))

    def test_preflight_is_reused_until_content_or_tool_mode_changes(self):
        messages = [dict(role='user', content='inspect')]
        params, cancel = dict(DEFAULT_SETTINGS), Cancellation()
        reply = {'choices':[dict(message=dict(role='assistant', content='partial'), finish_reason='stop')]}
        with patch.object(self.runtime, '_prompt_tokens', return_value=50) as count, patch.object(self.runtime, '_json', return_value=reply) as send:
            self.runtime.tool_budget(messages, params, cancel, [])
            self.runtime.tool_turn(messages, params, cancel, [])
            self.assertEqual(count.call_count, 1)
            messages[0]['content'] = 'changed'
            self.runtime.tool_turn(messages, params, cancel, [])
            self.assertEqual(count.call_count, 2)
            self.runtime.tool_budget(messages, params, cancel, [], final=True)
            self.runtime.tool_turn(messages, params, cancel, [], final=True)
            self.assertEqual(count.call_count, 3)
            self.assertEqual(send.call_args.args[1]['tool_choice'], 'none')

    def test_final_turn_never_executes_unexpected_tools(self):
        reply = {'choices':[dict(message=dict(role='assistant', content='', tool_calls=[dict(id='a',type='function',function=dict(name='read_file',arguments='{}'))]), finish_reason='tool_calls')]}
        with patch.object(self.runtime, '_json', return_value=reply), self.assertRaisesRegex(RuntimeError, '收尾階段'):
            self.runtime.tool_turn([dict(role='user',content='task')], dict(DEFAULT_SETTINGS), Cancellation(), [], final=True)


if __name__=='__main__':
    unittest.main()
