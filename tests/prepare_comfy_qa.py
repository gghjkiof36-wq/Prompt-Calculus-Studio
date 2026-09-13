"""Isolated ComfyUI smoke workspace; synthetic images and library only."""
import json
import shutil
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_comfyui import build, DEST
from prompt_studio.core import Storage, initial_state
from test_comfy_integration import png

qa = ROOT / 'qa' / 'comfy-integration'
qa.mkdir(parents=True, exist_ok=True)
for name in ('input', 'output', 'temp', 'user', 'saved', 'library', 'custom_nodes'):
    (qa / name).mkdir(exist_ok=True)
build()
shutil.copytree(DEST, qa / 'custom_nodes' / 'comfyui_prompt_studio', dirs_exist_ok=True)
(qa / 'custom_nodes' / 'comfyui_prompt_studio' / 'local_library.json').write_text(
    json.dumps({'library': str(qa / 'library' / 'studio.sqlite3')}), encoding='utf-8')
store = Storage(qa / 'library'); state = initial_state()
state['selections'] = {state['items'][0]['module']: [state['items'][0]['id']]}
store.save(state); store.close()
png(qa / 'input' / 'fixture.png', {})
fixture = qa / 'custom_nodes' / 'ps_test_fixture'; fixture.mkdir(exist_ok=True)
(fixture / '__init__.py').write_text('''class Fixture:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'image': ('IMAGE',), 'text': ('STRING', {'multiline': True})}}
    RETURN_TYPES = ('IMAGE',)
    FUNCTION = 'run'
    CATEGORY = 'Testing'
    def run(self, image, text):
        if 'ps-test-slow' in text:
            import time
            from comfy.model_management import throw_exception_if_processing_interrupted
            for _ in range(100):
                time.sleep(.05)
                throw_exception_if_processing_interrupted()
        return (image,)
NODE_CLASS_MAPPINGS = {'PromptStudioTestFixture': Fixture}
''', encoding='utf-8')
print(qa)
