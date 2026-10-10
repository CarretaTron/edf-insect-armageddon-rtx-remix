$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../runtime'))
Push-Location $repo
try {
  & cmd.exe /d /c 'call "D:\EDFBuildTools\Common7\Tools\VsDevCmd.bat" -no_logo -arch=amd64 -host_arch=amd64 && cl.exe /nologo /O2 /MD /EHsc /std:c++17 /utf-8 /DNOMINMAX /Iinclude /Iinclude\vulkan\include /Isrc\dxvk /Iexternal\usd_stock_x64\Release\include "..\analysis\edf\selected_draw\verify_runtime_projection.cpp" /Fo_build\edf_projection.obj /Fe_build\edf_projection.exe'
  if ($LASTEXITCODE) { throw 'Runtime projection helper probe did not compile.' }
  & '.\_build\edf_projection.exe' (Join-Path $PSScriptRoot 'runtime_projection_input.bin') |
    Tee-Object -FilePath (Join-Path $PSScriptRoot 'runtime_projection_verification.json')
  if ($LASTEXITCODE) { throw 'Runtime projection helper verification failed.' }
} finally {
  Pop-Location
}
