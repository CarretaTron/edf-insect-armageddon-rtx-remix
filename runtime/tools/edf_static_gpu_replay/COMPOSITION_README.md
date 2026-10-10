# Bounded WSI composition fixtures

The fixture uses a hidden, borderless `WS_POPUP` whose client area must be
exactly 1920×1080. The process is DPI aware. Nothing is displayed on the desktop
by this fixture; exported WSI pixels and submission logs do not prove scanout
or what a player saw in EDF.

`run_composition_matrix.py` copies an unexecuted matching package into fresh
workspace directories and measures the replay executable SHA1 and renderer
SHA256. It leaves the game installation untouched. Without `--run`, it only
prepares fixtures. With `--run`, it executes selected cases serially and stops
at the first failure. Do not run two native GPU experiments concurrently.

The test profile uses `autorunStartFrame=1`, one probe frame and four raster
frames. Frame zero creates the swapchain before the automatic request. Only the
fixture profile enables autorun: **never install its `rtx.conf` into EDF**.
After the final Present, the fixture waits for a D3D9 EVENT query and then for
the Runtime's recorded probe/composition callbacks to publish matching
frame/request completion records. Each phase has a 30-second deadline;
readback, device or validation failures reject the run. This prevents process
shutdown from truncating CPU file writes after the GPU has already finished.
The `--audit-callback-log FILE` mode exercises only this log parser; it returns
before loading D3D9 or creating a window/device and is not GPU evidence.

Choose a new output path for every attempt:

```powershell
Set-Location 'D:\EDF IA RTX REMIX'
$saida = '.\runtime\_build\edf_comp_x64_' + [guid]::NewGuid().ToString('N')
& '.\.tools\build-python\Scripts\python.exe' `
  '.\runtime\tools\edf_static_gpu_replay\run_composition_matrix.py' `
  --bundle '.\packages\edf-composition-preview\edf-diagnostics-x64' `
  --architecture x64 --output $saida --case positive_camera0 --run
if ($LASTEXITCODE) { throw 'A validação nativa falhou; consulte matrix_verification.json e os logs.' }
```

Replace both architecture and bundle suffix with `x86` to exercise the Bridge.
To prepare without executing, omit `--run`. For a matrix, repeat `--case`:

For a separate synchronization-validation attempt, add
`--validation-layer --validation-sdk 'C:\VulkanSDK\1.4.363.0'` and choose another
fresh output. This enables the Runtime debug callback and the installed
Khronos synchronization validator only in the replay child. The child receives
an explicit `validate_sync=true` settings file, plus the SDK-advertised
synchronization feature; GPU-assisted validation and best-practices extensions
remain disabled. SDK DLL, SDK manifest and settings hashes are recorded.
No global vkconfig or SDK file is modified. Approval requires the actual
enabled-layer log and the layer's synchronization-active status banner;
requested environment variables alone cannot prove validation ran. Any VUID
or synchronization hazard fails the case, including hazards outside this
experimental panel; such a failure requires review against a disabled baseline.

| Case | Required observation |
|---|---|
| `positive_camera0`, `positive_camera1`, `positive_camera2` | One current-frame panel at runtime frame 1, using the selected measured camera. |
| `disabled` | Offscreen probe completes, no panel command or composition sidecar. |
| `invalid_roi` | Out-of-bounds rectangle rejects before WSI writes. |
| `absent_initial`, `mutated_initial` | Request frame 1 has no valid mesh/current camera; frame 2 recovers and produces the sole panel. |
| `duplicate_initial` | Two recognized draws at frame 1 reject; frame 2 recovers. |
| `absent_after` | Frame 1 composes, frame 2 has no mesh or stale panel, frame 3 camera recovers. |
| `reset_after` | Frame 1 composes, Reset before frame 2 releases and recreates default-pool resources, later frames remain raster. |
| `nonidentity_gamma` | Nonidentity stored gamma ramp rejects. In windowed Runtime mode it does not change desktop hardware gamma. |
| `partial_present` | Partial COPY presentation rejects or explicitly discards the preview when the swapchain is recreated. |

`--source-camera 0..2` offsets the three measured cameras in `replay.cpp`.
`--skip-target-frame`, `--mutate-target-frame`, `--duplicate-target-frame` and
`--reset-before-frame` select a replay frame. `--presentation-pattern` adds
eight colored rectangles to the original backbuffer before Present. Fixtures
use synthetic textures, not gameplay captures or recovered EDF lighting.

Passing panel cases export `verified-images`: complete WSI before/after, the
GPU panel, backed-up ROI and changed-pixel map. Verification checks every RGBA
byte outside the ROI, exact panel copy, four original game color targets and an
independent CPU mask/crop/aspect/nearest/UNORM reference. It requires source
metadata approved by `verify_probe.py`, that metadata's SHA256 and the unchanged
renderer hash. Actual `presentation_queued` logs must match frame/present serial.
Device/submission/fence, SYNC-HAZARD, VUID and shader failures reject approval
even if a callback emitted `gpuCompleted=true`.

For a real EDF session, keep the matching renderer installed and the original
Runtime log available until verification finishes. Read the active log directly;
the existing probe verifier locates renderer provenance relative to it:

```powershell
Set-Location 'D:\EDF IA RTX REMIX'
$jogo = 'D:\SteamLibrary\steamapps\common\Earth Defense Force Insect Armageddon'
$sessao = 'D:\EDF IA RTX REMIX\captures\edf-composition\session_EXATA'
$log = Join-Path $jogo 'rtx-remix\logs\remix-dxvk.log'
$renderer = Join-Path $jogo '.trex\d3d9.dll'
$verificacao = '.\analysis\edf\composition\verificacao_' + [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Path $verificacao | Out-Null
& '.\.tools\build-python\Scripts\python.exe' '.\runtime\tools\edf_static_gpu_replay\verify_probe.py' `
  $sessao --runtime-log $log --expected-samples 1 --output (Join-Path $verificacao 'probe.json')
if ($LASTEXITCODE) { throw 'O probe independente não passou.' }
& '.\.tools\build-python\Scripts\python.exe' '.\runtime\tools\edf_static_gpu_replay\verify_composition.py' `
  $sessao --runtime-log $log --probe-verification (Join-Path $verificacao 'probe.json') `
  --runtime $renderer --expected-samples 1 --output (Join-Path $verificacao 'composition.json') `
  --image-output (Join-Path $verificacao 'images')
if ($LASTEXITCODE) { throw 'A composição não passou.' }
```

Replace `session_EXATA` with the exact session containing the single requested
panel. These commands read the installation; they do not deploy or change it.
For an archived log, retain a complete measured test bundle or verify before
archiving; an arbitrary log path does not establish the renderer's provenance.

A passing report proves bounded GPU panel readbacks and queued presentation,
not visible EDF gameplay, occlusion-correct replacement or continuous PT.
Depth/stencil receives no composition commands; its texels are not exported
and therefore the readbacks alone do not compare its original content.

Run synthetic preparation/verifier tests without native GPU execution:

```powershell
& '.\.tools\build-python\Scripts\python.exe' -m unittest discover `
  -s '.\runtime\tools\edf_static_gpu_replay' -p 'test*composition*.py' -v
```
