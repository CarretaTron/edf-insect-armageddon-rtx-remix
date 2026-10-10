param(
  [string]$Compiler = 'cl',
  [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $OutputDirectory) {
  $OutputDirectory = Join-Path $repositoryRoot '_build\edf_capture_writer_tests'
}
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$executable = Join-Path $OutputDirectory 'writer_tests.exe'
$sources = @(
  (Join-Path $PSScriptRoot 'writer_tests.cpp'),
  (Join-Path $repositoryRoot 'src\util\util_compat_capture.cpp'),
  (Join-Path $repositoryRoot 'src\util\sha1\sha1_util.cpp'),
  (Join-Path $repositoryRoot 'src\util\sha1\sha1.c')
)

Push-Location $OutputDirectory
try {
  if ([IO.Path]::GetFileNameWithoutExtension($Compiler) -eq 'cl') {
    & $Compiler /nologo /std:c++17 /EHsc /W4 /TP "/Fe:$executable" @sources
  } else {
    & $Compiler -std=c++17 -pthread -Wall -Wextra -x c++ @sources -o $executable
  }
  if ($LASTEXITCODE) {
    throw "Compilation failed with exit code $LASTEXITCODE"
  }
  & $executable (Join-Path $OutputDirectory 'artifacts')
  if ($LASTEXITCODE) {
    throw "Writer tests failed with exit code $LASTEXITCODE"
  }
} finally {
  Pop-Location
}
