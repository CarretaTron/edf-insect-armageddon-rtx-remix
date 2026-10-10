param(
  [string]$PackageRoot = '',
  [string]$RuntimeOutput = '',
  [string]$BridgeOutput = ''
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $PackageRoot) { $PackageRoot = Join-Path (Split-Path $repositoryRoot -Parent) 'packages' }
if (-not $RuntimeOutput) { $RuntimeOutput = Join-Path $repositoryRoot '_output' }
if (-not $BridgeOutput) { $BridgeOutput = Join-Path $repositoryRoot 'bridge\_output' }
$PackageRoot = [IO.Path]::GetFullPath($PackageRoot)
$RuntimeOutput = [IO.Path]::GetFullPath($RuntimeOutput)
$BridgeOutput = [IO.Path]::GetFullPath($BridgeOutput)
$x64Root = Join-Path $PackageRoot 'edf-diagnostics-x64'
$x86Root = Join-Path $PackageRoot 'edf-diagnostics-x86'
foreach ($target in @($x64Root, $x86Root)) {
  if (Test-Path -LiteralPath $target) { throw "Use a new PackageRoot. Target already exists: $target" }
}
foreach ($requiredFile in @(
  (Join-Path $RuntimeOutput 'd3d9.dll'),
  (Join-Path $RuntimeOutput 'usd\plugInfo.json'),
  (Join-Path $BridgeOutput 'd3d9.dll'),
  (Join-Path $BridgeOutput '.trex\NvRemixBridge.exe')
)) {
  if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) { throw "Build output missing: $requiredFile" }
}

function Copy-RuntimeTree([string]$Destination) {
  New-Item -ItemType Directory -Path $Destination -Force | Out-Null
  foreach ($sourceFile in Get-ChildItem -LiteralPath $RuntimeOutput -Recurse -File) {
    if ($sourceFile.Extension -in @('.pdb', '.lib', '.exp', '.obj') -or
        $sourceFile.Name -like '*.pdb.*') { continue }
    $relativePath = $sourceFile.FullName.Substring($RuntimeOutput.Length).TrimStart('\')
    $destinationFile = Join-Path $Destination $relativePath
    New-Item -ItemType Directory -Path (Split-Path $destinationFile -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $sourceFile.FullName -Destination $destinationFile
  }
}

function Write-Manifest([string]$Root, [string]$Architecture) {
  $files = @(Get-ChildItem -LiteralPath $Root -Recurse -File -Force | Sort-Object FullName | ForEach-Object {
    [ordered]@{
      path = $_.FullName.Substring($Root.Length).TrimStart('\').Replace('\', '/')
      size = $_.Length
      sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
  })
  $manifest = [ordered]@{
    schema = 1
    runtimeBaseCommit = '0e18e55c04309c08e6206e768bc72b24ac83f853'
    localChanges = 'EDF bounded diagnostic observer; opt-in exact-mesh static decoder 252 vertices / 144 triangles; frame-local camera/injection audit; bounded offscreen path tracing with fence-backed readback and original-target preservation checks; Bridge UP/range and MultiplyTransform fixes'
    buildType = 'debugoptimized'
    architecture = $Architecture
    createdUtc = [DateTime]::UtcNow.ToString('o')
    files = $files
  }
  $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $Root 'package-manifest.json') -Encoding utf8
}

Copy-RuntimeTree $x64Root
Copy-RuntimeTree (Join-Path $x86Root '.trex')
Copy-Item -LiteralPath (Join-Path $BridgeOutput 'd3d9.dll') -Destination $x86Root
Copy-Item -LiteralPath (Join-Path $BridgeOutput '.trex\NvRemixBridge.exe') -Destination (Join-Path $x86Root '.trex')
Write-Manifest $x64Root 'x64-runtime'
Write-Manifest $x86Root 'x86-bridge-client + x64-server/runtime'
Write-Output "Runtime probe package: $x64Root"
Write-Output "EDF diagnostic package: $x86Root"
Write-Output 'The game installation has not been modified.'
