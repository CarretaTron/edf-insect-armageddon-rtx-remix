/*
* Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
*
* Permission is hereby granted, free of charge, to any person obtaining a
* copy of this software and associated documentation files (the "Software"),
* to deal in the Software without restriction, including without limitation
* the rights to use, copy, modify, merge, publish, distribute, sublicense,
* and/or sell copies of the Software, and to permit persons to whom the
* Software is furnished to do so, subject to the following conditions:
*
* The above copyright notice and this permission notice shall be included in
* all copies or substantial portions of the Software.
*
* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
* IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
* FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
* THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
* LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
* FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
* DEALINGS IN THE SOFTWARE.
*/
#include "rtx/pass/edf_static_composition/edf_static_composition.h"

[[vk::binding(EDF_COMPOSITION_DEPTH)]]
Texture2D<float> LinearZ;
[[vk::binding(EDF_COMPOSITION_COLOR)]]
Texture2D<float4> DisplayColor;
[[vk::binding(EDF_COMPOSITION_BOUNDS)]]
StructuredBuffer<uint> Bounds;
[[vk::binding(EDF_COMPOSITION_OUTPUT)]]
layout(rgba8) RWTexture2D<float4> Panel;
layout(push_constant)
ConstantBuffer<EdfStaticCompositionArgs> cb;

[shader("compute")]
[numthreads(16, 16, 1)]
void main(uint2 threadId : SV_DispatchThreadID) {
  if (threadId.x >= cb.panelWidth || threadId.y >= cb.panelHeight) {
    return;
  }
  float3 result = float3(16.0, 16.0, 20.0) / 255.0;
  const bool border = threadId.x < 2 || threadId.y < 2
    || threadId.x >= cb.panelWidth - 2 || threadId.y >= cb.panelHeight - 2;
  if (border) {
    result = float3(0.0, 220.0, 255.0) / 255.0;
  } else if (Bounds[4] > 0 && Bounds[5] == 0 && Bounds[6] == 0) {
    const uint2 cropMin = uint2(Bounds[0] > 8 ? Bounds[0] - 8 : 0,
      Bounds[1] > 8 ? Bounds[1] - 8 : 0);
    const uint2 cropMax = min(uint2(Bounds[2] + 8, Bounds[3] + 8),
      uint2(cb.sourceWidth - 1, cb.sourceHeight - 1));
    const uint2 cropSize = cropMax - cropMin + 1;
    const uint2 available = uint2(cb.panelWidth - 8, cb.panelHeight - 8);
    const bool widthLimited = available.x * cropSize.y <= available.y * cropSize.x;
    const uint2 fitSize = widthLimited
      ? uint2(available.x, max(1u, cropSize.y * available.x / cropSize.x))
      : uint2(max(1u, cropSize.x * available.y / cropSize.y), available.y);
    const uint2 fitStart = (uint2(cb.panelWidth, cb.panelHeight) - fitSize) / 2;
    if (all(threadId >= fitStart) && all(threadId < fitStart + fitSize)) {
      const uint2 samplePos = cropMin + min(cropSize - 1,
        ((2 * (threadId - fitStart) + 1) * cropSize) / (2 * fitSize));
      const float z = LinearZ.Load(int3(samplePos, 0));
      const float3 color = DisplayColor.Load(int3(samplePos, 0)).xyz;
      if (isfinite(z) && z != cb.missLinearZ && all(isfinite(color))) {
        result = saturate(color);
      }
    }
  }
  Panel[threadId] = float4(result, 1.0);
}
