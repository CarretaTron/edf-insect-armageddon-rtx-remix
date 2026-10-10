# EDF: Insect Armageddon — RTX Remix research and mod

Private checkpoint of the project on 2026-10-10. Start with [PROJECT_STATE.md](PROJECT_STATE.md) before changing or installing anything.

## Saved here

- `runtime/`: full tracked source snapshot of NVIDIA dxvk-remix, including every local modification and the new capture/decoder/composition tools. Base: `0e18e55c04309c08e6206e768bc72b24ac83f853`.
- `analysis/`: investigations, shader disassembly, mathematical validations, reports, GPU verification, screenshots and diagnostic images.
- `profiles/`: bounded instrumentation/probe/composition configurations and manual installation/recovery commands.
- `runtime/_build/`: selected final verification reports and logs. Build caches, DLL dependencies and compiler installations are excluded.
- `upstream/`: tracked changes as a patch, original submodule commit references and provenance.
- `ARTIFACTS.json`: hashes of raw captures, native GPU readbacks and the custom compiled Runtime/Bridge saved as private release assets.

## Current result

One exact static mesh (252 vertices / 144 triangles) has reached the RTX scene and produced verified offscreen GPU output in EDF. The current-frame WSI diagnostic panel passed 18 controlled native GPU cases, 115 Python tests, 15 source contracts and 34 deployment assertions. Its physical appearance inside EDF is still unverified. Global Khronos validation is **not passed**: baseline raster/path-tracing hazards remain even with composition disabled.

The experiment is default OFF and does not provide whole-game compatibility, animated characters, occlusion-correct replacement or proven continuous real-time PT.

## Resume

Clone this repository, download the private `checkpoint-2026-10-10` release assets, check their SHA256 against `ARTIFACTS.json`, and extract them in the repository root. Follow [RESTORE.md](RESTORE.md). Then read `analysis/edf/composition/REPORT.md` and `profiles/edf-composition-preview/TESTAR.md`.

Preserve upstream LFS pointers when cloning this snapshot (the original upstream owns those dependencies):

```powershell
$env:GIT_LFS_SKIP_SMUDGE = '1'
git clone https://github.com/CarretaTron/edf-insect-armageddon-rtx-remix.git
Remove-Item Env:GIT_LFS_SKIP_SMUDGE
```

Evidence JSONs/logs remain byte-preserved and retain original absolute paths under `D:\EDF IA RTX REMIX`; their recorded hashes must not be silently rewritten. Use that original workspace location or explicitly adapt path handling and revalidate. Preserve the active game installation until the manual deployment step.

Upstream licensing remains in `runtime/LICENSE` and individual source headers. Captures contain game-derived research data; keep the repository/releases private. EDF and proprietary NVIDIA runtime dependencies are not supplied or relicensed here.
