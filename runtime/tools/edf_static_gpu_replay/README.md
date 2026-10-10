# Controlled replay of one validated EDF static mesh

This tool submits the original five used vertex streams, raw UINT16 indices,
SM3 bytecode and shader constants from frames 755, 1182 and 1292. It uses the
original declaration, MRT formats, depth format, viewport and relevant render
and sampler states. Only the textures are synthetic: an opaque DXT5 checker at
s0 and constant DXT1 environment cubes at s1/s2. It does not recreate the game
scene, illumination or original texture pixels.

Each frame draws a changed POSITION stream as a negative control, then the
single allowed mesh. The Runtime profile must preserve the control draw and
accept only the unchanged mesh. The tool saves MRT0 BMPs before Present, checks
every HRESULT and writes `replay_report.json`. Pipeline success must be checked
separately in the Runtime logs for recognized, RasterGeometry, SceneManager,
InstanceManager, BLAS and TLAS stages.

Build both architectures without starting any application:

```powershell
Set-Location 'D:\EDF IA RTX REMIX\runtime'
.\tools\edf_static_gpu_replay\run.ps1 -Architecture x64
.\tools\edf_static_gpu_replay\run.ps1 -Architecture x86
```

Tests in the restricted Codex environment require elevated execution for MSVC
and a writable `TEMP`/`TMP`, such as `runtime\_build\test-temp`. The tool has no
shader compiler, USD, Vulkan SDK or third-party JSON dependency. Fixture export
uses Python standard-library code and the checked manifest.

Before running, copy the complete matching Runtime bundle beside the compiled
EXE in a fresh test directory. Set its experimental profile to this EXE name
and the SHA1 printed by the build script, enable RT and retain
`rtx.edfStatic.rasterOnly=true`. Shader compilation must be synchronous for this
bounded probe. The real EDF executable fingerprint is not changed. Run at most
90 frames, with the test directory as the working directory. The replay window
is hidden. No real game files are read or modified while executing the tool.

The bounded WSI composition runner, its current-frame pixel verification and
negative scenarios are documented in [COMPOSITION_README.md](COMPOSITION_README.md).
