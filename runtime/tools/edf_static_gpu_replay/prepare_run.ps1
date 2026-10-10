param(
  [Parameter(Mandatory=$true)][string]$PackageRoot,
  [Parameter(Mandatory=$true)][string]$OutputDirectory,
  [ValidateSet('x64','x86')][string]$Architecture='x86',
  [switch]$Baseline
)
$ErrorActionPreference='Stop'
$repositoryRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$OutputDirectory=[IO.Path]::GetFullPath($OutputDirectory)
if(Test-Path -LiteralPath $OutputDirectory) { throw 'Use a fresh output directory.' }
$bundle=Join-Path $PackageRoot ('edf-diagnostics-'+$Architecture)
$built=Join-Path $repositoryRoot ('_build\edf_static_gpu_replay\'+$Architecture)
foreach($file in @((Join-Path $bundle 'd3d9.dll'),(Join-Path $built 'edf_static_gpu_replay.exe'),(Join-Path $built 'replay_fixture.bin'))) {
  if(-not(Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required file missing: $file" }
}
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
foreach($file in Get-ChildItem -LiteralPath $bundle -Force) { Copy-Item -LiteralPath $file.FullName -Destination $OutputDirectory -Recurse }
Copy-Item -LiteralPath (Join-Path $built 'edf_static_gpu_replay.exe'),(Join-Path $built 'replay_fixture.bin') -Destination $OutputDirectory
$exe=Join-Path $OutputDirectory 'edf_static_gpu_replay.exe'
$fingerprint=(Get-FileHash -LiteralPath $exe -Algorithm SHA1).Hash.ToLowerInvariant()
$enable=if($Baseline) {'False'} else {'True'}
$capture=(Join-Path $OutputDirectory 'captures').Replace('\','/')
@"
rtx.enableRaytracing = $enable
rtx.edfStatic.enabled = $enable
rtx.edfStatic.rasterOnly = True
rtx.edfStatic.executable = "edf_static_gpu_replay.exe"
rtx.edfStatic.executableSha1 = "$fingerprint"
rtx.useVertexCapture = True
rtx.fusedWorldViewMode = 0
rtx.initializer.asyncShaderPrewarming = False
rtx.shader.enableAsyncCompilation = False
rtx.compatCapture.enabled = True
rtx.compatCapture.executable = "edf_static_gpu_replay.exe"
rtx.compatCapture.outputDirectory = "$capture"
rtx.compatCapture.startFrame = 0
rtx.compatCapture.frameCount = 3
rtx.compatCapture.hotkey = 0
rtx.compatCapture.maxCaptureMiB = 256
rtx.compatCapture.maxQueueMiB = 64
"@ | Set-Content -LiteralPath (Join-Path $OutputDirectory 'rtx.conf') -Encoding utf8
Write-Output "Prepared $Architecture replay: $OutputDirectory"
Write-Output "Executable SHA1: $fingerprint"
Write-Output 'No executable was started.'
