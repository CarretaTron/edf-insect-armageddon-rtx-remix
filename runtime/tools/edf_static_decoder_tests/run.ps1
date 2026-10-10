param(
  [string]$CaptureRoot = 'D:\EDF IA RTX REMIX\captures\edf-static',
  [string]$Python = 'D:\EDF IA RTX REMIX\.tools\build-python\Scripts\python.exe',
  [string]$BuildToolsRoot = 'D:\EDFBuildTools'
)

$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$output = Join-Path $repo '_build\edf_static_decoder_tests'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$fixture = Join-Path $output 'captured_fixture.bin'
& $Python (Join-Path $PSScriptRoot 'make_fixture.py') $CaptureRoot $fixture
if ($LASTEXITCODE) { throw 'Captured shader oracle generation failed.' }
$devCommand = Join-Path $BuildToolsRoot 'Common7\Tools\VsDevCmd.bat'
$buildEnvironment = & cmd.exe /d /c "call `"$devCommand`" -no_logo -arch=amd64 -host_arch=amd64 > nul && set"
if ($LASTEXITCODE) { throw 'Could not initialize the Visual Studio build environment.' }
$importedNames = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($environmentLine in $buildEnvironment) {
  if ($environmentLine -match '^([^=]+)=(.*)$' -and $importedNames.Add($Matches[1])) {
    Set-Item -LiteralPath ('Env:' + $Matches[1]) -Value $Matches[2]
  }
}
$compiler = Join-Path $env:VCToolsInstallDir 'bin\Hostx64\x64\cl.exe'
Push-Location $output
try {
  & $compiler /nologo /O2 /MD /EHsc /std:c++17 /W4 /utf-8 /DNOMINMAX `
    (Join-Path $repo 'tools\edf_static_decoder_tests\decoder_tests.cpp') `
    (Join-Path $repo 'src\d3d9\d3d9_edf_static_decoder.cpp') `
    (Join-Path $repo 'src\util\sha1\sha1_util.cpp') `
    (Join-Path $repo 'src\util\sha1\sha1.c') /Fetest.exe
  if ($LASTEXITCODE) { throw 'Decoder standalone replay test did not compile.' }
  & '.\test.exe' $fixture | Tee-Object -FilePath 'verification.jsonl'
  if ($LASTEXITCODE) { throw 'Decoder standalone replay test failed.' }
} finally {
  Pop-Location
}
