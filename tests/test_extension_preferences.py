"""Stable preferences, conservative migration and verified bind-only discovery."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prompt_calculus_studio.extension_install import (InstallPreferences, workspace_preferences,
    preferences_for, bind_reported, loopback_server)


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root/'data'; self.data.mkdir()
        self.comfy = self.root/'ComfyUI'; (self.comfy/'custom_nodes'/'comfyui_prompt_calculus_studio').mkdir(parents=True)
        (self.comfy/'main.py').write_text('# inert')
        self.info = dict(root=str(self.comfy/'custom_nodes'/'comfyui_prompt_calculus_studio'))

    def legacy(self, name, value):
        path = self.data/'sessions'/name/'app-services'/'extension-install.json'
        path.parent.mkdir(parents=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
        return path

    def migrate(self, values, expected):
        paths = [self.legacy(str(i), value) for i,value in enumerate(values)]
        before = {path:path.read_bytes() for path in paths}
        result = workspace_preferences(self.data)
        self.assertEqual(result.value, expected)
        self.assertEqual({path:path.read_bytes() for path in paths}, before)
        self.assertEqual(InstallPreferences(self.data).value, expected)
        return result

    def test_consistent_migration_keeps_old_files(self):
        self.migrate([dict(root='A',automatic=False)]*2, dict(root='A',automatic=False))

    def test_consistent_migration_preserves_existing_extra_fields(self):
        value=dict(root='A',automatic=False,marker='preserved')
        self.migrate([value],value)

    def test_automatic_conflict_is_false_keeps_old_files(self):
        self.migrate([dict(root='A',automatic=True),dict(root='A',automatic=False)], dict(root='A',automatic=False))

    def test_root_conflict_is_empty_and_false_keeps_old_files(self):
        self.migrate([dict(root='A',automatic=True),dict(root='B',automatic=True)], dict(root='',automatic=False))

    def test_no_valid_files_default_and_only_one_level(self):
        nested = self.data/'sessions'/'one'/'nested'/'app-services'
        nested.mkdir(parents=True)
        path = nested/'extension-install.json'; path.write_text('{"root":"ignored","automatic":false}')
        before = path.read_bytes()
        self.migrate([], dict(root='',automatic=True))
        self.assertEqual(path.read_bytes(),before)
        # No second scan once the fixed file exists.
        self.legacy('later',dict(root='late',automatic=False))
        self.assertEqual(workspace_preferences(self.data).value,dict(root='',automatic=True))

    def test_broken_old_files_are_diagnosed_not_changed(self):
        with self.assertLogs('prompt_calculus_studio.extension_install',level='WARNING') as messages:
            self.migrate(['{bad',dict(root='A',automatic='false'),dict(root='A',automatic=False)],dict(root='A',automatic=False))
        self.assertEqual(len(messages.output),2)

    def test_fixed_corrupt_file_is_never_replaced_or_bound(self):
        old = self.legacy('one',dict(root='A',automatic=False)); before=old.read_bytes()
        fixed = self.data/'extension-install.json'; fixed.write_text('{broken')
        result = workspace_preferences(self.data)
        self.assertTrue(result.error)
        self.assertFalse(bind_reported(result,'http://127.0.0.1:8188',self.info)[0])
        self.assertEqual(fixed.read_text(),'{broken'); self.assertEqual(old.read_bytes(),before)

    def test_fixed_valid_file_wins_over_sessions(self):
        old=self.legacy('one',dict(root='old',automatic=True)); before=old.read_bytes()
        result=InstallPreferences(self.data);result.value=dict(root='fixed',automatic=False);result.save()
        self.assertEqual(workspace_preferences(self.data).value,result.value)
        self.assertEqual(old.read_bytes(),before)

    def test_host_selection_and_shared_manual_automatic_preferences(self):
        session=self.data/'sessions'/'new'/'app-services';session.mkdir(parents=True)
        host=SimpleNamespace(store=SimpleNamespace(directory=session),workspace_owner=SimpleNamespace(data=self.data))
        first=preferences_for(host);first.value=dict(root=str(self.comfy),automatic=False);first.save()
        second=preferences_for(host)
        self.assertEqual(second.path,self.data/'extension-install.json');self.assertEqual(first.value,second.value)
        standalone=SimpleNamespace(store=host.store)
        self.assertEqual(preferences_for(standalone).path,session/'extension-install.json')

    def test_successful_bind_preserves_automatic_and_never_installs(self):
        prefs=workspace_preferences(self.data);prefs.value['automatic']=False;prefs.save()
        with patch('prompt_calculus_studio.extension_page.ExtensionPage.install') as install:
            self.assertEqual(bind_reported(prefs,'http://127.0.0.1:8188',self.info),(True,'bound'))
            install.assert_not_called()
        self.assertEqual(InstallPreferences(self.data).value,dict(root=str(self.comfy),automatic=False))

    def test_binding_negative_conditions_do_not_write(self):
        prefs=workspace_preferences(self.data)
        cases=[('remote','http://192.0.2.1:8188',self.info,None),
               ('wrong_parent','http://127.0.0.1:8188',dict(root=str(self.comfy/'other'/'extension')),None),
               ('installed_mismatch','http://127.0.0.1:8188',dict(root=str(self.comfy/'custom_nodes'/'other')),None),
               ('invalid_root','http://127.0.0.1:8188',dict(root=str(self.root/'invalid'/'custom_nodes'/'extension')),None)]
        for name,url,info,_ in cases:
            with self.subTest(name=name):
                before=prefs.path.read_bytes();self.assertFalse(bind_reported(prefs,url,info)[0]);self.assertEqual(prefs.path.read_bytes(),before)
        prefs.value['root']='different';prefs.save();before=prefs.path.read_bytes()
        self.assertEqual(bind_reported(prefs,'http://127.0.0.1',self.info),(False,'already_bound'))
        self.assertEqual(prefs.path.read_bytes(),before)

    def test_redirected_path_rejected(self):
        prefs=workspace_preferences(self.data);before=prefs.path.read_bytes()
        for method in ('is_symlink','is_junction'):
            with self.subTest(method=method),patch.object(Path,method,return_value=True):
                self.assertFalse(bind_reported(prefs,'http://127.0.0.1',self.info)[0])
                self.assertEqual(prefs.path.read_bytes(),before)

    def test_localhost_must_resolve_to_loopback(self):
        self.assertTrue(loopback_server('http://[::1]:8188'))
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('192.0.2.1',80))]):
            self.assertFalse(loopback_server('http://localhost:8188'))
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',80))]):
            self.assertTrue(loopback_server('http://localhost:8188'))


if __name__ == '__main__': unittest.main()
