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

$script:assertionCount = 0
function Assert-True([bool]$Condition, [string]$Message) {
  $script:assertionCount++
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
Write-TestFile $profile "rtx.edfStatic.composition.enabled = False`nrtx.edfStatic.probe.enabled = True`n"
Write-TestFile (Join-Path $game 'EDF-IA.exe') 'fixture-executable'
$expectedExecutable = (Get-FileHash -LiteralPath (Join-Path $game 'EDF-IA.exe') -Algorithm SHA1).Hash
$manifestFiles = @(Get-ChildItem -LiteralPath $bundle -Recurse -Force -File | ForEach-Object {
  [PSCustomObject]@{ path = $_.FullName.Substring($bundle.Length + 1).Replace('\', '/'); size = $_.Length;
    sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }
})
$manifestPath = Join-Path $bundle 'package-manifest.json'
Write-TestFile $manifestPath ([PSCustomObject]@{ schema = 1; architecture = 'x86-bridge-client + x64-server/runtime'; files = $manifestFiles } | ConvertTo-Json -Depth 8)
[void][IO.Directory]::CreateDirectory((Join-Path $game '.edf-remix-backup'))
Write-TestFile (Join-Path $game '.edf-remix-backup\active.json') 'legacy-journal-must-remain-byte-identical'


& $deploy -Action Install -GameRoot $game -BundleRoot $bundle -ProfilePath $profile -ExpectedExecutableSha1 $expectedExecutable | Out-Null
Assert-True ((Read-TestFile (Join-Path $game 'd3d9.dll')) -eq 'diagnostic-client') 'Client was not installed.'
& $deploy -Action Verify -GameRoot $game | Out-Null
& $deploy -Action Enable -GameRoot $game | Out-Null
Assert-True ((Read-TestFile (Join-Path $game 'rtx.conf')).Contains('composition.enabled = True')) 'Enable did not select controlled profile.'
& $deploy -Action Verify -GameRoot $game | Out-Null
& $deploy -Action Disable -GameRoot $game | Out-Null
Assert-True ((Read-TestFile (Join-Path $game 'rtx.conf')) -eq (Read-TestFile $profile)) 'Disable changed delivered profile bytes.'
Assert-True ((Read-TestFile (Join-Path $game '.edf-remix-backup\active.json')) -eq 'legacy-journal-must-remain-byte-identical') 'Legacy backup namespace was modified.'

Assert-True ((Read-TestFile (Join-Path $game 'rtx.conf')) -eq (Read-TestFile $profile)) 'Profile was not installed.'
Assert-True ((Read-TestFile (Join-Path $game 'unrelated.txt')) -eq 'keep-this-file') 'Unrelated file was changed.'
$active = Get-Content -LiteralPath (Join-Path $game '.edf-composition-backup\active.json') -Raw | ConvertFrom-Json
$journalPath = Join-Path $game ('.edf-composition-backup\' + $active.session + '\journal.json')
$journal = Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json
Assert-True ($journal.entries.Count -eq 5 -and $journal.status -eq 'installed') 'Journal lacks installed files.'
Assert-True ((Read-TestFile (Join-Path $game ('.edf-composition-backup\' + $active.session + '\original\d3d9.dll'))) -eq 'original-client') 'Original client was not backed up.'

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
Assert-True (-not (Test-Path -LiteralPath (Join-Path $game '.edf-composition-backup\active.json'))) 'Restore kept an active deployment pointer.'
& $deploy -Action Restore -GameRoot $game | Out-Null

& $deploy -Action Install -GameRoot $game -BundleRoot $bundle -ProfilePath $profile -ExpectedExecutableSha1 $expectedExecutable | Out-Null
$active = Get-Content -LiteralPath (Join-Path $game '.edf-composition-backup\active.json') -Raw | ConvertFrom-Json
$journalPath = Join-Path $game ('.edf-composition-backup\' + $active.session + '\journal.json')
$journal = Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json
$clientBackup = Join-Path $game ('.edf-composition-backup\' + $active.session + '\original\d3d9.dll')
[IO.File]::Copy($clientBackup, (Join-Path $game 'd3d9.dll'), $true)
[IO.File]::Delete((Join-Path $game '.trex\d3d9.dll'))
$journal.status = 'restoring'
Write-TestFile $journalPath ($journal | ConvertTo-Json -Depth 12)
& $deploy -Action Restore -GameRoot $game | Out-Null
Assert-True ((Read-TestFile (Join-Path $game 'd3d9.dll')) -eq 'original-client') 'Resuming Restore changed an already restored original.'
Assert-True ((Read-TestFile (Join-Path $game 'rtx.conf')) -eq 'original-configuration') 'Resuming Restore did not finish remaining originals.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $game '.trex\NvRemixBridge.exe'))) 'Resuming Restore did not remove remaining installed files.'

& $deploy -Action Install -GameRoot $game -BundleRoot $bundle -ProfilePath $profile -ExpectedExecutableSha1 $expectedExecutable | Out-Null
$active = Get-Content -LiteralPath (Join-Path $game '.edf-composition-backup\active.json') -Raw | ConvertFrom-Json
$journalPath = Join-Path $game ('.edf-composition-backup\' + $active.session + '\journal.json')
$journal = Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json
$journal.entries[0].relativePath = '..\outside.txt'
Write-TestFile $journalPath ($journal | ConvertTo-Json -Depth 12)
$refused = $false
try { & $deploy -Action Restore -GameRoot $game | Out-Null } catch { $refused = $true }
Assert-True $refused 'Restore accepted a path escaping GameRoot.'
Assert-True ((Read-TestFile (Join-Path $game 'd3d9.dll')) -eq 'diagnostic-client') 'Malformed journal caused partial restore.'
Assert-True ((Read-TestFile (Join-Path $game '.edf-remix-backup\active.json')) -eq 'legacy-journal-must-remain-byte-identical') 'Legacy namespace changed after restore operations.'
Write-Output "All isolated composition deployment tests passed using fake directories only. Artifacts: $root"

$gameTwo = Join-Path $root 'fake-game-two'
[void][IO.Directory]::CreateDirectory($gameTwo)
Write-TestFile (Join-Path $gameTwo 'EDF-IA.exe') 'fixture-executable'
$refused = $false
try { & $deploy -Action Install -GameRoot $gameTwo -BundleRoot $bundle -ProfilePath $profile | Out-Null } catch { $refused = $true }
Assert-True $refused 'Install accepted a different executable fingerprint.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $gameTwo 'd3d9.dll'))) 'Fingerprint rejection mutated game files.'
Write-TestFile (Join-Path $bundle '.trex\d3d9.dll') 'tampered-package'
$refused = $false
try { & $deploy -Action Install -GameRoot $gameTwo -BundleRoot $bundle -ProfilePath $profile -ExpectedExecutableSha1 $expectedExecutable | Out-Null } catch { $refused = $true }
Assert-True $refused 'Install accepted a corrupted package.'
Assert-True (-not (Test-Path -LiteralPath (Join-Path $gameTwo 'd3d9.dll'))) 'Manifest rejection mutated game files.'
Write-TestFile (Join-Path $bundle '.trex\d3d9.dll') 'diagnostic-server'
& $deploy -Action Install -GameRoot $gameTwo -BundleRoot $bundle -ProfilePath $profile -ExpectedExecutableSha1 $expectedExecutable | Out-Null
& $deploy -Action Enable -GameRoot $gameTwo | Out-Null
& $deploy -Action Restore -GameRoot $gameTwo | Out-Null
Assert-True (-not (Test-Path -LiteralPath (Join-Path $gameTwo 'rtx.conf'))) 'Restoring enabled controlled profile failed.'
Write-Output 'Fingerprint, package corruption, controlled profile restoration and namespace isolation checks passed.'

$gameThree = Join-Path $root 'fake-game-three'
[void][IO.Directory]::CreateDirectory($gameThree)
Write-TestFile (Join-Path $gameThree 'EDF-IA.exe') 'fixture-executable'
Write-TestFile (Join-Path $gameThree 'd3d9.dll') 'game-three-original-client'
& $deploy -Action Install -GameRoot $gameThree -BundleRoot $bundle -ProfilePath $profile -ExpectedExecutableSha1 $expectedExecutable | Out-Null
$activeThree = Get-Content -LiteralPath (Join-Path $gameThree '.edf-composition-backup\active.json') -Raw | ConvertFrom-Json
$originalThree = Join-Path $gameThree ('.edf-composition-backup\' + $activeThree.session + '\original\d3d9.dll')
Write-TestFile $originalThree 'tampered-original-backup'
$refused = $false
try { & $deploy -Action Restore -GameRoot $gameThree | Out-Null } catch { $refused = $true }
Assert-True $refused 'Restore accepted a changed original backup.'
Assert-True ((Read-TestFile (Join-Path $gameThree 'd3d9.dll')) -eq 'diagnostic-client') 'Backup rejection partially restored target files.'
Write-Output 'Corrupted backup refusal passed before any restoration mutation.'

$gameFour = Join-Path $root 'fake-game-four'
[void][IO.Directory]::CreateDirectory($gameFour)
Write-TestFile (Join-Path $gameFour 'EDF-IA.exe') 'fixture-executable'
$profileCRLF = Join-Path $root 'profile-crlf.conf'
Write-TestFile $profileCRLF ((Read-TestFile $profile).Replace("`n", "`r`n"))
& $deploy -Action Install -GameRoot $gameFour -BundleRoot $bundle -ProfilePath $profileCRLF -ExpectedExecutableSha1 $expectedExecutable | Out-Null
$enableResponse = & $deploy -Action Enable -GameRoot $gameFour
Assert-True ($enableResponse -match 'Enabled: True') 'CRLF-enabled profile was reported as disabled.'
& $deploy -Action Disable -GameRoot $gameFour | Out-Null
Assert-True ((Read-TestFile (Join-Path $gameFour 'rtx.conf')) -eq (Read-TestFile $profileCRLF)) 'CRLF disabled profile bytes were changed.'
& $deploy -Action Restore -GameRoot $gameFour | Out-Null
Write-TestFile (Join-Path $root 'deployment_verification.json') ([PSCustomObject]@{
  schema = 1; passed = $true; assertionCount = $script:assertionCount;
  fixtureRoot = $root; activeInstallationTouched = $false; backupNamespace = '.edf-composition-backup';
  legacyNamespacePreserved = $true; controlledProfileCRLFValidated = $true
} | ConvertTo-Json -Depth 8)
Write-Output "Deployment verification passed: $script:assertionCount assertions. Report: $(Join-Path $root 'deployment_verification.json')"
