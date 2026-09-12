param(
    [Parameter(Mandatory=$true)][string]$ComfyUIRoot,
    [string]$DesktopData = (Join-Path $PSScriptRoot 'release\PromptStudio\data')
)
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'build\comfyui\comfyui_prompt_studio')).Path
$comfy = (Resolve-Path -LiteralPath $ComfyUIRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $comfy 'main.py'))) { throw 'Not a ComfyUI directory.' }
$custom = (Resolve-Path -LiteralPath (Join-Path $comfy 'custom_nodes')).Path
$target = [IO.Path]::GetFullPath((Join-Path $custom 'comfyui_prompt_studio'))
if ([IO.Path]::GetDirectoryName($target) -ne $custom) { throw 'Invalid installation target.' }
if (Test-Path -LiteralPath $target) { throw 'Extension already exists. Preserve it before installing a new version.' }
$library = (Resolve-Path -LiteralPath (Join-Path $DesktopData 'studio.sqlite3')).Path
Copy-Item -LiteralPath $source -Destination $target -Recurse
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
