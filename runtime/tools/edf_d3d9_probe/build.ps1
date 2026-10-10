param(
  [ValidateSet('x64', 'x86')]
  [string]$Architecture = 'x64',
  [string]$BuildToolsRoot = '',
  [string]$RuntimeDirectory = '',
  [string]$OutputDirectory = '',
  [ValidateRange(1, 16)]
  [int]$Frames = 3,
  [switch]$Run
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $BuildToolsRoot) {
  $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
  if (-not (Test-Path -LiteralPath $vswhere)) {
    throw 'Pass -BuildToolsRoot with a Visual Studio Build Tools installation.'
  }
  $BuildToolsRoot = & $vswhere -all -products '*' -property installationPath | Select-Object -First 1
}
$devCommand = Join-Path $BuildToolsRoot 'Common7\Tools\VsDevCmd.bat'
if (-not (Test-Path -LiteralPath $devCommand) -or $devCommand.Contains('"')) {
  throw 'Invalid Build Tools installation path.'
}
$buildEnvironment = & cmd.exe /d /c "call `"$devCommand`" -no_logo -arch=$Architecture -host_arch=amd64 > nul && set"
if ($LASTEXITCODE) {
  throw 'Could not initialize the Visual Studio build environment.'
}
$importedNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($environmentLine in $buildEnvironment) {
  if ($environmentLine -match '^([^=]+)=(.*)$' -and $importedNames.Add($Matches[1])) {
    Set-Item -LiteralPath ('Env:' + $Matches[1]) -Value $Matches[2]
  }
}
$compiler = Join-Path $env:VCToolsInstallDir ('bin\Hostx64\' + $Architecture + '\cl.exe')
if (-not (Test-Path -LiteralPath $compiler)) {
  throw 'The selected MSVC architecture is not installed.'
}

if (-not $OutputDirectory) {
  $session = (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '_' + $Architecture
  $OutputDirectory = Join-Path $repositoryRoot ('_build\edf_d3d9_probe\' + $session)
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath (Join-Path $OutputDirectory 'd3d9.dll')) {
  throw 'Use a new output directory to avoid mixing runtime copies between tests.'
}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$executable = Join-Path $OutputDirectory 'edf_d3d9_probe.exe'
$sources = @(
  (Join-Path $PSScriptRoot 'probe.cpp'),
  (Join-Path $repositoryRoot 'src\util\sha1\sha1_util.cpp'),
  (Join-Path $repositoryRoot 'src\util\sha1\sha1.c')
)

Push-Location $OutputDirectory
try {
  & $compiler /nologo /std:c++17 /EHsc /W4 /TP /DUNICODE /D_UNICODE /DNOMINMAX "/Fe:$executable" @sources /link user32.lib
  if ($LASTEXITCODE) {
    throw "Compilation failed with exit code $LASTEXITCODE"
  }
  if ($RuntimeDirectory) {
    $RuntimeDirectory = [IO.Path]::GetFullPath($RuntimeDirectory)
    $runtimeDll = Join-Path $RuntimeDirectory 'd3d9.dll'
    if (-not (Test-Path -LiteralPath $runtimeDll)) {
      throw 'RuntimeDirectory must contain the app-local d3d9.dll and its complete runtime bundle.'
    }
    $stream = [IO.File]::OpenRead($runtimeDll)
    $reader = [IO.BinaryReader]::new($stream)
    try {
      if ($reader.ReadUInt16() -ne 0x5a4d) { throw 'Runtime DLL has no MZ header.' }
      $stream.Position = 0x3c
      $peOffset = $reader.ReadUInt32()
      $stream.Position = $peOffset
      if ($reader.ReadUInt32() -ne 0x00004550) { throw 'Runtime DLL has no PE header.' }
      $machine = $reader.ReadUInt16()
      $expectedMachine = if ($Architecture -eq 'x64') { 0x8664 } else { 0x014c }
      if ($machine -ne $expectedMachine) { throw 'Runtime d3d9.dll does not match the selected probe architecture.' }
    } finally {
      $reader.Dispose()
      $stream.Dispose()
    }
    foreach ($runtimeFile in Get-ChildItem -LiteralPath $RuntimeDirectory -Force) {
      Copy-Item -LiteralPath $runtimeFile.FullName -Destination $OutputDirectory -Recurse
    }
  }
  Write-Output "Probe compiled: $executable"
  Write-Output 'No application is launched unless -Run is specified.'
  if ($Run) {
    & $executable --frames $Frames
    if ($LASTEXITCODE) {
      throw "D3D9 probe failed with exit code $LASTEXITCODE"
    }
  }
} finally {
  Pop-Location
}
