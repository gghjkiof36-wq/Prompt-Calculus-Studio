param(
    [Parameter(Mandatory=$true)][string]$ComfyUIRoot,
    [string]$DesktopData = (Join-Path $PSScriptRoot 'release\PromptStudio\data'),
    [switch]$Update
)
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'build\comfyui\comfyui_prompt_studio')).Path
$comfy = (Resolve-Path -LiteralPath $ComfyUIRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $comfy 'main.py'))) { throw 'Not a ComfyUI directory.' }
$custom = (Resolve-Path -LiteralPath (Join-Path $comfy 'custom_nodes')).Path
$target = [IO.Path]::GetFullPath((Join-Path $custom 'comfyui_prompt_studio'))
if ([IO.Path]::GetDirectoryName($target) -ne $custom) { throw 'Invalid installation target.' }
if (Test-Path -LiteralPath $target) {
    if (-not $Update) { throw 'Extension already exists. Use -Update to back it up before replacing program files.' }
    $existing = Get-Item -LiteralPath $target
    if ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to update a redirected extension directory.' }
    $backupRoot = Join-Path $PSScriptRoot 'release\program-backups'
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $backup = Join-Path $backupRoot ('comfy-before-0.7-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Copy-Item -LiteralPath $target -Destination $backup -Recurse
    Write-Output "Previous extension preserved: $backup"
} else {
    New-Item -ItemType Directory -Path $target | Out-Null
}
$library = (Resolve-Path -LiteralPath (Join-Path $DesktopData 'studio.sqlite3')).Path
foreach ($file in (Get-ChildItem -LiteralPath $source -Recurse -File)) {
    $relative = $file.FullName.Substring($source.Length + 1)
    $installed = [IO.Path]::GetFullPath((Join-Path $target $relative))
    if (-not $installed.StartsWith($target + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid program path.' }
    $parent = [IO.Path]::GetDirectoryName($installed)
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    if ((Get-Item -LiteralPath $parent).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to write through a directory link.' }
    if ((Test-Path -LiteralPath $installed) -and ((Get-Item -LiteralPath $installed).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Refusing to replace a file link.' }
    Copy-Item -LiteralPath $file.FullName -Destination $installed
}
$bootstrap = @{ library = $library } | ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $target 'local_library.json'), $bootstrap, (New-Object Text.UTF8Encoding($false)))
foreach ($file in (Get-ChildItem -LiteralPath $source -Recurse -File)) {
    $relative = $file.FullName.Substring($source.Length + 1)
    $installed = Join-Path $target $relative
    if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $installed).Hash) {
        throw "Installed file differs: $relative"
    }
}
Write-Output "Installed and verified: $target"
Write-Output "Read-only desktop library: $library"
Write-Output 'Restart ComfyUI and refresh its page to load Prompt Studio.'
