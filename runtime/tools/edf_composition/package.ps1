param(
  [string]$PackageRoot = 'D:\EDF IA RTX REMIX\packages\edf-composition-preview',
  [string]$Renderer = 'D:\EDF IA RTX REMIX\runtime\_CompDebugOptimized_x64\src\d3d9\d3d9.dll',
  [switch]$Refresh
)
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$PackageRoot = [IO.Path]::GetFullPath($PackageRoot)
$Renderer = [IO.Path]::GetFullPath($Renderer)
if ((Test-Path -LiteralPath $PackageRoot) -and -not $Refresh) { throw 'Use a fresh PackageRoot.' }
if (-not (Test-Path -LiteralPath $Renderer -PathType Leaf)) { throw 'Built renderer is missing.' }
$sources = @('src/d3d9/d3d9_swapchain.cpp','src/d3d9/d3d9_swapchain.h',
  'src/d3d9/d3d9_rtx.cpp','src/vulkan/vulkan_presenter.cpp','src/vulkan/vulkan_presenter.h',
  'src/dxvk/rtx_render/rtx_context.cpp','src/dxvk/rtx_render/rtx_context.h',
  'src/dxvk/rtx_render/rtx_edf_static_composition.cpp','src/dxvk/rtx_render/rtx_edf_static_composition.h',
  'src/dxvk/rtx_render/rtx_edf_static_probe.cpp','src/dxvk/rtx_render/rtx_edf_static_probe.h',
  'src/dxvk/shaders/rtx/pass/edf_static_composition/edf_static_composition.h',
  'src/dxvk/shaders/rtx/pass/edf_static_composition/edf_static_composition.comp.slang',
  'src/dxvk/shaders/rtx/pass/edf_static_composition/edf_static_composition_bounds.comp.slang',
  'src/dxvk/shaders/rtx/pass/edf_static_composition/edf_static_composition_present.comp.slang')
$rendererTime = (Get-Item -LiteralPath $Renderer).LastWriteTimeUtc
$sourceHashes = @($sources | ForEach-Object {
  $source = Join-Path $repo $_
  if ((Get-Item -LiteralPath $source).LastWriteTimeUtc -gt $rendererTime) { throw "Source newer than renderer: $_" }
  [ordered]@{ path=$_; sha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() }
})
if ($Refresh) {
  $rendererHash = (Get-FileHash -LiteralPath $Renderer -Algorithm SHA256).Hash.ToLowerInvariant()
  foreach ($architecture in @('x64','x86')) {
    $bundle = Join-Path $PackageRoot ('edf-diagnostics-' + $architecture)
    $manifestPath = Join-Path $bundle 'package-manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    foreach ($file in $manifest.files) {
      $target = Join-Path $bundle $file.path.Replace('/', '\')
      if ((Get-Item -LiteralPath $target).Length -ne $file.size -or
        (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $file.sha256) { throw "Prior package integrity failure: $target" }
    }
    $relative = if ($architecture -eq 'x64') { 'd3d9.dll' } else { '.trex/d3d9.dll' }
    $target = Join-Path $bundle $relative
    Copy-Item -LiteralPath $Renderer -Destination ($target+'.composition-new')
    Move-Item -LiteralPath ($target+'.composition-new') -Destination $target -Force
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $rendererHash) { throw 'Refreshed renderer differs.' }
    $entry = @($manifest.files | Where-Object path -eq $relative)
    if ($entry.Count -ne 1) { throw 'Manifest renderer entry is ambiguous.' }
    $entry[0].sha256 = $rendererHash
    $entry[0].size = (Get-Item -LiteralPath $target).Length
    $manifest.rendererSha256 = $rendererHash
    $manifest.compositionSourceHashes = $sourceHashes
    $manifest.createdUtc = [DateTime]::UtcNow.ToString('o')
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8
  }
  Write-Output ('Refreshed workspace package renderer SHA256: ' + $rendererHash)
  Write-Output 'No game installation was modified.'
  return
}
# Dependencies and Bridge come from the already built matching checkout. Keep
# the previous _output and all previous packages unchanged.
$staging = Join-Path $repo ('_build\edf_composition_package_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $staging | Out-Null
foreach ($source in Get-ChildItem -LiteralPath (Join-Path $repo '_output') -Force) {
  if ($source.Extension -in @('.pdb','.lib','.exp','.obj') -or $source.Name -like '*.pdb.*') { continue }
  Copy-Item -LiteralPath $source.FullName -Destination $staging -Recurse
}
Copy-Item -LiteralPath $Renderer -Destination (Join-Path $staging 'd3d9.dll') -Force
& (Join-Path $repo 'tools\edf_capture\package.ps1') -PackageRoot $PackageRoot -RuntimeOutput $staging
$rendererHash = (Get-FileHash -LiteralPath $Renderer -Algorithm SHA256).Hash.ToLowerInvariant()
foreach ($architecture in @('x64','x86')) {
  $bundle = Join-Path $PackageRoot ('edf-diagnostics-' + $architecture)
  $manifestPath = Join-Path $bundle 'package-manifest.json'
  $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  $manifest.localChanges = 'Bounded EDF diagnostic capture; exact 252-vertex / 144-triangle static decoder; guarded offscreen path tracing; opt-in current-frame WSI diagnostic panel, original target and outside-ROI GPU readback verification. Composition default off.'
  $manifest | Add-Member -NotePropertyName rendererSha256 -NotePropertyValue $rendererHash
  $manifest | Add-Member -NotePropertyName compositionSourceHashes -NotePropertyValue $sourceHashes
  $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding utf8
  foreach ($file in $manifest.files) {
    $target = Join-Path $bundle $file.path.Replace('/', '\')
    if ((Get-Item -LiteralPath $target).Length -ne $file.size -or
      (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $file.sha256) { throw "Package integrity failure: $target" }
  }
}
Write-Output ('Verified renderer SHA256: ' + $rendererHash)
Write-Output 'No game installation was modified.'
