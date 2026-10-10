# Install and restore the diagnostic bundle

These commands prepare a reversible diagnostic installation. They do not launch EDF. The default profile keeps the original raster renderer and enables bounded capture through F10. Use the completed x86 diagnostic bundle, including its `.trex` server/runtime tree.

From the repository directory, install:

```powershell
./tools/edf_capture/deploy.ps1 -Action Install -GameRoot 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon'
```

The default bundle is `D:\EDF IA RTX REMIX\packages\edf-diagnostics-x86`. Override it with `-BundleRoot`. Override the supplied `edf-observe.conf` with `-ProfilePath` if needed. The script installs only the client `d3d9.dll`, files under the bundle's `.trex`, and the selected profile as `rtx.conf`.

Restore:

```powershell
./tools/edf_capture/deploy.ps1 -Action Restore -GameRoot 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon'
```

Original files are copied to `.edf-remix-backup/<session>/original` before replacement. `journal.json` records target paths, original SHA256 hashes, installed SHA256 hashes, and newly created directories. The backup history remains after restoring.

Restore first verifies every journal target and original backup. A changed or missing installed file causes it to refuse the operation before modifying anything. Restore never overwrites user edits. Keep the active journal and originals available. An interrupted install or restore can be retried using the same Restore command; known original and installed hashes distinguish untouched files from completed steps.

Files created by the installer are removed individually when their current hashes match. Directories are removed only when the journal says they were newly created and they are empty. Unrelated files and existing directories remain. Paths escaping GameRoot, reparse points, mismatched roots, and malformed journals are rejected. There is no recursive deletion.

Run the synthetic deployment tests without touching the game:

```powershell
./tools/edf_capture/tests/test_deploy.ps1
```

The tests use fake game and bundle directories under `_build/edf_deployment_tests`. They verify existing DLL/config backups, install/restore, resuming an interrupted restore, unrelated-file preservation, refusal of user changes, whole-operation preflight, and rejection of a modified journal that tries to escape GameRoot.
