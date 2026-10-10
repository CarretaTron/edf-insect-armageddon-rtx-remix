param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('Install', 'Verify', 'Enable', 'Disable', 'Restore')]
  [string]$Action,
  [Parameter(Mandatory = $true)]
  [ValidateNotNullOrEmpty()]
  [string]$GameRoot,
  [string]$BundleRoot = 'D:\EDF IA RTX REMIX\packages\edf-composition-preview\edf-diagnostics-x86',
  [string]$ProfilePath = '',
  [ValidatePattern('^[0-9a-fA-F]{40}$')]
  [string]$ExpectedExecutableSha1 = 'd2e90641c560420d279a629ddd86e13cb767f464'
)

$ErrorActionPreference = 'Stop'
$pathComparer = [StringComparer]::OrdinalIgnoreCase

function Get-DirectoryRoot([string]$Path) {
  $full = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
  foreach ($segment in $full.Substring([IO.Path]::GetPathRoot($full).Length).Replace('/', '\').Split('\')) {
    if ($segment -and $segment.TrimEnd(' ', '.') -ne $segment) {
      throw 'Deployment roots cannot contain trailing spaces or dots.'
    }
  }
  if (-not (Test-Path -LiteralPath $full -PathType Container)) {
    throw "Directory does not exist: $full"
  }
  if ($full -eq [IO.Path]::GetPathRoot($full).TrimEnd('\', '/')) {
    throw 'A filesystem drive root cannot be a deployment directory.'
  }
  $ancestor = $full
  while ($ancestor) {
    if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
      throw "Reparse points are not supported as deployment root ancestors: $ancestor"
    }
    $parent = [IO.Path]::GetDirectoryName($ancestor)
    if (-not $parent -or $pathComparer.Equals($parent, $ancestor)) { break }
    $ancestor = $parent
  }
  return $full
}

function Get-SafePath([string]$Root, [string]$RelativePath) {
  if (-not $RelativePath -or [IO.Path]::IsPathRooted($RelativePath) -or $RelativePath.Contains(':')) {
    throw "Invalid relative deployment path: $RelativePath"
  }
  $relative = $RelativePath.Replace('/', '\')
  foreach ($segment in $relative.Split('\')) {
    if (-not $segment -or $segment -eq '.' -or $segment -eq '..' -or
      $segment.TrimEnd(' ', '.') -ne $segment -or
      $segment -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)' -or
      $segment.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0) {
      throw "Invalid relative deployment path: $RelativePath"
    }
  }
  $full = [IO.Path]::GetFullPath([IO.Path]::Combine($Root, $relative))
  if (-not $full.StartsWith($Root + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "Deployment path escaped the requested directory: $RelativePath"
  }
  $cursor = $full
  while (-not $pathComparer.Equals($cursor, $Root)) {
    if (Test-Path -LiteralPath $cursor) {
      if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Deployment refuses a reparse point: $cursor"
      }
    }
    $parent = [IO.Path]::GetDirectoryName($cursor)
    if (-not $parent -or $pathComparer.Equals($parent, $cursor)) {
      throw 'Could not verify deployment path ancestors.'
    }
    $cursor = $parent
  }
  return $full
}

function Get-Hash([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return $null
  }
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Assert-GamePath([string]$Path) {
  $full = [IO.Path]::GetFullPath($Path)
  if (-not $full.StartsWith($GameRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw "File operation escaped GameRoot: $full"
  }
  return Get-SafePath $GameRoot $full.Substring($GameRoot.Length + 1)
}

function Test-ManagedPath([string]$RelativePath) {
  return $pathComparer.Equals($RelativePath, 'd3d9.dll') -or
    $pathComparer.Equals($RelativePath, 'rtx.conf') -or
    $RelativePath.StartsWith('.trex\', [StringComparison]::OrdinalIgnoreCase)
}

function Save-Json([string]$Path, $Value) {
  $Path = Assert-GamePath $Path
  $temporary = Assert-GamePath ($Path + '.tmp')
  [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 12) + "`n", [Text.UTF8Encoding]::new($false))
  if (Test-Path -LiteralPath $Path -PathType Leaf) {
    [IO.File]::Replace($temporary, $Path, [System.Management.Automation.Language.NullString]::Value)
  } else {
    [IO.File]::Move($temporary, $Path)
  }
}

function Ensure-Parent([string]$Path) {
  $parent = Assert-GamePath ([IO.Path]::GetDirectoryName($Path))
  [void][IO.Directory]::CreateDirectory($parent)
}

function Replace-File([string]$Source, [string]$Target) {
  $Source = Assert-GamePath $Source
  $Target = Assert-GamePath $Target
  if (Test-Path -LiteralPath $Target -PathType Leaf) {
    [IO.File]::Replace($Source, $Target, [System.Management.Automation.Language.NullString]::Value)
  } else {
    [IO.File]::Move($Source, $Target)
  }
}

$GameRoot = Get-DirectoryRoot $GameRoot
if ($Action -in @('Install', 'Enable', 'Disable', 'Restore') -and
    (Get-Process -Name 'EDF-IA', 'NvRemixBridge' -ErrorAction SilentlyContinue)) {
  throw 'Close EDF-IA.exe and NvRemixBridge.exe before changing the installation.'
}
if ($Action -eq 'Install') {
  $executable = Get-SafePath $GameRoot 'EDF-IA.exe'
  if (-not (Test-Path -LiteralPath $executable -PathType Leaf) -or
      (Get-FileHash -LiteralPath $executable -Algorithm SHA1).Hash -ne $ExpectedExecutableSha1) {
    throw 'EDF executable fingerprint does not match the validated version.'
  }
}
$backupRelative = '.edf-composition-backup'
$activePath = Get-SafePath $GameRoot ($backupRelative + '\active.json')

if ($Action -eq 'Install') {
  if (Test-Path -LiteralPath $activePath) {
    throw 'An active deployment already exists. Restore it before installing another bundle.'
  }
  $BundleRoot = Get-DirectoryRoot $BundleRoot
  if ($BundleRoot.StartsWith($GameRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $GameRoot.StartsWith($BundleRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $pathComparer.Equals($BundleRoot, $GameRoot)) {
    throw 'BundleRoot and GameRoot must be separate directory trees.'
  }
  if (-not $ProfilePath) { $ProfilePath = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..\profiles\edf-composition-preview\rtx.conf')) }
  $ProfilePath = [IO.Path]::GetFullPath($ProfilePath)
  if (-not (Test-Path -LiteralPath $ProfilePath -PathType Leaf) -or
    $ProfilePath.StartsWith($GameRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'ProfilePath must be an existing file outside GameRoot.'
  }
  $client = Get-SafePath $BundleRoot 'd3d9.dll'
  $serverRoot = Get-SafePath $BundleRoot '.trex'
  if (-not (Test-Path -LiteralPath $client -PathType Leaf) -or
    -not (Test-Path -LiteralPath $serverRoot -PathType Container)) {
    throw 'BundleRoot must contain the x86 client d3d9.dll and its .trex server/runtime directory.'
  }
  $sources = @([PSCustomObject]@{ relativePath = 'd3d9.dll'; source = $client })
  foreach ($file in Get-ChildItem -LiteralPath $serverRoot -Recurse -Force) {
    if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) {
      throw "Bundle contains a reparse point: $($file.FullName)"
    }
    if (-not $file.PSIsContainer) {
      $relative = '.trex\' + $file.FullName.Substring($serverRoot.Length + 1)
      [void](Get-SafePath $BundleRoot $relative)
      $sources += [PSCustomObject]@{ relativePath = $relative; source = $file.FullName }
    }
  }
  if ($sources.Count -lt 2) { throw 'The .trex runtime directory has no files.' }
  $manifestFile = Get-SafePath $BundleRoot 'package-manifest.json'
  if (-not (Test-Path -LiteralPath $manifestFile -PathType Leaf)) { throw 'Package manifest is missing.' }
  $manifest = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
  if ($manifest.schema -ne 1 -or $manifest.architecture -ne 'x86-bridge-client + x64-server/runtime') {
    throw 'This experiment requires the x86 Bridge client and x64 Runtime package.'
  }
  $manifestPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach ($file in $manifest.files) {
    $relative = ([string]$file.path).Replace('/', '\')
    $source = Get-SafePath $BundleRoot $relative
    if (-not (Test-ManagedPath $relative) -or $relative -eq 'rtx.conf' -or
        -not $manifestPaths.Add($relative) -or $file.sha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        -not (Test-Path -LiteralPath $source -PathType Leaf) -or
        (Get-Item -LiteralPath $source).Length -ne $file.size -or (Get-Hash $source) -ne $file.sha256) {
      throw "Package file is missing, unsafe, duplicated or differs from the manifest: $relative"
    }
  }
  if ($manifestPaths.Count -ne $sources.Count -or
      -not $manifestPaths.Contains('d3d9.dll') -or -not $manifestPaths.Contains('.trex\d3d9.dll') -or
      -not $manifestPaths.Contains('.trex\NvRemixBridge.exe')) { throw 'The package file set is incomplete.' }
  foreach ($source in $sources) {
    if (-not $manifestPaths.Contains($source.relativePath)) { throw 'Unlisted file exists inside the package.' }
  }
  $profileText = [IO.File]::ReadAllText($ProfilePath)
  $option = [regex]::new('(?mi)^(rtx\.edfStatic\.composition\.enabled[ \t]*=[ \t]*)False([ \t]*\r?)$')
  if ($option.Matches($profileText).Count -ne 1 -or
      [regex]::Matches($profileText, '(?mi)^rtx\.edfStatic\.composition\.enabled[ \t]*=').Count -ne 1) {
    throw 'The delivered profile must contain exactly one composition.enabled=False entry.'
  }
  $enabledProfileText = $option.Replace($profileText, '${1}True${2}')
  $sources += [PSCustomObject]@{ relativePath = 'rtx.conf'; source = $ProfilePath }
  $session = (Get-Date -Format 'yyyyMMddTHHmmssfff') + '-' + [Guid]::NewGuid().ToString('N')
  $sessionRelative = $backupRelative + '\' + $session
  $journalPath = Get-SafePath $GameRoot ($sessionRelative + '\journal.json')
  $entries = @()
  $createdDirectories = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach ($source in $sources) {
    $target = Get-SafePath $GameRoot $source.relativePath
    if (Test-Path -LiteralPath $target -PathType Container) {
      throw "A deployment file target is an existing directory: $target"
    }
    $originalHash = Get-Hash $target
    $entries += [PSCustomObject]@{
      relativePath = $source.relativePath
      installedSha256 = Get-Hash $source.source
      originalExists = [bool]$originalHash
      originalSha256 = $originalHash
      backupRelativePath = 'original\' + $source.relativePath
    }
    $parent = [IO.Path]::GetDirectoryName($target)
    while (-not $pathComparer.Equals($parent, $GameRoot)) {
      if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void]$createdDirectories.Add($parent.Substring($GameRoot.Length + 1))
      }
      $parent = [IO.Path]::GetDirectoryName($parent)
    }
  }
  Ensure-Parent $journalPath
  foreach ($entry in $entries) {
    if ($entry.originalExists) {
      $target = Get-SafePath $GameRoot $entry.relativePath
      $backup = Get-SafePath $GameRoot ($sessionRelative + '\' + $entry.backupRelativePath)
      Ensure-Parent $backup
      [IO.File]::Copy($target, $backup, $false)
      if (-not $pathComparer.Equals((Get-Hash $backup), $entry.originalSha256)) {
        throw "Original file changed while backing it up: $($entry.relativePath)"
      }
    }
  }
  $disabledProfile = Get-SafePath $GameRoot ($sessionRelative + '\profiles\disabled.conf')
  $enabledProfile = Get-SafePath $GameRoot ($sessionRelative + '\profiles\enabled.conf')
  Ensure-Parent $disabledProfile
  [IO.File]::Copy($ProfilePath, $disabledProfile, $false)
  [IO.File]::WriteAllText($enabledProfile, $enabledProfileText, [Text.UTF8Encoding]::new($false))
  $profileEntry = $entries | Where-Object { $_.relativePath -eq 'rtx.conf' }
  $profileEntry | Add-Member -NotePropertyName allowedInstalledSha256 -NotePropertyValue @((Get-Hash $disabledProfile), (Get-Hash $enabledProfile))
  $journal = [PSCustomObject]@{
    schema = 1
    hashAlgorithm = 'SHA256'
    gameRoot = $GameRoot
    session = $session
    status = 'installing'
    entries = $entries
    createdDirectories = @($createdDirectories)
  }
  Save-Json $journalPath $journal
  Save-Json $activePath ([PSCustomObject]@{ schema = 1; session = $session })
  try {
    for ($index = 0; $index -lt $entries.Count; ++$index) {
      $entry = $entries[$index]
      $target = Get-SafePath $GameRoot $entry.relativePath
      $pending = Get-SafePath $GameRoot ($sessionRelative + '\pending\' + $entry.relativePath)
      Ensure-Parent $pending
      [IO.File]::Copy($sources[$index].source, $pending, $false)
      if (-not $pathComparer.Equals((Get-Hash $pending), $entry.installedSha256)) {
        throw "Bundle source changed while staging: $($entry.relativePath)"
      }
      if (-not $pathComparer.Equals((Get-Hash $target), $entry.originalSha256)) {
        throw "Target changed after backup: $($entry.relativePath)"
      }
      if ([IO.Path]::GetDirectoryName($target) -ne $GameRoot) { Ensure-Parent $target }
      Replace-File $pending $target
    }
    $journal.status = 'installed'
    Save-Json $journalPath $journal
    Write-Output "Composition preview installed with composition disabled. Backup journal: $journalPath"
  } catch {
    throw "Installation stopped: $($_.Exception.Message) Run Restore against this GameRoot to undo recorded changes. Journal: $journalPath"
  }
  return
}

if (-not (Test-Path -LiteralPath $activePath -PathType Leaf)) {
  if ($Action -ne 'Restore') { throw 'No active composition deployment exists.' }
  Write-Output 'No active composition deployment to restore.'
  return
}
$active = Get-Content -LiteralPath $activePath -Raw | ConvertFrom-Json
if ($active.schema -ne 1 -or $active.session -notmatch '^[0-9]{8}T[0-9]{9}-[0-9a-f]{32}$') {
  throw 'Invalid active deployment pointer.'
}
$sessionRelative = $backupRelative + '\' + $active.session
$journalPath = Get-SafePath $GameRoot ($sessionRelative + '\journal.json')
$journal = Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json
if ($journal.schema -ne 1 -or $journal.hashAlgorithm -ne 'SHA256' -or
  -not $pathComparer.Equals([string]$journal.gameRoot, $GameRoot) -or
  $journal.session -ne $active.session -or
  $journal.status -notin @('installing', 'installed', 'restoring', 'restored')) {
  throw 'Deployment journal does not match this GameRoot or has an unsupported schema.'
}
$seenPaths = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$restorePlan = @()
foreach ($entry in $journal.entries) {
  $relative = ([string]$entry.relativePath).Replace('/', '\')
  $target = Get-SafePath $GameRoot $relative
  if (Test-Path -LiteralPath $target -PathType Container) {
    throw "Restore refused: a journal file target became a directory: $relative"
  }
  if (-not (Test-ManagedPath $relative) -or -not $seenPaths.Add($relative) -or
    $entry.installedSha256 -notmatch '^[0-9a-fA-F]{64}$' -or
    $entry.originalExists -isnot [bool] -or
    ($entry.originalExists -and $entry.originalSha256 -notmatch '^[0-9a-fA-F]{64}$') -or
    -not $pathComparer.Equals([string]$entry.backupRelativePath, 'original\' + $relative)) {
    throw "Invalid deployment journal entry: $relative"
  }
  $backup = Get-SafePath $GameRoot ($sessionRelative + '\original\' + $relative)
  if ($entry.originalExists -and -not $pathComparer.Equals((Get-Hash $backup), $entry.originalSha256)) {
    throw "Original backup is missing or changed: $relative"
  }
  $currentHash = Get-Hash $target
  $originalHash = if ($entry.originalExists) { [string]$entry.originalSha256 } else { $null }
  $alreadyOriginal = $pathComparer.Equals($currentHash, $originalHash)
  $allowedHashes = @([string]$entry.installedSha256)
  if ($relative -eq 'rtx.conf') {
    if (@($entry.allowedInstalledSha256).Count -ne 2 -or
        @($entry.allowedInstalledSha256 | Where-Object { $_ -notmatch '^[0-9a-fA-F]{64}$' }).Count) {
      throw 'Invalid controlled profile hashes in composition journal.'
    }
    $allowedHashes += @($entry.allowedInstalledSha256)
  }
  $matchesInstalled = $false
  foreach ($allowedHash in $allowedHashes) {
    $matchesInstalled = $matchesInstalled -or $pathComparer.Equals($currentHash, $allowedHash)
  }
  if ($journal.status -eq 'installed' -and -not $matchesInstalled) {
    throw "Restore refused: installed file is missing or changed by the user: $relative"
  }
  if ($journal.status -ne 'installed' -and -not ($alreadyOriginal -or $matchesInstalled)) {
    throw "Restore refused: a partially deployed file has unknown user changes: $relative"
  }
  $restorePlan += [PSCustomObject]@{
    entry = $entry; target = $target; backup = $backup
    currentHash = $currentHash; restore = -not $alreadyOriginal
  }
}
foreach ($relative in $journal.createdDirectories) {
  $directory = Get-SafePath $GameRoot ([string]$relative)
  if (-not ($pathComparer.Equals([string]$relative, '.trex') -or
    ([string]$relative).StartsWith('.trex\', [StringComparison]::OrdinalIgnoreCase))) {
    throw 'Journal contains an unmanaged created directory.'
  }
}

if ($Action -in @('Verify', 'Enable', 'Disable')) {
  if ($journal.status -ne 'installed') { throw 'The deployment must be fully installed before this action.' }
  if ($Action -ne 'Verify') {
    $variant = if ($Action -eq 'Enable') { 'enabled' } else { 'disabled' }
    $source = Get-SafePath $GameRoot ($sessionRelative + '\profiles\' + $variant + '.conf')
    $profileEntry = $journal.entries | Where-Object { $_.relativePath -eq 'rtx.conf' }
    $expectedHash = if ($Action -eq 'Enable') { $profileEntry.allowedInstalledSha256[1] } else { $profileEntry.allowedInstalledSha256[0] }
    if ((Get-Hash $source) -ne $expectedHash) { throw 'The controlled profile variant backup has changed.' }
    $target = Get-SafePath $GameRoot 'rtx.conf'
    $pending = Get-SafePath $GameRoot ($sessionRelative + '\toggle-' + [guid]::NewGuid().ToString('N') + '.conf')
    [IO.File]::Copy($source, $pending, $false)
    if ((Get-Hash $pending) -ne $expectedHash) { throw 'Profile variant changed during staging.' }
    $profilePlan = $restorePlan | Where-Object { $_.entry.relativePath -eq 'rtx.conf' }
    if ((Get-Hash $target) -ne $profilePlan.currentHash) { throw 'Profile changed after verification.' }
    Replace-File $pending $target
  }
  $profileText = [IO.File]::ReadAllText((Get-SafePath $GameRoot 'rtx.conf'))
  $isEnabled = [regex]::IsMatch($profileText, '(?mi)^rtx\.edfStatic\.composition\.enabled[ \t]*=[ \t]*True[ \t]*\r?$')
  Write-Output "Composition deployment verified. Enabled: $isEnabled. Journal: $journalPath"
  return
}

$journal.status = 'restoring'
Save-Json $journalPath $journal
foreach ($plan in $restorePlan) {
  if (-not $plan.restore) { continue }
  $entry = $plan.entry
  $target = Get-SafePath $GameRoot ([string]$entry.relativePath)
  if (-not $pathComparer.Equals((Get-Hash $target), $plan.currentHash)) {
    throw "Restore stopped because a file changed after preflight: $($entry.relativePath)"
  }
  if ($entry.originalExists) {
    $pending = Get-SafePath $GameRoot ($sessionRelative + '\restoring\' + $entry.relativePath)
    Ensure-Parent $pending
    [IO.File]::Copy($plan.backup, $pending, $true)
    if (-not $pathComparer.Equals((Get-Hash $pending), $entry.originalSha256)) {
      throw "Original backup changed during restore: $($entry.relativePath)"
    }
    if (-not $pathComparer.Equals((Get-Hash $target), $plan.currentHash)) {
      throw "Restore stopped because a file changed before replacement: $($entry.relativePath)"
    }
    Replace-File $pending $target
  } else {
    [IO.File]::Delete($target)
  }
}
foreach ($relative in ($journal.createdDirectories | Sort-Object { $_.Length } -Descending)) {
  $directory = Get-SafePath $GameRoot ([string]$relative)
  if ((Test-Path -LiteralPath $directory -PathType Container) -and
    -not (Get-ChildItem -LiteralPath $directory -Force | Select-Object -First 1)) {
    [IO.Directory]::Delete($directory, $false)
  }
}
$journal.status = 'restored'
Save-Json $journalPath $journal
$activeNow = Get-Content -LiteralPath $activePath -Raw | ConvertFrom-Json
if ($activeNow.session -ne $active.session) { throw 'Active deployment pointer changed during restore.' }
[void](Get-SafePath $GameRoot ($backupRelative + '\active.json'))
[IO.File]::Delete($activePath)
Write-Output "Composition preview restored to the immediately preceding installation. Backup journal preserved: $journalPath"
