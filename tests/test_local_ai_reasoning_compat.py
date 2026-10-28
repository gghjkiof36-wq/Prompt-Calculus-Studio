"""Lazy-page compatibility with a service/settings module already in memory."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

import test_local_ai_model_presets as preset_fixture


class ReasoningLazyImports(unittest.TestCase):
    def child(self, body):
        root = Path(__file__).resolve().parents[1]
        code = 'import sys\n'
        code += f'sys.path[:0] = {[str(root / "vendor"), str(root)]!r}\n'
        code += textwrap.dedent(body)
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code],
                                cwd=root, capture_output=True, text=True, timeout=20,
                                env=dict(os.environ, QT_QPA_PLATFORM='offscreen'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_fresh_imports_share_one_reasoning_contract(self):
        self.child('''
            from prompt_calculus_studio.local_ai import settings, reasoning, chat_settings, service
            assert settings.DEFAULT_REASONING is reasoning.DEFAULT_REASONING
            assert settings.normalize_reasoning is reasoning.normalize_reasoning
            assert chat_settings.DEFAULT_REASONING is reasoning.DEFAULT_REASONING
            assert service.DEFAULT_REASONING is reasoning.DEFAULT_REASONING
        ''')

    def test_new_lazy_modules_accept_pre_reasoning_settings_without_reloading_it(self):
        self.child('''
            from types import ModuleType
            from prompt_calculus_studio.local_ai import settings
            old = ModuleType(settings.__name__)
            old.__dict__.update({k: v for k, v in vars(settings).items()
                                if k not in ('DEFAULT_REASONING', 'normalize_reasoning')})
            sys.modules[settings.__name__] = old
            import prompt_calculus_studio.local_ai as package
            package.settings = old
            from prompt_calculus_studio.local_ai import chat_settings, registry, runtime, service
            from prompt_calculus_studio.local_documents import chat, schema
            assert chat_settings.DEFAULT_REASONING == dict(enable_thinking=None, reasoning_budget_tokens=None)
            assert schema.decode(schema.encode(chat.empty()))['kind'] == 'chat'
            assert sys.modules[settings.__name__] is old
            assert not hasattr(old, 'DEFAULT_REASONING')
            assert not hasattr(old, 'normalize_reasoning')
        ''')


class LegacyPresetService:
    """The existing pre-reasoning preset interface; rejects unknown keywords."""
    def __init__(self, service):
        self.service = service
        self.calls = []

    @property
    def error(self):
        return self.service.error

    def model_preset(self, model_id):
        value = self.service.model_preset(model_id)
        value.pop('reasoning')
        return value

    def configure_model(self, model_id, values, *, system_prompt=None, expected_revision=None):
        self.calls.append((model_id, dict(values), system_prompt))
        return self.service.configure_model(model_id, values, system_prompt=system_prompt,
                                            expected_revision=expected_revision)

    def reset_model_preset(self, model_id, *, expected_revision=None):
        return self.service.reset_model_preset(model_id, expected_revision=expected_revision)


class ReasoningLegacyPanel(unittest.TestCase):
    setUpClass = classmethod(preset_fixture.ModelPresetContracts.setUpClass.__func__)
    setUp = preset_fixture.ModelPresetContracts.setUp
    tearDown = preset_fixture.ModelPresetContracts.tearDown
    add_model = preset_fixture.ModelPresetContracts.add_model
    wait_for = preset_fixture.ModelPresetContracts.wait_for

    def test_legacy_service_can_open_edit_save_and_reset_without_new_controls(self):
        from prompt_calculus_studio.local_ai.chat_settings import ModelPresetPanel
        legacy = LegacyPresetService(self.service)
        panel = ModelPresetPanel(legacy, self.a['id'])
        self.addCleanup(panel.deleteLater)
        self.assertTrue(panel.thinking_controls.isHidden())
        panel.temperature.setValue(.45)
        panel.system_prompt.setPlainText('legacy model instructions')
        panel.refresh()
        self.assertEqual(panel.temperature.value(), .45)
        self.assertTrue(panel.apply(), panel.message.text())
        self.assertEqual(len(legacy.calls), 1)
        self.assertEqual(self.service.model_preset(self.a['id'])['parameters']['temperature'], .45)
        self.assertEqual(self.service.model_preset(self.a['id'])['system_prompt'], 'legacy model instructions')
        self.assertTrue(panel.reset(), panel.message.text())
        self.assertEqual(self.service.model_preset(self.a['id'])['system_prompt'], '')
        self.assertTrue(panel.thinking_controls.isHidden())
        self.assertIsNone(self.service._worker)
