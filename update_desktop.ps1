param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Destination
)
. (Join-Path $PSScriptRoot 'deploy_common.ps1')
$package=Read-Package $Source 'desktop'
$target=Assert-PlainPath $Destination
if (Test-Path -LiteralPath $target) { throw 'Use a new desktop destination. Existing versions and data are never replaced.' }
$parent=[IO.Path]::GetDirectoryName($target)
if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'Destination parent directory must exist.' }
if ($target.StartsWith($package+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Destination cannot be inside the source package.' }
$stage=Join-Path $parent ('.pcs-desktop-stage-'+[Guid]::NewGuid().ToString('N'))
Copy-Package $package $stage
$null=Read-Package $stage 'desktop'
# Publish a complete package; failures leave staging separate from old versions.
$null=Assert-PlainPath $target
if (Test-Path -LiteralPath $target) { throw 'Destination appeared during staging; no files replaced.' }
Move-Item -LiteralPath $stage -Destination $target
Write-Output "Verified new desktop package: $target"
Write-Output 'Its launcher uses its own data directory. Keep the old version with its original data for rollback.'
