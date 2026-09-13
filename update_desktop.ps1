# Replace verified program files only. The portable data directory is untouched.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$package = (Resolve-Path -LiteralPath (Join-Path $root 'build\package\PromptStudio')).Path
$release = (Resolve-Path -LiteralPath (Join-Path $root 'release\PromptStudio')).Path
$executable = Join-Path $release 'PromptStudio.exe'
if (Get-Process PromptStudio -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $executable }) {
    throw 'Close Prompt Studio before replacing its program files.'
}
if (-not $release.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Release must stay inside the project.' }
if ((Get-Item -LiteralPath $release).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Release must not be redirected.' }
$database = Join-Path $release 'data\studio.sqlite3'
$before = (Get-FileHash -LiteralPath $database).Hash
$backup = Join-Path $root ('release\program-backups\before-0.7.1-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($name in @('PromptStudio.exe','_internal','README.md','使用說明.txt','COMFYUI_GUIDE.md','IMPLEMENTATION_NOTES.md','CLEAN_EXPORT_GUIDE.md')) {
    $previous = Join-Path $release $name
    if (Test-Path -LiteralPath $previous) { Copy-Item -LiteralPath $previous -Destination (Join-Path $backup $name) -Recurse }
}
Copy-Item -LiteralPath $database -Destination (Join-Path $backup 'studio.sqlite3')
foreach ($file in (Get-ChildItem -LiteralPath $package -File -Recurse)) {
    $relative = $file.FullName.Substring($package.Length + 1)
    if ($relative -ne 'PromptStudio.exe' -and -not $relative.StartsWith('_internal\')) { throw "Unexpected program entry: $relative" }
    $destination = [IO.Path]::GetFullPath((Join-Path $release $relative))
    if (-not $destination.StartsWith($release + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid program destination.' }
    $parent = [IO.Path]::GetDirectoryName($destination)
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    if ((Get-Item -LiteralPath $parent).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to write through a directory link.' }
    Copy-Item -LiteralPath $file.FullName -Destination $destination
    if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath $destination).Hash) { throw "File verification failed: $relative" }
}
foreach ($name in @('README.md','IMPLEMENTATION_NOTES.md','使用說明.txt','COMFYUI_GUIDE.md','NEXT_UI.md','CLEAN_EXPORT_GUIDE.md')) {
    Copy-Item -LiteralPath (Join-Path $root $name) -Destination (Join-Path $release $name)
}
if ((Get-FileHash -LiteralPath $database).Hash -ne $before) { throw 'The database changed during program replacement.' }
Write-Output "Updated and verified: $executable"
Write-Output "Previous version and database backup: $backup"
Write-Output 'Portable data and image files preserved.'
