"""Automatic companion installation uses only inert, disposable install trees."""
import json
import os
import subprocess
import unittest
from test_deployment import DeploymentTests, ROOT, SHELL


class ExtensionDeploymentTests(DeploymentTests):
    def wrapper(self, prefix, expected):
        script = self.root / 'automatic-install.ps1'
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        script.write_text("$ErrorActionPreference='Stop'\n" + prefix + '\n& ' + quote(ROOT / 'install_comfyui.ps1') +
            ' -Source ' + quote(self.source) + ' -ComfyUIRoot ' + quote(self.comfy) +
            ' -DesktopData ' + quote(self.data) + ' -Update -PreserveLibrary -RequireStopped\nexit $LASTEXITCODE', encoding='utf-8-sig')
        result = subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-File', str(script)], env=self.env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))

    def test_existing_library_is_preserved_when_current_pcs_uses_other_data(self):
        self.old_install()
        config = json.dumps(dict(library=str(self.root/'original-data/studio.sqlite3'), extra='keep')).encode()
        (self.old/'local_library.json').write_bytes(config)
        self.wrapper('function Get-CimInstance { @() }', 0)
        self.assertEqual((self.target/'local_library.json').read_bytes(), config)
        self.assertEqual((next((self.comfy/'.pcs-program-backups').glob('previous-*'))/'local_library.json').read_bytes(), config)

    def test_running_server_defers_before_changing_any_files(self):
        self.old_install(); before = self.tree(self.comfy)
        self.wrapper("function Get-CimInstance { [pscustomobject]@{Name='python.exe'; CommandLine='python main.py --port 8199'; ExecutablePath='C:\\Python\\python.exe'} }", 20)
        self.assertEqual(self.tree(self.comfy), before)

    def test_running_desktop_defers_and_unrelated_python_does_not(self):
        self.old_install(); before = self.tree(self.comfy)
        self.wrapper("function Get-CimInstance { [pscustomobject]@{Name='ComfyUI.exe'; CommandLine='ComfyUI.exe'} }", 20)
        self.assertEqual(self.tree(self.comfy), before)
        self.wrapper("function Get-CimInstance { [pscustomobject]@{Name='python.exe'; CommandLine='python other_task.py'; ExecutablePath='C:\\Python\\python.exe'} }", 0)
        self.assertTrue(self.target.exists())

    def test_process_query_failure_never_means_safe_to_install(self):
        self.old_install(); before = self.tree(self.comfy)
        self.wrapper("function Get-CimInstance { throw 'synthetic process query denied' }", 1)
        self.assertEqual(self.tree(self.comfy), before)

    def test_final_replace_failure_restores_original_folder(self):
        self.old_install(); before = self.tree(self.old)
        self.wrapper(r"""function Get-CimInstance { @() }
function Move-Item { param($LiteralPath,$Destination)
 if ([IO.Path]::GetFileName($LiteralPath).StartsWith('stage-')) { throw 'synthetic replacement failure' }
 Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination
}""", 1)
        self.assertEqual(self.tree(self.old), before)
        self.assertFalse(self.target.exists())
        self.assertEqual(json.loads((self.comfy/'.pcs-program-backups/install-state.json').read_text())['state'],'rolled_back')

    def test_interrupted_install_is_not_blindly_retried(self):
        self.old_install()
        backups=self.comfy/'.pcs-program-backups'; backups.mkdir()
        (backups/'install-state.json').write_text(json.dumps(dict(state='replacing')))
        before=self.tree(self.comfy)
        self.wrapper('function Get-CimInstance { @() }',21)
        self.assertEqual(self.tree(self.comfy),before)

    def test_desktop_data_root_with_models_and_user_is_supported(self):
        (self.comfy/'main.py').unlink()
        (self.comfy/'models').mkdir(); (self.comfy/'user').mkdir()
        self.wrapper('function Get-CimInstance { @() }', 0)
        self.assertTrue((self.target/'web/prompt_studio.js').is_file())


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(ExtensionDeploymentTests(name) for name in ExtensionDeploymentTests.__dict__ if name.startswith('test_'))
