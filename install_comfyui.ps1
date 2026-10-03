param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$ComfyUIRoot,
    [Parameter(Mandatory=$true)][string]$DesktopData,
    [switch]$Update,
    [switch]$PreserveLibrary,
    [switch]$RequireStopped
)
. (Join-Path $PSScriptRoot 'deploy_common.ps1')
$package=Read-Package $Source 'comfyui'
if ([IO.Path]::GetFileName($package) -ne 'comfyui_prompt_calculus_studio') { throw 'Use the complete comfyui_prompt_calculus_studio package directory.' }
$comfy=Assert-PlainPath $ComfyUIRoot
$custom=Assert-PlainPath (Join-Path $comfy 'custom_nodes')
$backups=Assert-PlainPath (Join-Path $comfy '.pcs-program-backups')
$receipt=Assert-PlainPath (Join-Path $backups 'install-state.json')
if (Test-Path -LiteralPath $receipt) {
    $last=Get-Content -LiteralPath $receipt -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($last.state -notin @('completed','rolled_back')) { Write-Output 'PCS_PREVIOUS_INSTALL_UNCONFIRMED: inspect .pcs-program-backups before continuing.'; exit 21 }
}
$codeRoot=Test-Path -LiteralPath (Join-Path $comfy 'main.py') -PathType Leaf
$dataRoot=(Test-Path -LiteralPath (Join-Path $comfy 'models') -PathType Container) -and (Test-Path -LiteralPath (Join-Path $comfy 'user') -PathType Container)
if ((-not $codeRoot -and -not $dataRoot) -or -not (Test-Path -LiteralPath $custom -PathType Container)) { throw 'Not a ComfyUI directory.' }
function Test-ComfyRunning {
    # A relative main.py has no reliable working-directory field in CIM.
    # Defer rather than replacing files of an unidentifiable live server.
    $processes=@(Get-CimInstance Win32_Process -OperationTimeoutSec 5 -ErrorAction Stop | Where-Object { $_.Name -match '^(python(w)?|python[0-9.]+|ComfyUI)\.exe$' })
    foreach ($process in $processes) {
        if ($process.Name -eq 'ComfyUI.exe' -or $process.CommandLine -match '(?i)(^|[\\/\s"''])main\.py([\s"'']|$)' -or ($process.ExecutablePath -and $process.ExecutablePath.StartsWith($comfy+'\',[StringComparison]::OrdinalIgnoreCase))) { return $true }
    }
    return $false
}
if ($RequireStopped -and (Test-ComfyRunning)) { Write-Output 'PCS_WAIT_FOR_COMFY_EXIT'; exit 20 }
$library=Assert-PlainPath (Join-Path $DesktopData 'studio.sqlite3')
if (-not (Test-Path -LiteralPath $library -PathType Leaf)) { throw 'Desktop database does not exist.' }
$target=Assert-PlainPath (Join-Path $custom 'comfyui_prompt_calculus_studio')
$old=Assert-PlainPath (Join-Path $custom 'comfyui_prompt_studio')
$existing=@(@($target,$old) | Where-Object { Test-Path -LiteralPath $_ })
if ($existing.Count -gt 1) { throw 'Both old and new extension folders exist. Preserve and move the unused version outside custom_nodes before continuing.' }
if ($existing.Count -and -not $Update) { throw 'Extension exists. Use -Update only after stopping ComfyUI.' }
$config=$null; $oldFiles=@()
if ($existing.Count) {
    $previous=$existing[0]
    if (-not (Test-Path -LiteralPath $previous -PathType Container)) { throw 'Existing extension is not a directory.' }
    $oldFiles=Get-TreeManifest $previous
    $bootstrap=Join-Path $previous 'local_library.json'
    if (Test-Path -LiteralPath $bootstrap) {
        $config=[IO.File]::ReadAllBytes($bootstrap)
        $settings=[Text.Encoding]::UTF8.GetString($config).TrimStart([char]0xFEFF) | ConvertFrom-Json
        if (-not $settings.library) { throw 'Existing library setting is invalid; no configuration was replaced.' }
        $previousLibrary=Assert-PlainPath $settings.library
        if (-not $PreserveLibrary -and $previousLibrary -ne $library) { throw 'DesktopData conflicts with the existing library setting; no configuration was replaced.' }
    }
}
if ($null -eq $config) { $config=[Text.Encoding]::UTF8.GetBytes((@{library=$library} | ConvertTo-Json)) }
if ($package.StartsWith($comfy+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Extract the source package outside the ComfyUI installation before updating.' }
$id=[Guid]::NewGuid().ToString('N')
$stage=Join-Path $backups ('stage-'+$id); $backup=Join-Path $backups ('previous-'+$id)
function Write-InstallState([string]$State) {
    $temporary=Assert-PlainPath ($receipt+'.'+$id+'.tmp')
    $value=@{state=$State; id=$id; target=$target; stage=$stage; backup=$backup; previousName=$(if($existing.Count){[IO.Path]::GetFileName($previous)}else{''})} | ConvertTo-Json
    [IO.File]::WriteAllText($temporary,$value,[Text.UTF8Encoding]::new($false))
    $null=Assert-PlainPath $receipt
    if (Test-Path -LiteralPath $receipt) {
        $recordBackup=Assert-PlainPath (Join-Path $backups 'previous-install-state.json')
        [IO.File]::Replace($temporary,$receipt,$recordBackup)
    } else { [IO.File]::Move($temporary,$receipt) }
}
New-Item -ItemType Directory -Path $backups -Force | Out-Null
Copy-Package $package $stage
$null=Read-Package $stage 'comfyui'
[IO.File]::WriteAllBytes((Join-Path $stage 'local_library.json'),$config)
if ($RequireStopped -and (Test-ComfyRunning)) { Write-Output 'PCS_WAIT_FOR_COMFY_EXIT'; exit 20 }
if ($existing.Count) {
    $null=Assert-PlainPath $previous; $null=Assert-PlainPath $backup
    if ([IO.Path]::GetDirectoryName($previous) -ne $custom -or [IO.Path]::GetDirectoryName($backup) -ne $backups) { throw 'Invalid backup target.' }
    if ((ConvertTo-Json @(Get-TreeManifest $previous) -Depth 5 -Compress) -ne (ConvertTo-Json @($oldFiles) -Depth 5 -Compress)) { throw 'Installed extension changed during staging; update aborted.' }
    @{previousName=[IO.Path]::GetFileName($previous); files=@($oldFiles)} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $backups ($id+'-rollback.json')) -Encoding UTF8
}
Write-InstallState 'replacing'
try {
    if ($existing.Count) { Move-Item -LiteralPath $previous -Destination $backup }
    $null=Assert-PlainPath $target
    if (Test-Path -LiteralPath $target) { throw 'Target appeared during staging.' }
    Move-Item -LiteralPath $stage -Destination $target
} catch {
    if ($existing.Count -and -not (Test-Path -LiteralPath $previous)) { Move-Item -LiteralPath $backup -Destination $previous }
    Write-InstallState 'rolled_back'
    throw
}
Write-InstallState 'completed'
Write-Output "Installed verified extension: $target"
if ($existing.Count) { Write-Output "Previous extension preserved outside custom_nodes: $backup" }
Write-Output 'No process was stopped. Restart ComfyUI and refresh the page to load this version. Desktop library settings were preserved.'
