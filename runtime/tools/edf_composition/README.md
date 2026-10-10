# EDF composition deployment

`deploy.ps1` provides Install, Verify, Enable, Disable and Restore. The default profile disables visual composition; Enable selects the precomputed SHA256-approved profile variant before launching the game. All writes stay inside the explicitly supplied GameRoot. The game fingerprint and every packaged file are checked before installation. Running EDF/Bridge processes reject mutations.

Backups use `.edf-composition-backup`, independently of the existing `.edf-remix-backup`. Existing installations are stacked as originals, and Restore returns their exact bytes without restoring or modifying the legacy journal. Restore rejects unrecognised file edits. Toggle actions modify only the managed `rtx.conf` between its two audited variants.

Manual commands and test criteria: [TESTAR.md](<D:/EDF IA RTX REMIX/profiles/edf-composition-preview/TESTAR.md>).

Fixture tests:

```powershell
& 'D:\EDF IA RTX REMIX\runtime\tools\edf_composition\tests\test_deploy.ps1' -OutputDirectory 'D:\EDF IA RTX REMIX\runtime\_build\edf_composition_deployment_tests'
```

The fixtures are artificial files inside the workspace; they do not touch the active installation. Windows sandbox environments can deny File.Replace even inside the writable workspace; an unsandboxed run of this fixture-only command may be required. No fixture bypass is enabled by default: tests explicitly supply the SHA1 of their artificial executable using ExpectedExecutableSha1.
