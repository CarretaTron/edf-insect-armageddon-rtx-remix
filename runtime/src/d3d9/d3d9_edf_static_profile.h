// NV-DXVK start: Explicit opt-in for one validated EDF static mesh.
#pragma once

#include <cstdint>
#include <string>

#include "../dxvk/rtx_render/rtx_option.h"

namespace dxvk {
  class EdfStaticProfile {
  public:
    static constexpr uint64_t kTraceId = 0x4544465354415449ull;

    RTX_OPTION("rtx.edfStatic", bool, enabled, false, "Enable the experimental decoder for one validated EDF static mesh. All original raster draws are preserved.");
    RTX_OPTION("rtx.edfStatic", bool, rasterOnly, true, "Legacy scene-only option. The validated mesh always preserves the game raster image, including when this option is false. Use the separate offscreen path tracing probe to render this mesh. Full game output replacement is unsupported.");
    RTX_OPTION("rtx.edfStatic", std::string, executable, "EDF-IA.exe", "Executable name allowed to use the static prototype. Matching is case insensitive.");
    RTX_OPTION("rtx.edfStatic", std::string, executableSha1, "d2e90641c560420d279a629ddd86e13cb767f464", "Required SHA1 of the executable allowed to use the static prototype. Shader and mesh allowlists still apply.");

    static bool matchExecutable();
  };
}
// NV-DXVK end
