# Offline consistency checks. Hashes are not a signature or publisher identity.
$ErrorActionPreference = 'Stop'
function Assert-PlainPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $cursor = $full
    while ($cursor) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -Force -LiteralPath $cursor).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Redirected paths are not allowed.' }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $full
}
function Get-PlainFiles([string]$Root) {
    $rootPath = Assert-PlainPath $Root
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($rootPath)
    while ($pending.Count) {
        foreach ($entry in Get-ChildItem -Force -LiteralPath $pending.Pop()) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Package contains a redirected entry.' }
            if ($entry.PSIsContainer) { $pending.Push($entry.FullName) } else { $entry }
        }
    }
}
function Get-TreeManifest([string]$Root) {
    @(Get-PlainFiles $Root | Sort-Object FullName | ForEach-Object {
        [ordered]@{ path=$_.FullName.Substring($Root.Length+1).Replace('\','/'); size=$_.Length; sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
}
function Read-Package([string]$Source,[string]$Kind) {
    $sourcePath = Assert-PlainPath $Source
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) { throw 'Package directory is missing.' }
    $actual = @(Get-PlainFiles $sourcePath)
    $manifest = Get-Content -LiteralPath (Join-Path $sourcePath 'PACKAGE_MANIFEST.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.format -ne 'pcs-package-1' -or $manifest.kind -ne $Kind -or -not $manifest.files.Count) { throw 'Invalid package manifest.' }
    $seen = @{}
    foreach ($entry in $manifest.files) {
        $name = [string]$entry.path
        if (-not $name -or $name -match '(^/|\\|:|(^|/)\.\.?(/|$))' -or $seen.ContainsKey($name) -or $name -eq 'PACKAGE_MANIFEST.json') { throw 'Invalid package entry.' }
        $target = [IO.Path]::GetFullPath((Join-Path $sourcePath $name))
        if (-not $target.StartsWith($sourcePath + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Package path escaped its root.' }
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { throw "Missing package file: $name" }
        $file = Get-Item -LiteralPath $target
        if ($file.Length -ne $entry.size -or (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $entry.sha256) { throw "Package hash mismatch: $name" }
        $seen[$name]=$true
    }
    if ($actual.Count -ne $seen.Count+1) { throw 'Package includes unlisted files.' }
    foreach ($file in $actual) {
        $name=$file.FullName.Substring($sourcePath.Length+1).Replace('\','/')
        if ($name -ne 'PACKAGE_MANIFEST.json' -and -not $seen.ContainsKey($name)) { throw 'Package includes unlisted files.' }
        if ($name -match '(^|/)(data|local_library\.json|credentials)(/|$)') { throw 'Package includes local data.' }
    }
    $info = Get-Content -LiteralPath (Join-Path $sourcePath 'BUILD_INFO.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($info.release -ne $manifest.release -or $info.version -ne $manifest.version -or $info.git_head -ne $manifest.git_head -or $info.git_head -notmatch '^[0-9a-f]{40}$' -or $info.working_changes) { throw 'Build identity differs from the manifest or has uncommitted changes.' }
    foreach ($required in @('SOURCE_MANIFEST.json','PromptCalculusStudio-source.zip')) {
        if (-not $seen.ContainsKey($required)) { throw 'Source evidence is missing.' }
    }
    if ((Get-FileHash -LiteralPath (Join-Path $sourcePath 'PromptCalculusStudio-source.zip')).Hash -ne $info.source_sha256) { throw 'Source archive identity differs.' }
    if ($Kind -eq 'desktop') {
        if (-not $seen.ContainsKey('PromptCalculusStudio.exe')) { throw 'Desktop executable is missing.' }
        $runtimePath=Join-Path $sourcePath '_internal/prompt_calculus_studio/assets/build-info.json'
        if (-not (Test-Path -LiteralPath $runtimePath -PathType Leaf)) { $runtimePath=Join-Path $sourcePath '_internal/prompt_studio/assets/build-info.json' }
        $runtime=Get-Content -LiteralPath $runtimePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($runtime.release -ne $info.release -or $runtime.version -ne $info.version -or $info.binary_git_head -ne $info.git_head) { throw 'Desktop runtime version differs.' }
    } elseif (-not $seen.ContainsKey('__init__.py') -or -not $seen.ContainsKey('web/prompt_studio.js')) { throw 'Extension runtime is missing.' }
    return $sourcePath
}
function Copy-Package([string]$Source,[string]$Destination) {
    if (Test-Path -LiteralPath $Destination) { throw 'Staging destination already exists.' }
    $null=Assert-PlainPath $Destination
    New-Item -ItemType Directory -Path $Destination | Out-Null
    foreach ($file in Get-PlainFiles $Source) {
        $target=Join-Path $Destination $file.FullName.Substring($Source.Length+1)
        New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($target)) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target
    }
}
