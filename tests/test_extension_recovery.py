"""Recovery receipts describe verified trees, including competing installers."""
import json
import unittest
from test_extension_deployment import ExtensionDeploymentTests


class RecoveryTests(ExtensionDeploymentTests):
    def receipt(self):
        return json.loads((self.comfy/'.pcs-program-backups/install-state.json').read_text())

    def conflict(self, same_name=False, occupy_previous=False):
        self.old_install()
        if same_name:self.old.rename(self.target)
        original=self.target if same_name else self.old
        before=self.tree(original);data=self.tree(self.data)
        foreign=original if occupy_previous else self.target
        escaped=str(foreign).replace("'","''")
        prefix="""function Get-CimInstance { @() }
function Move-Item { param($LiteralPath,$Destination)
 Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath -Destination $Destination
 if ([IO.Path]::GetFileName($Destination).StartsWith('previous-')) {
  $foreign='FOREIGN'
  [IO.Directory]::CreateDirectory($foreign) | Out-Null
  [IO.File]::WriteAllText((Join-Path $foreign 'foreign.txt'),'concurrent owner')
 }
}""".replace('FOREIGN',escaped)
        # Occupying a legacy previous name alone only matters after replacement fails.
        if occupy_previous and not same_name:
            prefix+=self.replacement_probe("throw 'replace failed'")
        result=self.wrapper(prefix,21)
        self.assertIn(b'PCS_ROLLBACK_UNCONFIRMED',result.stdout)
        receipt=self.receipt();self.assertEqual(receipt['state'],'rollback_unconfirmed')
        from pathlib import Path
        self.assertEqual(self.tree(Path(receipt['backup'])),before)
        self.assertEqual(self.tree(foreign),{'foreign.txt':b'concurrent owner'})
        self.assertEqual(self.tree(self.data),data)
        snapshot=self.tree(self.comfy)
        self.wrapper('function Get-CimInstance { @() }',21)
        self.assertEqual(self.tree(self.comfy),snapshot)

    def test_same_name_concurrent_target_preserves_both_trees_and_blocks_retry(self):
        self.conflict(same_name=True)

    def test_legacy_concurrent_target_preserves_backup_and_blocks_retry(self):
        self.conflict()

    def test_legacy_recovery_path_occupied_preserves_both_trees(self):
        self.conflict(occupy_previous=True)

    def test_changed_backup_cannot_be_reported_as_restored(self):
        self.old_install();before=self.tree(self.old)
        self.wrapper("""function Get-CimInstance { @() }
function Move-Item { param($LiteralPath,$Destination)
 Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath -Destination $Destination
 if ([IO.Path]::GetFileName($Destination).StartsWith('previous-')) {
  [IO.File]::WriteAllText((Join-Path $Destination 'extra.txt'),'changed backup')
 }
}"""+self.replacement_probe("throw 'replace failed'"),21)
        from pathlib import Path
        record=self.receipt();self.assertEqual(record['state'],'rollback_unconfirmed')
        self.assertEqual(self.tree(Path(record['backup'])),dict(before,**{'extra.txt':b'changed backup'}))
        self.assertFalse(self.old.exists());self.assertFalse(self.target.exists())

    def test_same_name_normal_recovery_is_verified(self):
        self.old_install();self.old.rename(self.target);before=self.tree(self.target)
        self.wrapper('function Get-CimInstance { @() }\n'+self.replacement_probe("throw 'replace failed'"),1)
        self.assertEqual(self.receipt()['state'],'rolled_back')
        self.assertEqual(self.tree(self.target),before)

    def test_first_install_failure_without_target_is_verified(self):
        self.wrapper('function Get-CimInstance { @() }\n'+self.replacement_probe("throw 'replace failed'"),1)
        self.assertEqual(self.receipt()['state'],'rolled_back')
        self.assertFalse(self.target.exists())

    def test_target_appearing_after_final_check_is_not_modified_or_reported_complete(self):
        from pathlib import Path
        self.old_install();self.old.rename(self.target);before=self.tree(self.target)
        action="[IO.Directory]::CreateDirectory($LiteralPath) | Out-Null; [IO.File]::WriteAllText((Join-Path $LiteralPath 'foreign.txt'),'late owner')"
        self.wrapper('function Get-CimInstance { @() }\n'+self.replacement_probe(action),21)
        receipt=self.receipt();self.assertEqual(receipt['state'],'rollback_unconfirmed')
        self.assertEqual(self.tree(Path(receipt['backup'])),before)
        self.assertEqual(self.tree(self.target),{'foreign.txt':b'late owner'})
        self.assertTrue(Path(receipt['stage']).is_dir())


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(RecoveryTests(n) for n in RecoveryTests.__dict__ if n.startswith('test_'))
