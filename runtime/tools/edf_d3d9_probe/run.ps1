param(
  [Parameter(Mandatory = $true)]
  [string]$ProbeDirectory,
  [ValidateRange(1, 16)]
  [int]$Frames = 3
)

$ErrorActionPreference = 'Stop'
$ProbeDirectory = [IO.Path]::GetFullPath($ProbeDirectory)
$executable = Join-Path $ProbeDirectory 'edf_d3d9_probe.exe'
if (-not (Test-Path -LiteralPath $executable)) {
  throw 'ProbeDirectory must point to a directory prepared by build.ps1.'
}
Push-Location $ProbeDirectory
try {
  & $executable --frames $Frames
  if ($LASTEXITCODE) {
    throw "D3D9 probe failed with exit code $LASTEXITCODE"
  }
} finally {
  Pop-Location
}
