param([string]$OutputDirectory = '')

$ErrorActionPreference = 'Stop'
$deploy = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\deploy.ps1'))
if (-not $OutputDirectory) {
  $repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
  $OutputDirectory = Join-Path $repositoryRoot '_build\edf_deployment_tests'
}
$root = [IO.Path]::GetFullPath((Join-Path $OutputDirectory ([Guid]::NewGuid().ToString('N'))))
$game = Join-Path $root 'fake-game'
$bundle = Join-Path $root 'fake-bundle'
$profile = Join-Path $root 'edf-observe.conf'
[void][IO.Directory]::CreateDirectory((Join-Path $game '.trex'))
[void][IO.Directory]::CreateDirectory((Join-Path $bundle '.trex\nested'))

function Assert-True([bool]$Condition, [string]$Message) {
  if (-not $Condition) { throw $Message }
}
function Write-TestFile([string]$Path, [string]$Content) {
  [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}
function Read-TestFile([string]$Path) { return [IO.File]::ReadAllText($Path) }

Write-TestFile (Join-Path $game 'd3d9.dll') 'original-client'
Write-TestFile (Join-Path $game 'rtx.conf') 'original-configuration'
Write-TestFile (Join-Path $game 'unrelated.txt') 'keep-this-file'
Write-TestFile (Join-Path $game '.trex\unrelated.dll') 'keep-this-server-file'
Write-TestFile (Join-Path $bundle 'd3d9.dll') 'diagnostic-client'
Write-TestFile (Join-Path $bundle '.trex\d3d9.dll') 'diagnostic-server'
Write-TestFile (Join-Path $bundle '.trex\NvRemixBridge.exe') 'diagnostic-bridge'
Write-TestFile (Join-Path $bundle '.trex\nested\plugin.dll') 'diagnostic-plugin'
Write-TestFile $profile "rtx.enableRaytracing = False`nrtx.compatCapture.enabled = True`n"

& $deploy -Action Install -GameRoot $game -BundleRoot $bundle -ProfilePath $profile | Out-Null
Assert-True ((Read-TestFile (Join-Path $game 'd3d9.dll')) -eq 'diagnostic-client') 'Client was not installed.'
Assert-True ((Read-TestFile (Join-Path $game 'rtx.conf')) -eq (Read-TestFile $profile)) 'Profile was not installed.'
Assert-True ((Read-TestFile (Join-Path $game 'unrelated.txt')) -eq 'keep-this-file') 'Unrelated file was changed.'
$active = Get-Content -LiteralPath (Join-Path $game '.edf-remix-backup\active.json') -Raw | ConvertFrom-Json
$journalPath = Join-Path $game ('.edf-remix-backup\' + $active.session + '\journal.json')
$journal = Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json
Assert-True ($journal.entries.Count -eq 5 -and $journal.status -eq 'installed') 'Journal lacks installed files.'
Assert-True ((Read-TestFile (Join-Path $game ('.edf-remix-backup\' + $active.session + '\original\d3d9.dll'))) -eq 'original-client') 'Original client was not backed up.'

Write-TestFile (Join-Path $game 'rtx.conf') 'user-edited-configuration'
$refused = $false
try { & $deploy -Action Restore -GameRoot $game | Out-Null } catch { $refused = $true }
Assert-True $refused 'Restore did not refuse a user-edited file.'
Assert-True ((Read-TestFile (Join-Path $game 'rtx.conf')) -eq 'user-edited-configuration') 'Restore overwrote user changes.'
Assert-True ((Read-TestFile (Join-Path $game 'd3d9.dll')) -eq 'diagnostic-client') 'Restore mutated files before completing preflight.'
Write-TestFile (Join-Path $game 'rtx.conf') (Read-TestFile $profile)

& $deploy -Action Restore -GameRoot $game | Out-Null
Assert-True ((Read-TestFile (Join-Path $game 'd3d9.dll')) -eq 'original-client') 'Original client was not restored.'
Assert-True ((Read-TestFile (Join-Path $game 'rtx.conf')) -eq 'original-configuration') 'Original profile was not restored.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $game '.trex\d3d9.dll'))) 'Installed server was not removed.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $game '.trex\nested'))) 'New empty directory was not removed.'
Assert-True ((Read-TestFile (Join-Path $game '.trex\unrelated.dll')) -eq 'keep-this-server-file') 'Unrelated .trex file was changed.'
Assert-True ((Read-TestFile (Join-Path $game 'unrelated.txt')) -eq 'keep-this-file') 'Unrelated root file was changed.'
Assert-True (Test-Path -LiteralPath $journalPath) 'Restore deleted backup history.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $game '.edf-remix-backup\active.json'))) 'Restore kept an active deployment pointer.'
& $deploy -Action Restore -GameRoot $game | Out-Null

& $deploy -Action Install -GameRoot $game -BundleRoot $bundle -ProfilePath $profile | Out-Null
$active = Get-Content -LiteralPath (Join-Path $game '.edf-remix-backup\active.json') -Raw | ConvertFrom-Json
$journalPath = Join-Path $game ('.edf-remix-backup\' + $active.session + '\journal.json')
$journal = Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json
$clientBackup = Join-Path $game ('.edf-remix-backup\' + $active.session + '\original\d3d9.dll')
[IO.File]::Copy($clientBackup, (Join-Path $game 'd3d9.dll'), $true)
[IO.File]::Delete((Join-Path $game '.trex\d3d9.dll'))
$journal.status = 'restoring'
Write-TestFile $journalPath ($journal | ConvertTo-Json -Depth 12)
& $deploy -Action Restore -GameRoot $game | Out-Null
Assert-True ((Read-TestFile (Join-Path $game 'd3d9.dll')) -eq 'original-client') 'Resuming Restore changed an already restored original.'
Assert-True ((Read-TestFile (Join-Path $game 'rtx.conf')) -eq 'original-configuration') 'Resuming Restore did not finish remaining originals.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $game '.trex\NvRemixBridge.exe'))) 'Resuming Restore did not remove remaining installed files.'

& $deploy -Action Install -GameRoot $game -BundleRoot $bundle -ProfilePath $profile | Out-Null
$active = Get-Content -LiteralPath (Join-Path $game '.edf-remix-backup\active.json') -Raw | ConvertFrom-Json
$journalPath = Join-Path $game ('.edf-remix-backup\' + $active.session + '\journal.json')
$journal = Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json
$journal.entries[0].relativePath = '..\outside.txt'
Write-TestFile $journalPath ($journal | ConvertTo-Json -Depth 12)
$refused = $false
try { & $deploy -Action Restore -GameRoot $game | Out-Null } catch { $refused = $true }
Assert-True $refused 'Restore accepted a path escaping GameRoot.'
Assert-True ((Read-TestFile (Join-Path $game 'd3d9.dll')) -eq 'diagnostic-client') 'Malformed journal caused partial restore.'
Write-Output "All deployment tests passed using fake directories only. Artifacts: $root"
