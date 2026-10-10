param(
  [string]$BuildToolsRoot = '',
  [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
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
$buildEnvironment = & cmd.exe /d /c "call `"$devCommand`" -no_logo -arch=amd64 -host_arch=amd64 > nul && set"
if ($LASTEXITCODE) {
  throw 'Could not initialize the Visual Studio build environment.'
}
$importedNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($environmentLine in $buildEnvironment) {
  if ($environmentLine -match '^([^=]+)=(.*)$' -and $importedNames.Add($Matches[1])) {
    Set-Item -LiteralPath ('Env:' + $Matches[1]) -Value $Matches[2]
  }
}
$compiler = Join-Path $env:VCToolsInstallDir 'bin\Hostx64\x64\cl.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
  throw 'The MSVC x64 compiler is not installed.'
}
$harness = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\edf_capture_writer_tests\run.ps1'))
& $harness -Compiler $compiler -OutputDirectory $OutputDirectory
