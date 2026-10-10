# Restoring the checkpoint

1. Clone the private repository. Verify and extract the three release ZIPs in its root. They preserve original relative paths: `captures/`, `runtime/_build/`, and `packages/edf-composition-preview/edf-diagnostics-x86/`.
2. The custom-binary ZIP intentionally includes only our matching compiled renderer and Bridge, plus the package manifest. It is **not a complete installable dependency bundle**. Restore other dependencies from the matching official RTX Remix distribution/build workflow, retain their licenses, and verify every entry in the saved package manifest before using `deploy.ps1`. Missing entries must fail verification; do not bypass it.
3. Build tools were Python 3.12+, Meson, MSVC VS 17.14.41 in `D:\EDFBuildTools`, and Vulkan SDK `C:\VulkanSDK\1.4.363.0`. They are not backed up. Read `runtime/.agents/skills/build-remix/SKILL.md` and upstream build documentation.
4. `runtime/` is a full source snapshot, not a nested upstream Git checkout. For an independent build checkout, clone official dxvk-remix into a **new** directory, check out the base commit from `upstream/provenance.json`, initialize its submodules, apply `upstream/modifications.patch`, and copy the new/untracked files from the snapshot into that checkout. Preserve the `.git` and submodule metadata of that fresh checkout. Compare source hashes against the saved manifest and final package source hashes.
5. Use `meson compile`, never direct ninja. A rebuild produces a different binary SHA256; do not pretend it is the recorded tested DLL. Repeat bounded native verification and update deployment pins only with new evidence.
6. Tools and evidence retain paths from the original workspace. Easiest recovery is `D:\EDF IA RTX REMIX`. On a different path, adapt configurations/tool path handling explicitly, preserve original evidence unchanged, and generate fresh validation reports rather than changing old hashes.

To run the CPU suites after restoring Python dependencies:

```powershell
Set-Location -LiteralPath 'D:\EDF IA RTX REMIX'
& '.tools\build-python\Scripts\python.exe' -B -m unittest discover -s 'runtime\tools\edf_static_gpu_replay' -p 'test_*.py'
& '.tools\build-python\Scripts\python.exe' -B 'runtime\tools\edf_composition\tests\test_source_contract.py'
```

These commands are historical workspace paths; `.tools` itself is excluded and must be recreated. Native fixtures must be rebuilt before callback tests or GPU matrix execution. Keep the real game untouched while restoring/checking the checkpoint.
The fourth archive, `edf-replay-fixtures.zip`, preserves both native replay executables used for controlled GPU tests. Extract it at the repository root. These are test harnesses, not game executables or runtime dependencies.

