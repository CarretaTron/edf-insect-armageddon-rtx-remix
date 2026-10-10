param(
  [ValidateSet('x64', 'x86')]
  [string]$Architecture = 'x64',
  [string]$BuildToolsRoot = 'D:\EDFBuildTools',
  [string]$Python = 'D:\EDF IA RTX REMIX\.tools\build-python\Scripts\python.exe',
  [string]$Manifest = 'D:\EDF IA RTX REMIX\analysis\edf\new_candidate\replay_manifest.json',
  [string]$RuntimeDirectory = '',
  [string]$OutputDirectory = '',
  [ValidateRange(1, 90)]
  [int]$Frames = 3,
  [ValidateRange(-1, 89)]
  [int]$SkipTargetFrame = -1,
  [ValidateRange(-1, 89)]
  [int]$MutateTargetFrame = -1,
  [ValidateRange(-1, 89)]
  [int]$DuplicateTargetFrame = -1,
  [switch]$PresentationPattern,
  [switch]$NoControl,
  [ValidateRange(0, 2)]
  [int]$SourceCamera = 0,
  [ValidateRange(-1, 89)]
  [int]$ResetBeforeFrame = -1,
  [switch]$NonidentityGamma,
  [switch]$PartialPresent,
  [switch]$Run
)

$ErrorActionPreference = 'Stop'
if ($SkipTargetFrame -ge $Frames) {
  throw 'SkipTargetFrame must be inside the requested frame range.'
}
if ($MutateTargetFrame -ge $Frames -or $DuplicateTargetFrame -ge $Frames) {
  throw 'Mutation and duplicate frame indices must be inside the requested frame range.'
}
if ($ResetBeforeFrame -ge $Frames) { throw 'ResetBeforeFrame must be inside the requested frame range.' }
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $OutputDirectory) {
  $OutputDirectory = Join-Path $repo ('_build\edf_static_gpu_replay\' + $Architecture)
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
& $Python (Join-Path $PSScriptRoot 'make_fixture.py') $Manifest (Join-Path $OutputDirectory 'replay_fixture.bin')
if ($LASTEXITCODE) { throw 'GPU replay fixture export failed.' }
$devCommand = Join-Path $BuildToolsRoot 'Common7\Tools\VsDevCmd.bat'
$buildEnvironment = & cmd.exe /d /c "call `"$devCommand`" -no_logo -arch=$Architecture -host_arch=amd64 > nul && set"
if ($LASTEXITCODE) { throw 'Could not initialize the Visual Studio build environment.' }
$importedNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($environmentLine in $buildEnvironment) {
  if ($environmentLine -match '^([^=]+)=(.*)$' -and $importedNames.Add($Matches[1])) {
    Set-Item -LiteralPath ('Env:' + $Matches[1]) -Value $Matches[2]
  }
}
$compiler = Join-Path $env:VCToolsInstallDir ('bin\Hostx64\' + $Architecture + '\cl.exe')
Push-Location $OutputDirectory
try {
  & $compiler /nologo /O2 /MD /EHsc /std:c++17 /W4 /utf-8 /DNOMINMAX /DUNICODE /D_UNICODE `
    (Join-Path $PSScriptRoot 'replay.cpp') /Feedf_static_gpu_replay.exe /link user32.lib
  if ($LASTEXITCODE) { throw 'GPU replay tool did not compile.' }
  if ($RuntimeDirectory) {
    if (Test-Path -LiteralPath (Join-Path $OutputDirectory 'd3d9.dll')) {
      throw 'Use a fresh output directory when copying a Runtime bundle.'
    }
    foreach ($file in Get-ChildItem -LiteralPath $RuntimeDirectory -Force) {
      Copy-Item -LiteralPath $file.FullName -Destination $OutputDirectory -Recurse
    }
  }
  Write-Output ('Compiled: ' + (Join-Path $OutputDirectory 'edf_static_gpu_replay.exe'))
  Write-Output ('Executable SHA1: ' + (Get-FileHash -LiteralPath '.\edf_static_gpu_replay.exe' -Algorithm SHA1).Hash)
  Write-Output 'No GPU application is started without -Run. Set the explicit replay executable/fingerprint profile before running.'
  if ($Run) {
    $replayArguments = @('--frames', $Frames, '--source-camera', $SourceCamera)
    if ($SkipTargetFrame -ge 0) {
      $replayArguments += @('--skip-target-frame', $SkipTargetFrame)
    }
    if ($MutateTargetFrame -ge 0) { $replayArguments += @('--mutate-target-frame', $MutateTargetFrame) }
    if ($DuplicateTargetFrame -ge 0) { $replayArguments += @('--duplicate-target-frame', $DuplicateTargetFrame) }
    if ($PresentationPattern) { $replayArguments += '--presentation-pattern' }
    if ($NoControl) { $replayArguments += '--no-control' }
    if ($ResetBeforeFrame -ge 0) { $replayArguments += @('--reset-before-frame', $ResetBeforeFrame) }
    if ($NonidentityGamma) { $replayArguments += '--nonidentity-gamma' }
    if ($PartialPresent) { $replayArguments += '--partial-present' }
    & '.\edf_static_gpu_replay.exe' @replayArguments | Tee-Object -FilePath 'replay_stdout.jsonl'
    if ($LASTEXITCODE) { throw 'GPU replay failed.' }
  }
} finally {
  Pop-Location
}
